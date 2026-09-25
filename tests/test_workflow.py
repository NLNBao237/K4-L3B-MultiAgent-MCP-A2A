from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

import pytest

from student_agent.analysis import select_timeline
from student_agent.contracts import Contracts
from student_agent.mcp_gateway import ToolCallError
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case

ROOT = Path(__file__).resolve().parents[1]
ORDER_ID = "0123456789abcdef0123456789abcdef"


def rule(status: str, action: str, refund: float, party: str, party_id: str | None = None):
    return {
        "case_status": status,
        "recommended_action": action,
        "refund_brl": refund,
        "responsible_parties": [{"party_id": party_id, "party_type": party}],
    }


POLICY = {
    "currency": "BRL",
    "policy_version": "TEST_POLICY",
    "rules": {
        "canceled_order_paid": rule("action_required", "issue_refund", 79.0, "platform"),
        "late_delivery_seller": rule(
            "action_required", "refund_freight", 18.0, "seller", "seller-from-template"
        ),
        "valid_split_payment": rule("no_action", "document_no_action", 0.0, "customer"),
        "refund_failed": rule("action_required", "retry_refund", 52.0, "payment_provider"),
        "unsupported_claim": rule("no_action", "document_no_action", 0.0, "customer"),
    },
}


def order_row(purchase: str, status: str, carrier: str | None, delivered: str | None,
              estimated: str) -> dict[str, Any]:
    return {
        "order_id": ORDER_ID, "customer_id": "customer-row-test", "order_status": status,
        "order_purchase_timestamp": purchase, "order_approved_at": purchase,
        "order_delivered_carrier_date": carrier, "order_delivered_customer_date": delivered,
        "order_estimated_delivery_date": estimated,
    }


def payment_timeline(events: list[dict[str, Any]]) -> dict[str, Any]:
    return evidence("payment", {"order_id": ORDER_ID, "payments": [], "events": events})


def evidence(domain: str, data: Any) -> dict[str, Any]:
    return {
        "schema_version": "day09-mcp-evidence-v1",
        "evidence_ref": f"ev_{secrets.token_urlsafe(24)}",
        "result_hash": "sha256:" + "0" * 64,
        "domain": domain,
        "data": data,
        "warnings": [],
    }


class FakeGateway:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        self.calls.append(tool_name)
        if tool_name == "get_order" and arguments.get("order_id") != ORDER_ID:
            raise ToolCallError("not found")
        response = self.responses.get(tool_name)
        if response is None:
            raise ToolCallError("no record")
        return response


def make_case(topic: str) -> dict[str, Any]:
    return {
        "case_id": "TEST_CASE_001",
        "opened_at": "2018-03-12T09:00:00-03:00",
        "customer_request": {
            "language": "vi", "message": "test", "claimed_order_id": ORDER_ID,
            "claims": [
                {"claim_id": "claim-a", "topic": topic},
                {"claim_id": "claim-b", "topic": "requested_full_refund"},
            ],
        },
        "policy_version": "TEST_POLICY",
        "candidate_order_ids": [ORDER_ID, "candidate-001"],
        "investigation_scope": {
            "include_customer_history": True, "include_product_context": True,
            "require_independent_verification": True,
        },
        "customer_unique_id_hint": "customer-test",
    }


def item(limit: str, freight: str) -> dict[str, Any]:
    return {
        "order_id": ORDER_ID, "order_item_id": "item-test", "product_id": "product-test",
        "seller_id": "seller-test", "shipping_limit_date": limit, "price": "79.00",
        "freight_value": freight,
    }


def run_case(case: dict[str, Any], responses: dict[str, Any], tmp_path: Path):
    contracts = Contracts(ROOT / "contracts" / "schemas")
    trace_path = tmp_path / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)
    gateway = FakeGateway(responses)
    output = asyncio.run(solve_case(case, gateway, trace))  # type: ignore[arg-type]
    contracts.validate_output(output, "output")
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    return output, events, gateway


