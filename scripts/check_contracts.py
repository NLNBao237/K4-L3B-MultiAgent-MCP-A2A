"""Kiểm tra input, output và trace vượt mức JSON Schema.

`day09 validate` chỉ kiểm tra schema. Script này bổ sung các rule suy ra từ
contracts/scoring/scoring-policy-v2.json: hard gates, consistency và workflow.

Chạy:
    python scripts/check_contracts.py inputs    # trước khi chạy agent
    python scripts/check_contracts.py outputs   # sau `day09 run`
    python scripts/check_contracts.py           # cả hai
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from student_agent.cases import CaseSet, load_case_set
from student_agent.contracts import ContractError, Contracts

REQUIRED_EVENTS = {
    "case_received",
    "task_assigned",
    "handoff",
    "verification_completed",
    "case_finalized",
}
KNOWN_INPUT_KEYS = {
    "case_id",
    "opened_at",
    "customer_request",
    "policy_version",
    "candidate_order_ids",
    "investigation_scope",
    "customer_unique_id_hint",
}
EXTRA_CLAIM_TOPICS = {"requested_full_refund"}
MIN_ACTORS = 3
EPS = 0.01


def known_topics(schema_root: Path) -> set[str]:
    schema = json.loads((schema_root / "l3a-output-v2.schema.json").read_text(encoding="utf-8"))
    return set(schema["$defs"]["primaryIssue"]["enum"]) | EXTRA_CLAIM_TOPICS


def case_type(case: dict[str, Any]) -> str:
    claims = case.get("customer_request", {}).get("claims") or []
    return claims[0].get("topic", "unknown") if claims else "unknown"


def check_input(case: dict[str, Any], topics: set[str]) -> tuple[list[str], list[str]]:
    """Trả về (errors, warnings) cho một input case."""
    errors: list[str] = []
    warnings: list[str] = []
    extra = set(case) - KNOWN_INPUT_KEYS
    if extra:
        warnings.append(f"field lạ trong input: {sorted(extra)}")
    for key in ("policy_version", "customer_request", "candidate_order_ids"):
        if key not in case:
            errors.append(f"thiếu field {key}")

    request = case.get("customer_request") or {}
    claims = request.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("customer_request.claims rỗng hoặc không phải mảng")
        claims = []
    claim_ids = [claim.get("claim_id") for claim in claims]
    if len(claim_ids) != len(set(claim_ids)) or None in claim_ids:
        errors.append("claim_id bị thiếu hoặc trùng")
    for claim in claims:
        if claim.get("topic") not in topics:
            warnings.append(f"topic lạ: {claim.get('topic')!r}")

    candidates = case.get("candidate_order_ids")
    if not isinstance(candidates, list) or not all(isinstance(c, str) for c in candidates):
        errors.append("candidate_order_ids phải là mảng string")
        candidates = []
    if len(candidates) != len(set(candidates)):
        errors.append("candidate_order_ids bị trùng")
    if not candidates:
        warnings.append("không có candidate -> entity_resolution có thể là not_found")
    claimed = request.get("claimed_order_id")
    if claimed is None:
        warnings.append("không có claimed_order_id -> bắt buộc resolve bằng evidence")
    elif claimed not in candidates:
        warnings.append("claimed_order_id không nằm trong candidate_order_ids")
    if not case.get("customer_unique_id_hint"):
        warnings.append("không có customer_unique_id_hint")
    return errors, warnings


def check_trace(events: list[dict[str, Any]]) -> list[str]:
    if not events:
        return ["không có trace event nào"]
    errors: list[str] = []
    types = [event["event_type"] for event in events]
    missing = REQUIRED_EVENTS - set(types)
    if missing:
        errors.append(f"trace thiếu event: {sorted(missing)}")
    if types[0] != "case_received":
        errors.append("event đầu tiên phải là case_received")
    if types[-1] != "case_finalized":
        errors.append("event cuối cùng phải là case_finalized")
    if types.count("case_received") > 1 or types.count("case_finalized") > 1:
        errors.append("case_received/case_finalized bị emit nhiều lần")
    if len({event["actor"] for event in events}) < MIN_ACTORS:
        errors.append(f"ít hơn {MIN_ACTORS} actor khác nhau -> điểm workflow thấp")
    return errors


def check_output(
    case: dict[str, Any], out: dict[str, Any], events: list[dict[str, Any]]
) -> list[str]:
    """Rule consistency/hard gate cho một output đã pass schema."""
    errors: list[str] = []
    candidates = set(case.get("candidate_order_ids") or [])

    # Evidence: phải có và phải được trace bằng tool_result_consumed của đúng case.
    traced = {
        ref
        for event in events
        if event["event_type"] == "tool_result_consumed"
        for ref in event.get("evidence_refs", [])
    }
    top_refs = set(out["evidence_refs"])
    claim_refs = {
        ref for claim in out.get("claim_assessments", []) for ref in claim["evidence_refs"]
    }
    if not top_refs:
        errors.append("evidence_refs rỗng -> hard gate missing_required_evidence")
    untraced = (top_refs | claim_refs) - traced
    if untraced:
        errors.append(f"{len(untraced)} evidence_ref không có trong tool_result_consumed")
    if claim_refs - top_refs:
        errors.append("claim_assessments dùng evidence không có trong evidence_refs")

    # Entity resolution.
    resolution = out["entity_resolution"]
    resolved = set(resolution["resolved_order_ids"])
    rejected = set(resolution["rejected_candidates"])
    if resolution["status"] == "resolved" and not resolved:
        errors.append("status=resolved nhưng resolved_order_ids rỗng")
    if resolution["status"] == "not_found" and resolved:
        errors.append("status=not_found nhưng vẫn có resolved_order_ids")
    if resolved & rejected:
        errors.append("một order vừa resolved vừa rejected")
    if not (resolved | rejected) <= candidates:
        errors.append("resolved/rejected chứa order ngoài candidate_order_ids")
    if not set(out["affected_entities"]["order_ids"]) <= resolved:
        errors.append("affected_entities.order_ids chứa order chưa resolved")

    # Claims phải khớp 1-1 với input.
    input_claims = {claim["claim_id"] for claim in case["customer_request"]["claims"]}
    output_claims = [claim["claim_id"] for claim in out.get("claim_assessments", [])]
    if set(output_claims) != input_claims or len(output_claims) != len(set(output_claims)):
        errors.append(f"claim_assessments không khớp claims input {sorted(input_claims)}")

    # Shipment và trách nhiệm seller.
    shipment = out["shipment_analysis"]
    late = set(shipment["late_seller_ids"])
    parties = out["root_cause_analysis"]["responsible_parties"]
    if shipment["verdict"] == "seller_delay":
        if not late:
            errors.append("verdict=seller_delay nhưng late_seller_ids rỗng")
        if not any(party["party_type"] == "seller" for party in parties):
            errors.append("seller_delay nhưng responsible_parties không có seller")
    elif late:
        errors.append("có late_seller_ids nhưng verdict khác seller_delay")
    if not late <= set(out["affected_entities"]["seller_ids"]):
        errors.append("late_seller_ids không nằm trong affected_entities.seller_ids")

    # Payment và refund.
    payment = out["payment_analysis"]
    financial = out["financial_resolution"]
    captured = payment["captured_total_brl"]
    refunded = payment["refunded_total_brl"]
    refundable = payment["refundable_total_brl"]
    recommended = financial["recommended_refund_brl"]
    if None not in (captured, refunded, refundable) and refundable > captured - refunded + EPS:
        errors.append("refundable_total > captured - refunded")
    if abs(sum(line["amount_brl"] for line in financial["refund_lines"]) - recommended) > EPS:
        errors.append("tổng refund_lines != recommended_refund_brl")
    if refundable is not None and recommended > refundable + EPS:
        errors.append("recommended_refund_brl > refundable_total_brl")
    if payment["verdict"] == "insufficient_evidence" and recommended > 0:
        errors.append("payment insufficient_evidence nhưng vẫn đề xuất refund")

    # Status và action.
    status = out["assessment"]["case_status"]
    if status == "no_action" and (recommended > 0 or out["resolution_actions"]):
        errors.append("no_action nhưng vẫn có refund/action")
    if status == "action_required" and not out["resolution_actions"]:
        errors.append("action_required nhưng resolution_actions rỗng")

    ranks = [cause["rank"] for cause in out["root_cause_analysis"]["ranked_causes"]]
    if len(ranks) != len(set(ranks)):
        errors.append("ranked_causes có rank trùng")
    return errors


def load_trace(path: Path) -> dict[str, list[dict[str, Any]]]:
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            event = json.loads(line)
            by_case[event["case_id"]].append(event)
    return by_case


def report(case_id: str, errors: list[str], warnings: list[str] | None = None) -> None:
    if errors:
        print(f"[FAIL] {case_id}")
    elif warnings:
        print(f"[WARN] {case_id}")
    for error in errors:
        print(f"    - {error}")
    for warning in warnings or []:
        print(f"    ~ {warning}")


def run_inputs(case_set: CaseSet, schema_root: Path) -> int:
    topics = known_topics(schema_root)
    failed = 0
    for case_id in case_set.case_ids:
        errors, warnings = check_input(case_set.cases[case_id], topics)
        failed += bool(errors)
        report(case_id, errors, warnings)
    types = Counter(case_type(case) for case in case_set.cases.values())
    print("\nPhân bố case type (topic của claim đầu tiên):")
    for topic, count in sorted(types.items()):
        print(f"    {topic:<28} {count}")
    print(f"inputs: {len(case_set.case_ids) - failed}/{len(case_set.case_ids)} case OK")
    return failed


def run_outputs(root: Path, case_set: CaseSet, contracts: Contracts) -> int:
    trace_path = root / "traces" / "trace.jsonl"
    if not trace_path.exists():
        print("ERROR: thiếu traces/trace.jsonl, hãy chạy `day09 run` trước")
        return len(case_set.case_ids)
    trace = load_trace(trace_path)
    ref_owner: dict[str, str] = {}
    failed = 0
    for case_id in case_set.case_ids:
        errors: list[str] = []
        path = root / "outputs" / f"{case_id}.json"
        if not path.exists():
            errors.append("thiếu file output")
        else:
            out = json.loads(path.read_text(encoding="utf-8"))
            try:
                contracts.validate_output(out, f"outputs/{case_id}.json")
            except ContractError as exc:
                errors.append(f"schema: {exc}")
            else:
                if out["case_id"] != case_id:
                    errors.append("case_id mismatch -> hard gate")
                errors += check_output(case_set.cases[case_id], out, trace.get(case_id, []))
                for ref in out["evidence_refs"]:
                    owner = ref_owner.setdefault(ref, case_id)
                    if owner != case_id:
                        errors.append(f"evidence {ref[:16]}... dùng chung với {owner}")
        errors += check_trace(trace.get(case_id, []))
        failed += bool(errors)
        report(case_id, errors)
    print(f"outputs: {len(case_set.case_ids) - failed}/{len(case_set.case_ids)} case OK")
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", nargs="?", choices=["inputs", "outputs", "all"], default="all")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    schema_root = root / "contracts" / "schemas"
    case_set = load_case_set(root)
    failed = 0
    if args.target in ("inputs", "all"):
        failed += run_inputs(case_set, schema_root)
    if args.target in ("outputs", "all"):
        failed += run_outputs(root, case_set, Contracts(schema_root))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
