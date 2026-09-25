from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from student_agent.contracts import Contracts

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "schemas"
_spec = importlib.util.spec_from_file_location(
    "check_contracts", ROOT / "scripts" / "check_contracts.py"
)
assert _spec and _spec.loader
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)

REAL = "af0bbb47f125381ce9f3597dc70ef07b"
DECOY = "candidate-001"
REF = "ev_" + "a" * 24

CASE: dict[str, Any] = {
    "case_id": "L3B_CASE_001",
    "opened_at": "2018-01-01T09:00:00-03:00",
    "customer_request": {
        "language": "vi",
        "message": "test",
        "claimed_order_id": REAL,
        "claims": [
            {"claim_id": "claim-001-a", "topic": "late_delivery_logistics"},
            {"claim_id": "claim-001-b", "topic": "requested_full_refund"},
        ],
    },
    "policy_version": "EC_POLICY_V2",
    "candidate_order_ids": [REAL, DECOY],
    "investigation_scope": {"include_customer_history": True},
    "customer_unique_id_hint": "customer-597dc70ef07b",
}

OUTPUT: dict[str, Any] = {
    "schema_version": "day09-l3b-output-v2",
    "case_id": "L3B_CASE_001",
    "assessment": {
        "primary_issue": "late_delivery_logistics",
        "secondary_issues": [],
        "case_status": "action_required",
        "confidence": 0.8,
    },
    "affected_entities": {
        "order_ids": [REAL],
        "item_ids": [],
        "seller_ids": ["seller-1"],
        "payment_references": [],
        "shipment_ids": [],
    },
    "claim_assessments": [
        {
            "claim_id": "claim-001-a",
            "verdict": "supported",
            "confidence": 0.8,
            "evidence_refs": [REF],
        },
        {
            "claim_id": "claim-001-b",
            "verdict": "partially_supported",
            "confidence": 0.6,
            "evidence_refs": [REF],
        },
    ],
    "entity_resolution": {
        "status": "resolved",
        "resolved_order_ids": [REAL],
        "rejected_candidates": [DECOY],
        "confidence": 0.9,
    },
    "customer_context": {"customer_unique_id": None, "related_order_ids": []},
    "shipment_analysis": {
        "verdict": "logistics_delay",
        "late_seller_ids": [],
        "timeline_complete": True,
    },
    "payment_analysis": {
        "verdict": "reconciled",
        "captured_total_brl": 100.0,
        "refunded_total_brl": 0.0,
        "refundable_total_brl": 100.0,
    },
    "root_cause_analysis": {
        "ranked_causes": [{"cause_code": "CARRIER_DELAY", "rank": 1}],
        "responsible_parties": [{"party_type": "logistics_provider", "party_id": None}],
    },
    "evidence_refs": [REF],
    "data_conflicts": [],
    "financial_resolution": {
        "currency": "BRL",
        "recommended_refund_brl": 10.0,
        "refund_lines": [{"reason_code": "LATE_DELIVERY", "amount_brl": 10.0, "entity_id": REAL}],
    },
    "resolution_actions": ["refund_freight"],
}


def event(event_type: str, actor: str, **extra: Any) -> dict[str, Any]:
    return {"event_type": event_type, "actor": actor, **extra}


TRACE = [
    event("case_received", "coordinator"),
    event("task_assigned", "coordinator", target="entity-agent"),
    event("tool_result_consumed", "entity-agent", evidence_refs=[REF]),
    event("handoff", "entity-agent", target="verifier"),
    event("verification_completed", "verifier"),
    event("case_finalized", "coordinator"),
]


def test_sample_output_passes_schema_and_checks() -> None:
    Contracts(SCHEMAS).validate_output(OUTPUT, "sample")
    assert checks.check_output(CASE, OUTPUT, TRACE) == []
    assert checks.check_trace(TRACE) == []


def test_sample_input_passes() -> None:
    errors, warnings = checks.check_input(CASE, checks.known_topics(SCHEMAS))
    assert errors == []
    assert warnings == []
    assert checks.case_type(CASE) == "late_delivery_logistics"


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("financial_resolution", "recommended_refund_brl"), 50.0, "refund_lines"),
        (("shipment_analysis", "verdict"), "seller_delay", "late_seller_ids"),
        (("entity_resolution", "rejected_candidates"), [REAL], "vừa resolved vừa rejected"),
        (("assessment", "case_status"), "no_action", "no_action"),
        (("evidence_refs",), [], "missing_required_evidence"),
    ],
)
def test_inconsistent_output_is_reported(path: tuple[str, ...], value: Any, expected: str) -> None:
    out = copy.deepcopy(OUTPUT)
    target = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert any(expected in error for error in checks.check_output(CASE, out, TRACE))


def test_untraced_evidence_is_reported() -> None:
    trace = [e for e in TRACE if e["event_type"] != "tool_result_consumed"]
    assert any("tool_result_consumed" in e for e in checks.check_output(CASE, OUTPUT, trace))


def test_trace_order_and_required_events() -> None:
    errors = checks.check_trace(list(reversed(TRACE[:3])))
    assert any("thiếu event" in e for e in errors)
    assert any("case_received" in e for e in errors)
    assert any("case_finalized" in e for e in errors)


def test_input_with_bad_claims_is_reported() -> None:
    case = copy.deepcopy(CASE)
    case["customer_request"]["claims"].append({"claim_id": "claim-001-a", "topic": "x"})
    case["customer_request"]["claimed_order_id"] = "not-a-candidate"
    errors, warnings = checks.check_input(case, checks.known_topics(SCHEMAS))
    assert any("trùng" in e for e in errors)
    assert any("topic lạ" in w for w in warnings)
    assert any("claimed_order_id" in w for w in warnings)