def late_seller_responses() -> dict[str, Any]:
    # Version 1 is a distractor purchased after the complaint; version 2 is late by the seller.
    decoy = order_row("2018-05-02T09:00:00-03:00", "canceled", "2018-05-04T09:00:00-03:00",
                      None, "2018-05-12T09:00:00-03:00")
    real = order_row("2018-02-28T09:00:00-03:00", "delivered", "2018-03-06T09:00:00-03:00",
                     "2018-03-13T09:00:00-03:00", "2018-03-10T09:00:00-03:00")
    return {
        "get_customer_history": evidence(
            "customer", {"customer_unique_id": "customer-test", "orders": [decoy, real]}
        ),
        "get_order": evidence("order", decoy),
        "get_order_items": evidence("item", [
            item("2018-05-05T09:00:00-03:00", "10.00"),
            item("2018-03-03T09:00:00-03:00", "18.00"),
        ]),
        "get_product_context": evidence("product", [{"product_id": "product-test"}]),
        "get_policy": evidence("policy", POLICY),
        "get_payment_timeline": payment_timeline([
            {"event_at": "2018-05-02T10:00:00-03:00", "event_type": "captured",
             "amount_brl": "79.00", "status": "confirmed"},
            {"event_at": "2018-02-28T10:00:00-03:00", "event_type": "captured",
             "amount_brl": "18.00", "status": "confirmed"},
        ]),
        "get_shipment_summary": evidence("shipment", {
            "order_id": ORDER_ID, "shipping_limits": [], "events": [
                {"event_at": "2018-03-13T09:00:00-03:00", "event_type": "delivered_late",
                 "actor": "seller", "status": "confirmed"},
            ]}),
        "get_sellers": evidence("seller", [{"seller_id": "seller-test"}]),
    }


def test_select_timeline_ignores_versions_whose_promise_had_not_matured() -> None:
    rows = [
        order_row("2018-03-09T09:00:00-03:00", "delivered", None, None,
                  "2018-03-19T09:00:00-03:00"),
        order_row("2018-02-28T09:00:00-03:00", "canceled", None, None,
                  "2018-03-10T09:00:00-03:00"),
    ]
    selection = select_timeline(rows, "2018-03-12T09:00:00-03:00")
    assert selection.selected == 1
    assert not selection.ambiguous


def test_late_seller_case_uses_authoritative_timeline(tmp_path: Path) -> None:
    output, events, gateway = run_case(
        make_case("late_delivery_seller"), late_seller_responses(), tmp_path
    )
    assert output["assessment"]["primary_issue"] == "late_delivery_seller"
    assert output["shipment_analysis"] == {
        "verdict": "seller_delay", "late_seller_ids": ["seller-test"], "timeline_complete": True,
    }
    assert output["financial_resolution"]["recommended_refund_brl"] == 18.0
    assert output["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "seller", "party_id": "seller-test"}
    ]
    assert output["entity_resolution"]["rejected_candidates"] == ["candidate-001"]
    assert "get_order" in gateway.calls and gateway.calls.count("get_order") == 1
    # Every output evidence ref is linked to a consumed tool result in the trace.
    consumed = {
        ref for e in events if e["event_type"] == "tool_result_consumed"
        for ref in e.get("evidence_refs", [])
    }
    assert set(output["evidence_refs"]) <= consumed
    kinds = {e["event_type"] for e in events}
    assert {"task_assigned", "handoff", "verification_completed", "policy_decided"} <= kinds
    assert "case_received" not in kinds  # emitted once by the CLI, not by the solver


def test_claim_is_not_trusted_over_evidence(tmp_path: Path) -> None:
    output, _, _ = run_case(make_case("canceled_order_paid"), late_seller_responses(), tmp_path)
    assert output["assessment"]["primary_issue"] == "late_delivery_seller"
    verdicts = {c["claim_id"]: c["verdict"] for c in output["claim_assessments"]}
    assert verdicts["claim-a"] == "unsupported"
    assert verdicts["claim-b"] == "partially_supported"


@pytest.mark.parametrize("topic,expected", [
    ("valid_split_payment", "valid_split_payment"),
    ("refund_failed", "refund_failed"),
])
def test_same_timestamp_versions_use_verified_claim(
    tmp_path: Path, topic: str, expected: str
) -> None:
    row = order_row("2018-02-28T09:00:00-03:00", "delivered", "2018-03-02T09:00:00-03:00",
                    "2018-03-09T09:00:00-03:00", "2018-03-10T09:00:00-03:00")
    responses = {
        "get_customer_history": evidence(
            "customer", {"customer_unique_id": "customer-test", "orders": [row, dict(row)]}
        ),
        "get_order": evidence("order", row),
        "get_order_items": evidence("item", [item("2018-03-03T09:00:00-03:00", "10.00")] * 2),
        "get_policy": evidence("policy", POLICY),
        "get_payment_timeline": payment_timeline([
            {"event_at": "2018-02-28T10:00:00-03:00", "event_type": "captured",
             "amount_brl": amount, "status": "confirmed"}
            for amount in ("52.00", "44.50", "44.50")
        ]),
        "get_refund_timeline": evidence("refund", {"order_id": ORDER_ID, "events": [
            {"event_at": "2018-03-11T09:00:00-03:00", "event_type": "refund_requested",
             "amount_brl": "52.00", "status": "failed"},
        ]}),
    }
    output, _, _ = run_case(make_case(topic), responses, tmp_path)
    assert output["assessment"]["primary_issue"] == expected
    captured = output["payment_analysis"]["captured_total_brl"]
    assert captured == (89.0 if expected == "valid_split_payment" else 52.0)
