"""L3B multi-agent workflow: a coordinator dispatching specialist agents over A2A messages.

Flow for one case (every arrow is an A2A message recorded as a ``handoff`` trace event):

    coordinator -> entity-agent      customer history, candidate resolution, customer context
    coordinator -> order-agent       order row, items/sellers, product context   (parallel)
    coordinator -> payment-agent     payment timeline, refund timeline if needed (parallel)
    coordinator -> policy-agent      machine-readable policy                     (parallel)
    coordinator -> shipment-agent    shipment summary when the timeline needs it
    policy-agent -> conflict-agent   issue decision + policy rule
    conflict-agent -> verifier       reconciled sources
    verifier -> coordinator          independently re-checked output

Only the gateway supplies evidence; refs are never invented and every ref placed in the
output was consumed (and traced) within this case.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any

from . import OUTPUT_SCHEMA_VERSION
from .analysis import (
    PAYMENT_VERDICTS,
    Classification,
    PaymentFacts,
    ShipmentFacts,
    TimelineSelection,
    classify,
    issue_signals,
    order_items_total,
    parse_ts,
    payment_facts,
    policy_rule,
    responsible_parties,
    select_timeline,
    shipment_facts,
    to_amount,
    unique,
)
from .mcp_gateway import EvidenceGateway, ToolCallError
from .trace import TraceWriter

COORDINATOR = "coordinator"
ENTITY_AGENT = "entity-agent"
ORDER_AGENT = "order-agent"
PAYMENT_AGENT = "payment-agent"
SHIPMENT_AGENT = "shipment-agent"
POLICY_AGENT = "policy-agent"
CONFLICT_AGENT = "conflict-agent"
VERIFIER = "verifier"

CALL_TIMEOUT_SECONDS = 120.0
MAX_TOOL_CALLS_PER_CASE = 12
# Product/category data never changes a decision; citing it costs a call and evidence precision.
USE_PRODUCT_CONTEXT = False
REFUND_TOPICS = {"refund_pending", "refund_failed"}
LATE_TOPICS = {"late_delivery_seller", "late_delivery_logistics"}
# Policy actions that return everything the customer paid (vs. freight/difference only).
FULL_REFUND_ACTIONS = {"issue_refund", "retry_refund"}


class CaseAborted(RuntimeError):
    """The transport failed mid-case; the CLI reconnects and re-runs the case."""


# ---------------------------------------------------------------------------
# A2A envelope and shared case state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class A2AMessage:
    message_id: str
    case_id: str
    sender: str
    recipient: str
    task_type: str
    correlation_id: str
    payload: dict[str, Any]


@dataclass
class Evidence:
    tool: str
    ref: str
    domain: str
    data: Any
    actor: str


@dataclass
class CaseContext:
    case: dict[str, Any]
    gateway: EvidenceGateway
    trace: TraceWriter
    evidence: list[Evidence] = field(default_factory=list)
    failed_tools: list[str] = field(default_factory=list)
    messages: list[A2AMessage] = field(default_factory=list)
    _cache: dict[tuple[str, tuple[tuple[str, str], ...]], Evidence | None] = field(
        default_factory=dict
    )
    _sequence: itertools.count = field(default_factory=lambda: itertools.count(1))
    calls: int = 0

    @property
    def case_id(self) -> str:
        return self.case["case_id"]

    # -- A2A -------------------------------------------------------------------------------

    def assign(self, recipient: str, task_type: str, **attributes: Any) -> str:
        correlation_id = f"{self.case_id}:{task_type}"
        self.trace.emit(
            case_id=self.case_id,
            event_type="task_assigned",
            actor=COORDINATOR,
            target=recipient,
            decision_code=task_type,
            attributes={"correlation_id": correlation_id, **_scalar(attributes)},
        )
        return correlation_id

    def send(
        self,
        sender: str,
        recipient: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> A2AMessage:
        message = A2AMessage(
            message_id=f"msg-{self.case_id}-{next(self._sequence):03d}",
            case_id=self.case_id,
            sender=sender,
            recipient=recipient,
            task_type=task_type,
            correlation_id=f"{self.case_id}:{task_type}",
            payload=payload or {},
        )
        self.messages.append(message)
        self.trace.emit(
            case_id=self.case_id,
            event_type="handoff",
            actor=sender,
            target=recipient,
            decision_code=task_type,
            evidence_refs=unique(evidence_refs or [])[:20] or None,
            attributes={
                "message_id": message.message_id,
                "correlation_id": message.correlation_id,
                **_scalar(message.payload),
            },
        )
        return message

    # -- MCP ---------------------------------------------------------------------------------

    async def call(self, actor: str, tool: str, purpose: str, **arguments: str) -> Evidence | None:
        """Call one MCP tool once per case (cached), trace the consumed evidence."""
        key = (tool, tuple(sorted(arguments.items())))
        if key in self._cache:
            return self._cache[key]
        if self.calls >= MAX_TOOL_CALLS_PER_CASE:
            return None
        self.calls += 1
        try:
            response = await asyncio.wait_for(
                self.gateway.call(tool, case_id=self.case_id, **arguments),
                timeout=CALL_TIMEOUT_SECONDS,
            )
        except ToolCallError:
            # Deterministic "no record" answers are not retried.
            self._cache[key] = None
            self.failed_tools.append(tool)
            self.trace.emit(
                case_id=self.case_id,
                event_type="tool_result_consumed",
                actor=actor,
                tool_name=tool,
                decision_code="NO_RECORD",
                attributes={"purpose": purpose},
            )
            return None
        except (TimeoutError, OSError) as exc:
            raise CaseAborted(f"{tool}: {type(exc).__name__}") from exc
        evidence = Evidence(
            tool=tool,
            ref=response["evidence_ref"],
            domain=response["domain"],
            data=response["data"],
            actor=actor,
        )
        self._cache[key] = evidence
        self.evidence.append(evidence)
        self.trace.emit(
            case_id=self.case_id,
            event_type="tool_result_consumed",
            actor=actor,
            tool_name=tool,
            evidence_refs=[evidence.ref],
            attributes={"purpose": purpose, "domain": evidence.domain},
        )
        return evidence

    def refs(self, *tools: str) -> list[str]:
        return unique(e.ref for e in self.evidence if not tools or e.tool in tools)


def _scalar(values: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    result: dict[str, str | int | float | bool | None] = {}
    for key, value in list(values.items())[:16]:
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(v) for v in value)[:200]
        elif isinstance(value, dict):
            continue
        elif isinstance(value, str):
            value = value[:200]
        result[key] = value
    return result


# ---------------------------------------------------------------------------
# Specialist agents
# ---------------------------------------------------------------------------


@dataclass
class EntityFindings:
    order_id: str | None
    rejected: list[str]
    customer_unique_id: str | None
    related_order_ids: list[str]
    history_rows: list[dict[str, Any]]
    status: str
    confidence: float


async def entity_agent(ctx: CaseContext) -> EntityFindings:
    """Resolve the complained order from candidates using the customer's own history."""
    case = ctx.case
    request = case.get("customer_request") or {}
    claimed = request.get("claimed_order_id")
    candidates = unique([claimed, *(case.get("candidate_order_ids") or [])])
    hint = case.get("customer_unique_id_hint")
    ctx.assign(ENTITY_AGENT, "resolve_entity", candidates=len(candidates))

    rows: list[dict[str, Any]] = []
    customer_id = None
    scope = case.get("investigation_scope") or {}
    if hint and scope.get("include_customer_history", True):
        history = await ctx.call(
            ENTITY_AGENT, "get_customer_history", "entity_resolution", customer_unique_id=hint
        )
        if history and isinstance(history.data, dict):
            rows = [r for r in history.data.get("orders") or [] if isinstance(r, dict)]
            customer_id = history.data.get("customer_unique_id") or hint

    owned = unique(r.get("order_id") for r in rows)
    matched = [c for c in candidates if c in owned]
    if matched:
        # Prefer the explicitly claimed order when it belongs to the customer.
        order_id = claimed if claimed in matched else matched[0]
        status = "resolved" if len(matched) == 1 or claimed in matched else "ambiguous"
        confidence = 0.95 if status == "resolved" else 0.6
    else:
        order_id, status, confidence = None, "not_found", 0.3
        # No usable history: verify candidates directly (only well-formed order ids).
        for candidate in candidates:
            if not _looks_like_order_id(candidate):
                continue
            order = await ctx.call(
                ENTITY_AGENT, "get_order", "candidate_verification", order_id=candidate
            )
            if order and isinstance(order.data, dict):
                order_id, status, confidence = candidate, "resolved", 0.75
                rows = [order.data]
                break
    rejected = [c for c in candidates if c != order_id]
    findings = EntityFindings(
        order_id=order_id,
        rejected=rejected,
        customer_unique_id=customer_id,
        related_order_ids=owned or ([order_id] if order_id else []),
        history_rows=rows,
        status=status,
        confidence=confidence,
    )
    ctx.send(
        ENTITY_AGENT,
        COORDINATOR,
        "entity_resolved",
        {"status": status, "order_id": order_id, "rejected": rejected},
        ctx.refs("get_customer_history", "get_order"),
    )
    return findings


def _looks_like_order_id(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(
        c in "0123456789abcdef" for c in value
    )


@dataclass
class OrderFindings:
    order_row: dict[str, Any] | None
    items: list[dict[str, Any]]
    item_ids: list[str]
    seller_ids: list[str]
    products: list[dict[str, Any]]


async def order_agent(ctx: CaseContext, order_id: str) -> OrderFindings:
    ctx.assign(ORDER_AGENT, "analyze_order", order_id=order_id)
    scope = ctx.case.get("investigation_scope") or {}
    calls = [
        ctx.call(ORDER_AGENT, "get_order", "order_verification", order_id=order_id),
        ctx.call(ORDER_AGENT, "get_order_items", "affected_entities", order_id=order_id),
    ]
    if USE_PRODUCT_CONTEXT and scope.get("include_product_context", False):
        calls.append(
            ctx.call(ORDER_AGENT, "get_product_context", "product_context", order_id=order_id)
        )
    results = await asyncio.gather(*calls)
    order, items_ev = results[0], results[1]
    products_ev = results[2] if len(results) > 2 else None
    items = [i for i in (items_ev.data if items_ev else []) or [] if isinstance(i, dict)]
    findings = OrderFindings(
        order_row=order.data if order and isinstance(order.data, dict) else None,
        items=items,
        item_ids=unique(i.get("order_item_id") for i in items),
        seller_ids=unique(i.get("seller_id") for i in items),
        products=[
            p for p in (products_ev.data if products_ev else []) or [] if isinstance(p, dict)
        ],
    )
    ctx.send(
        ORDER_AGENT,
        COORDINATOR,
        "order_analyzed",
        {"items": findings.item_ids, "sellers": findings.seller_ids},
        ctx.refs("get_order", "get_order_items", "get_product_context"),
    )
    return findings


async def payment_agent(ctx: CaseContext, order_id: str) -> dict[str, Any] | None:
    ctx.assign(PAYMENT_AGENT, "analyze_payment", order_id=order_id)
    timeline = await ctx.call(
        PAYMENT_AGENT, "get_payment_timeline", "payment_lifecycle", order_id=order_id
    )
    data = timeline.data if timeline and isinstance(timeline.data, dict) else None
    ctx.send(
        PAYMENT_AGENT,
        COORDINATOR,
        "payment_timeline_collected",
        {"events": len((data or {}).get("events") or [])},
        ctx.refs("get_payment_timeline"),
    )
    return data


async def refund_agent_step(ctx: CaseContext, order_id: str, reason: str) -> dict[str, Any] | None:
    ctx.assign(PAYMENT_AGENT, "analyze_refund", order_id=order_id, reason=reason)
    refund = await ctx.call(
        PAYMENT_AGENT, "get_refund_timeline", "refund_lifecycle", order_id=order_id
    )
    data = refund.data if refund and isinstance(refund.data, dict) else None
    ctx.send(
        PAYMENT_AGENT,
        COORDINATOR,
        "refund_timeline_collected",
        {"found": data is not None},
        ctx.refs("get_refund_timeline"),
    )
    return data


async def shipment_agent(ctx: CaseContext, order_id: str, reason: str) -> dict[str, Any] | None:
    ctx.assign(SHIPMENT_AGENT, "analyze_shipment", order_id=order_id, reason=reason)
    summary = await ctx.call(
        SHIPMENT_AGENT, "get_shipment_summary", "delivery_timeline", order_id=order_id
    )
    data = summary.data if summary and isinstance(summary.data, dict) else None
    ctx.send(
        SHIPMENT_AGENT,
        COORDINATOR,
        "shipment_analyzed",
        {"events": len((data or {}).get("events") or [])},
        ctx.refs("get_shipment_summary"),
    )
    return data


async def policy_fetch(ctx: CaseContext) -> dict[str, Any] | None:
    version = ctx.case.get("policy_version")
    ctx.assign(POLICY_AGENT, "load_policy", policy_version=version)
    if not version:
        return None
    policy = await ctx.call(POLICY_AGENT, "get_policy", "policy_rules", policy_version=version)
    return policy.data if policy and isinstance(policy.data, dict) else None


async def seller_verification(ctx: CaseContext, order_id: str) -> list[str]:
    ctx.assign(ORDER_AGENT, "verify_seller", order_id=order_id)
    sellers = await ctx.call(ORDER_AGENT, "get_sellers", "seller_responsibility", order_id=order_id)
    rows = sellers.data if sellers and isinstance(sellers.data, list) else []
    seller_ids = unique(r.get("seller_id") for r in rows if isinstance(r, dict))
    ctx.send(
        ORDER_AGENT,
        POLICY_AGENT,
        "seller_verified",
        {"sellers": seller_ids},
        ctx.refs("get_sellers"),
    )
    return seller_ids


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


@dataclass
class Findings:
    entity: EntityFindings
    order: OrderFindings | None = None
    selection: TimelineSelection | None = None
    payments: PaymentFacts = field(default_factory=PaymentFacts)
    shipment: ShipmentFacts | None = None
    policy: dict[str, Any] | None = None
    classification: Classification | None = None
    rule: dict[str, Any] = field(default_factory=dict)
    seller_ids: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Coordinator entry point. ``case_received``/``case_finalized`` are emitted by the CLI."""
    ctx = CaseContext(case=case, gateway=gateway, trace=trace)
    request = case.get("customer_request") or {}
    claimed_topics = [c.get("topic") for c in request.get("claims") or [] if isinstance(c, dict)]

    entity = await entity_agent(ctx)
    findings = Findings(entity=entity)
    order_id = entity.order_id

    if order_id:
        order, payment_data, policy = await asyncio.gather(
            order_agent(ctx, order_id), payment_agent(ctx, order_id), policy_fetch(ctx)
        )
        findings.order = order
        findings.policy = policy
        rows = entity.history_rows or ([order.order_row] if order.order_row else [])
        rows = [r for r in rows if r.get("order_id") in (None, order_id)]
        selection = select_timeline(rows, case.get("opened_at"))
        findings.selection = selection
        group = selection.group
        items_total = order_items_total(order.items, rows, group)

        # Provisional view from order rows + items decides which extra evidence is needed.
        provisional_ship = shipment_facts(selection.row, rows, group, order.items, None)
        provisional_pay = payment_facts(rows, group, payment_data, None)
        provisional = issue_signals(selection.row, provisional_pay, provisional_ship, items_total)

        needs_shipment = provisional_ship.late or bool(LATE_TOPICS & set(claimed_topics))
        explained = {"payment_mismatch", "duplicate_charge", "valid_split_payment",
                     "canceled_order_paid", "unavailable_order_paid"}
        needs_refund = bool(REFUND_TOPICS & set(claimed_topics)) or (
            not (set(provisional) & explained) and not provisional_ship.late
        )
        extra = []
        if needs_shipment:
            extra.append(shipment_agent(ctx, order_id, "timeline_late_or_claimed"))
        if needs_refund:
            extra.append(refund_agent_step(ctx, order_id, "unexplained_payment_claim"))
        results = await asyncio.gather(*extra)
        shipment_data = results[0] if needs_shipment else None
        refund_data = results[-1] if needs_refund else None

        findings.shipment = shipment_facts(selection.row, rows, group, order.items, shipment_data)
        findings.payments = payment_facts(rows, group, payment_data, refund_data)
        signals = issue_signals(selection.row, findings.payments, findings.shipment, items_total)
        findings.classification = classify(
            signals,
            claimed_topics,
            selection.ambiguous,
            has_core_evidence=selection.row is not None and payment_data is not None,
        )
    else:
        findings.policy = await policy_fetch(ctx)
        findings.classification = classify([], claimed_topics, True, has_core_evidence=False)

    await policy_agent(ctx, findings)
    conflict_agent(ctx, findings)
    output = build_output(ctx, findings, claimed_topics)
    return verifier(ctx, findings, output)


async def policy_agent(ctx: CaseContext, findings: Findings) -> None:
    classification = findings.classification
    assert classification is not None
    issue = classification.primary_issue
    findings.rule = policy_rule(findings.policy, issue)
    seller_ids = list(findings.order.seller_ids) if findings.order else []
    needs_seller = any(
        p.get("party_type") == "seller" for p in findings.rule.get("responsible_parties") or []
    )
    if needs_seller and findings.entity.order_id:
        verified = await seller_verification(ctx, findings.entity.order_id)
        seller_ids = unique([*seller_ids, *verified])
    findings.seller_ids = seller_ids
    ctx.trace.emit(
        case_id=ctx.case_id,
        event_type="policy_decided",
        actor=POLICY_AGENT,
        decision_code=issue,
        evidence_refs=ctx.refs("get_policy") or None,
        attributes={
            "case_status": findings.rule.get("case_status"),
            "recommended_action": findings.rule.get("recommended_action"),
            "signals": ",".join(classification.signals) or "none",
            "claim_supported": classification.claim_supported,
        },
    )
    ctx.send(POLICY_AGENT, CONFLICT_AGENT, "issue_decided", {"primary_issue": issue})


def conflict_agent(ctx: CaseContext, findings: Findings) -> None:
    """Record every place two sources disagree and which source was selected."""
    ctx.assign(CONFLICT_AGENT, "reconcile_sources")
    conflicts: list[dict[str, Any]] = []
    selection = findings.selection
    order_row = findings.order.order_row if findings.order else None
    selected = selection.row if selection else None
    if selection and len(selection.rows) > 1:
        conflicts.append({
            "field": "order_timeline_version",
            "sources": ["get_customer_history.version_1", "get_customer_history.version_2"],
            "selected_source": f"get_customer_history.version_{(selection.selected or 0) + 1}",
            "resolution_code": (
                "SAME_TIMESTAMP_CLAIM_VERIFIED" if selection.ambiguous
                else "LATEST_MATURED_PROMISE_BEFORE_COMPLAINT"
            ),
        })
    if order_row and selected:
        for key, label in (
            ("order_status", "order_status"),
            ("order_purchase_timestamp", "order_purchase_timestamp"),
            ("order_delivered_customer_date", "order_delivered_customer_date"),
        ):
            if order_row.get(key) != selected.get(key):
                conflicts.append({
                    "field": label,
                    "sources": ["get_order", "get_customer_history"],
                    "selected_source": "get_customer_history",
                    "resolution_code": "PRE_COMPLAINT_TIMELINE_AUTHORITATIVE",
                })
    shipment = findings.shipment
    if shipment and shipment.seller_event and shipment.logistics_event:
        conflicts.append({
            "field": "late_delivery_actor",
            "sources": ["get_shipment_summary.events", "get_order_items.shipping_limit"],
            "selected_source": "get_order_items.shipping_limit",
            "resolution_code": "HANDOFF_VS_SHIPPING_LIMIT",
        })
    findings.conflicts = conflicts[:5]
    ctx.send(
        CONFLICT_AGENT,
        VERIFIER,
        "sources_reconciled",
        {"conflicts": len(findings.conflicts)},
    )


# ---------------------------------------------------------------------------
# Output assembly and independent verification
# ---------------------------------------------------------------------------


def build_output(
    ctx: CaseContext, findings: Findings, claimed_topics: list[str]
) -> dict[str, Any]:
    entity = findings.entity
    classification = findings.classification
    assert classification is not None
    issue = classification.primary_issue
    rule = findings.rule
    payments = findings.payments
    shipment = findings.shipment
    order_id = entity.order_id

    ambiguous = bool(findings.selection and findings.selection.ambiguous)
    captured = payments.captured_for(issue, ambiguous) if payments.has_timeline else None
    refunded = payments.refunded_total if payments.has_timeline else None
    remaining = round(max((captured or 0.0) - (refunded or 0.0), 0.0), 2)
    policy_refund = to_amount(rule.get("refund_brl"))
    recommended = round(min(policy_refund, remaining), 2) if captured is not None else 0.0
    if issue in ("insufficient_evidence",):
        recommended = 0.0
    action = str(rule.get("recommended_action") or "manual_review")
    case_status = str(rule.get("case_status") or "needs_investigation")

    late_seller_ids = list(shipment.late_seller_ids) if shipment else []
    if issue != "late_delivery_seller":
        late_seller_ids = []
    seller_ids = unique([*findings.seller_ids, *late_seller_ids])
    parties = responsible_parties(rule, seller_ids, late_seller_ids)

    if not shipment or not findings.selection or findings.selection.row is None:
        ship_verdict = "insufficient_evidence"
    elif issue == "late_delivery_seller":
        ship_verdict = "seller_delay"
    elif issue == "late_delivery_logistics":
        ship_verdict = "logistics_delay"
    elif shipment.delivered and not shipment.late:
        ship_verdict = "on_time"
    elif shipment.delivered and shipment.late:
        ship_verdict = "seller_delay" if shipment.seller_late else "logistics_delay"
    else:
        ship_verdict = "insufficient_evidence"

    if not payments.has_timeline:
        pay_verdict = "insufficient_evidence"
    elif issue in PAYMENT_VERDICTS:
        pay_verdict = PAYMENT_VERDICTS[issue]
    elif refunded and refunded > 0:
        pay_verdict = "refunded"
    else:
        pay_verdict = "reconciled"

    refund_lines = []
    if recommended > 0:
        refund_lines.append(
            {"reason_code": action, "amount_brl": recommended, "entity_id": order_id}
        )

    claim_refs = _claim_refs(ctx, issue)
    claims = []
    for claim in (ctx.case.get("customer_request") or {}).get("claims") or []:
        topic = claim.get("topic")
        claims.append(_assess_claim(claim, topic, issue, recommended, captured, action,
                                    claim_refs, classification.confidence))

    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "case_id": ctx.case_id,
        "assessment": {
            "primary_issue": issue,
            "secondary_issues": [],
            "case_status": case_status,
            "confidence": classification.confidence,
        },
        "affected_entities": {
            "order_ids": [order_id] if order_id else [],
            "item_ids": (findings.order.item_ids if findings.order else [])[:20],
            "seller_ids": seller_ids[:20],
            "payment_references": [],
            "shipment_ids": [],
        },
        "claim_assessments": claims[:5],
        "entity_resolution": {
            "status": entity.status,
            "resolved_order_ids": [order_id] if order_id else [],
            "rejected_candidates": entity.rejected[:20],
            "confidence": entity.confidence,
        },
        "customer_context": {
            "customer_unique_id": entity.customer_unique_id,
            "related_order_ids": entity.related_order_ids[:20],
        },
        "shipment_analysis": {
            "verdict": ship_verdict,
            "late_seller_ids": late_seller_ids[:20],
            "timeline_complete": bool(shipment and shipment.timeline_complete),
        },
        "payment_analysis": {
            "verdict": pay_verdict,
            "captured_total_brl": captured,
            "refunded_total_brl": refunded,
            "refundable_total_brl": remaining if captured is not None else None,
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": issue.upper(), "rank": 1}],
            "responsible_parties": parties,
        },
        "evidence_refs": ctx.refs()[:30],
        "data_conflicts": findings.conflicts,
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": recommended,
            "refund_lines": refund_lines,
        },
        "resolution_actions": [action],
    }


def _claim_refs(ctx: CaseContext, issue: str) -> list[str]:
    tools = ["get_customer_history", "get_order", "get_policy"]
    if "late" in issue:
        tools += ["get_shipment_summary", "get_order_items"]
    elif issue in ("canceled_order_paid", "unavailable_order_paid"):
        tools += ["get_payment_timeline", "get_order_items", "get_sellers"]
    else:
        tools += ["get_payment_timeline", "get_refund_timeline"]
    return ctx.refs(*tools)


def _assess_claim(
    claim: dict[str, Any],
    topic: str | None,
    issue: str,
    recommended: float,
    captured: float | None,
    action: str,
    refs: list[str],
    confidence: float,
) -> dict[str, Any]:
    if topic == "requested_full_refund":
        full_refund = action in FULL_REFUND_ACTIONS
        if recommended <= 0:
            verdict = "unsupported"
        elif full_refund and captured is not None and recommended + 0.01 >= captured:
            verdict = "supported"
        else:
            verdict = "partially_supported"
    elif topic == "unsupported_claim":
        verdict = "unsupported" if issue == "unsupported_claim" else "insufficient_evidence"
    elif issue == "insufficient_evidence":
        verdict = "insufficient_evidence"
    else:
        verdict = "supported" if topic == issue else "unsupported"
    return {
        "claim_id": str(claim.get("claim_id") or topic or "claim")[:64],
        "verdict": verdict,
        "confidence": confidence,
        "evidence_refs": refs[:30],
    }


def verifier(ctx: CaseContext, findings: Findings, output: dict[str, Any]) -> dict[str, Any]:
    """Independent post-handoff checks; fixes violations instead of trusting upstream agents."""
    ctx.assign(VERIFIER, "verify_output")
    fixes: list[str] = []
    consumed = set(ctx.refs())

    refs = [r for r in output["evidence_refs"] if r in consumed]
    if refs != output["evidence_refs"]:
        fixes.append("evidence_scope")
        output["evidence_refs"] = refs
    for claim in output.get("claim_assessments", []):
        claim["evidence_refs"] = [r for r in claim["evidence_refs"] if r in consumed]

    if output["case_id"] != ctx.case_id:
        fixes.append("case_id")
        output["case_id"] = ctx.case_id

    financial = output["financial_resolution"]
    captured = output["payment_analysis"]["captured_total_brl"]
    if captured is not None and financial["recommended_refund_brl"] > captured + 0.01:
        fixes.append("refund_cap")
        financial["recommended_refund_brl"] = captured
    total_lines = round(sum(line["amount_brl"] for line in financial["refund_lines"]), 2)
    if abs(total_lines - financial["recommended_refund_brl"]) > 0.01:
        fixes.append("refund_lines")
        financial["refund_lines"] = [
            {**line, "amount_brl": financial["recommended_refund_brl"]}
            for line in financial["refund_lines"][:1]
        ] if financial["recommended_refund_brl"] > 0 else []

    status = output["assessment"]["case_status"]
    if status == "no_action" and financial["recommended_refund_brl"] > 0:
        fixes.append("status_refund")
        output["assessment"]["case_status"] = "action_required"

    sellers = set(output["affected_entities"]["seller_ids"])
    for party in output["root_cause_analysis"]["responsible_parties"]:
        if party["party_type"] == "seller" and party["party_id"] not in sellers:
            fixes.append("seller_party")
            party["party_id"] = next(iter(sorted(sellers)), None)
    if not set(output["shipment_analysis"]["late_seller_ids"]) <= sellers:
        fixes.append("late_sellers")
        output["shipment_analysis"]["late_seller_ids"] = sorted(
            set(output["shipment_analysis"]["late_seller_ids"]) & sellers
        )

    # Confidence falls when verification had to repair upstream work.
    if fixes:
        output["assessment"]["confidence"] = round(
            max(0.3, output["assessment"]["confidence"] - 0.05 * len(fixes)), 2
        )

    opened = parse_ts(ctx.case.get("opened_at"))
    selected = findings.selection.row if findings.selection else None
    purchase = parse_ts((selected or {}).get("order_purchase_timestamp"))
    ctx.trace.emit(
        case_id=ctx.case_id,
        event_type="verification_completed",
        actor=VERIFIER,
        target=COORDINATOR,
        decision_code="CORRECTED" if fixes else "VERIFIED",
        evidence_refs=output["evidence_refs"][:20] or None,
        attributes={
            "fixes": ",".join(fixes) or "none",
            "evidence_count": len(output["evidence_refs"]),
            "tool_calls": ctx.calls,
            "failed_tools": ",".join(ctx.failed_tools) or "none",
            "timeline_before_complaint": bool(opened and purchase and purchase <= opened),
            "primary_issue": output["assessment"]["primary_issue"],
        },
    )
    ctx.send(VERIFIER, COORDINATOR, "output_verified", {"fixes": len(fixes)})
    return output
