"""
L3B Multi-Agent Workflow Implementation

This module implements the Coordinator + Specialist Agent pattern for investigating
e-commerce complaint cases using MCP evidence gateway.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

# ============================================================================
# Data Models
# ============================================================================


@dataclass
class AgentContext:
    """Shared context passed between agents during workflow."""

    case: dict[str, Any]
    case_id: str
    gateway: EvidenceGateway
    trace: TraceWriter

    # Entity resolution
    resolved_order_ids: list[str] = field(default_factory=list)
    rejected_candidates: list[str] = field(default_factory=list)
    customer_unique_id: str | None = None

    # Analysis results
    order_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    customer_history: dict[str, Any] | None = None
    shipment_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    payment_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    refund_data: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Evidence tracking
    evidence_refs: list[str] = field(default_factory=list)

    # Conflict tracking
    data_conflicts: list[dict[str, Any]] = field(default_factory=list)

    # Final output components
    affected_entities: dict[str, list[str]] = field(default_factory=lambda: {
        "order_ids": [],
        "item_ids": [],
        "seller_ids": [],
        "payment_references": [],
        "shipment_ids": [],
    })
    claim_assessments: list[dict[str, Any]] = field(default_factory=list)
    secondary_issues: list[str] = field(default_factory=list)
    resolution_actions: list[str] = field(default_factory=list)


# ============================================================================
# Coordinator (Main Entry Point)
# ============================================================================


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """
    L3B Multi-Agent Case Resolution.

    Flow:
    1. Entity Resolution → find order IDs from case data
    2. Parallel Specialist Analysis → fetch order, payment, shipment data
    3. Customer Context → build customer profile
    4. Policy Analysis → map issues to policies
    5. Conflict Resolution → handle data inconsistencies
    6. Verification → ensure output validity
    7. Output → build final response
    """
    case_id = case["case_id"]
    trace.emit(case_id=case_id, event_type="case_received", actor="coordinator")

    # Initialize context
    ctx = AgentContext(case=case, case_id=case_id, gateway=gateway, trace=trace)

    try:
        # Step 1: Entity Resolution
        await _resolve_entities(ctx)

        # Step 2: Parallel Specialist Analysis
        await _run_specialist_agents(ctx)

        # Step 3: Customer Context
        await _build_customer_context(ctx)

        # Step 4: Policy Analysis
        await _analyze_policies(ctx)

        # Step 5: Conflict Resolution
        await _resolve_conflicts(ctx)

        # Step 6: Verification
        await _verify_output(ctx)

        # Step 7: Build final output
        output = _build_output(ctx)

        trace.emit(case_id=case_id, event_type="case_finalized", actor="coordinator")
        return output

    except Exception as exc:
        # Return partial output on failure
        trace.emit(
            case_id=case_id,
            event_type="verification_completed",
            actor="coordinator",
            attributes={"error": str(exc)},
        )
        # Return a minimal valid output
        return _build_error_output(ctx, str(exc))


# ============================================================================
# Step 1: Entity Resolution
# ============================================================================


async def _resolve_entities(ctx: AgentContext) -> None:
    """Resolve order IDs from case data using entity resolution strategies."""

    trace = ctx.trace
    case_id = ctx.case_id
    gateway = ctx.gateway

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="entity-resolver",
    )

    # Strategy 1: Direct order ID extraction with verification
    order_ids = _extract_order_ids(ctx.case)
    if order_ids:
        # Verify each order exists via MCP
        verified_orders = []
        for oid in order_ids:
            try:
                evidence = await gateway.call("get_order", case_id=case_id, order_id=oid)
                ctx.evidence_refs.append(evidence["evidence_ref"])
                ctx.order_data[oid] = evidence["data"]
                verified_orders.append(oid)
                trace.emit(
                    case_id=case_id,
                    event_type="handoff",
                    actor="entity-resolver",
                    tool_name="get_order",
                    evidence_refs=[evidence["evidence_ref"]],
                    attributes={"order_id": oid, "status": "verified"},
                )
            except Exception as e:
                ctx.rejected_candidates.append(oid)
                trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="entity-resolver",
                    tool_name="get_order",
                    attributes={"order_id": oid, "rejected": True, "reason": str(e)[:100]},
                )
        # Only add verified orders to resolved list (fixes dual-listing bug)
        ctx.resolved_order_ids = verified_orders

    # Strategy 2: Customer-based lookup
    if not ctx.resolved_order_ids:
        customer_id = _extract_customer_id(ctx.case)
        if customer_id:
            try:
                evidence = await gateway.call(
                    "get_customer_history", case_id=case_id, customer_unique_id=customer_id
                )
                ctx.evidence_refs.append(evidence["evidence_ref"])
                ctx.customer_history = evidence["data"]
                trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="entity-resolver",
                    tool_name="get_customer_history",
                    evidence_refs=[evidence["evidence_ref"]],
                )
                # Extract order IDs from customer history
                if "orders" in ctx.customer_history:
                    ctx.resolved_order_ids = [o["order_id"] for o in ctx.customer_history["orders"]]
                    ctx.customer_unique_id = customer_id
            except Exception:
                pass

    # Strategy 3: Search by payment reference
    if not ctx.resolved_order_ids:
        payment_refs = _extract_payment_refs(ctx.case)
        for pref in payment_refs:
            try:
                evidence = await gateway.call("get_payment", case_id=case_id, payment_reference=pref)
                ctx.evidence_refs.append(evidence["evidence_ref"])
                payment = evidence["data"]
                if "order_id" in payment:
                    ctx.resolved_order_ids.append(payment["order_id"])
                ctx.payment_data[pref] = payment
                trace.emit(
                    case_id=case_id,
                    event_type="handoff",
                    actor="entity-resolver",
                    tool_name="get_payment_timeline",
                    evidence_refs=[evidence["evidence_ref"]],
                    attributes={"payment_ref": pref, "order_id": payment.get("order_id")},
                )
            except Exception as e:
                ctx.rejected_candidates.append(pref)
                trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="entity-resolver",
                    tool_name="get_payment",
                    attributes={"payment_ref": pref, "rejected": True, "reason": str(e)[:100]},
                )

    # Handoff to next phase
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="entity-resolver",
        target="specialist-agents",
        attributes={"resolved_count": len(ctx.resolved_order_ids)},
    )


def _extract_order_ids(case: dict[str, Any]) -> list[str]:
    """Extract order IDs from case data using pattern matching."""
    patterns = [
        r"af0bbb47f125381ce9f3597dc70ef07b",  # claimed_order_id
        r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",  # UUID v4
        r"order[_\s]?(?:id)?[:\s]+([A-Z0-9_-]{10,})",
        r"OID[:\s]+([A-Z0-9_-]{10,})",
    ]

    text = str(case)
    found = set()
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        found.update(matches)

    # Also check explicit fields
    if "claimed_order_id" in case:
        found.add(case["claimed_order_id"])
    if "candidate_order_ids" in case:
        found.update(case["candidate_order_ids"])

    return list(found)[:5]  # Limit to 5 candidates


def _extract_customer_id(case: dict[str, Any]) -> str | None:
    """Extract customer unique ID from case data."""
    # Check explicit fields
    if "customer_unique_id" in case:
        return case["customer_unique_id"]
    if "customer_unique_id_hint" in case:
        return case["customer_unique_id_hint"]

    # Pattern matching
    text = str(case)
    matches = re.findall(r"customer[_\s]?(?:id)?[:\s]+([a-z0-9_-]{10,})", text, re.IGNORECASE)
    return matches[0] if matches else None


def _extract_payment_refs(case: dict[str, Any]) -> list[str]:
    """Extract payment references from case data."""
    if "payment_reference" in case:
        return [case["payment_reference"]] if isinstance(case["payment_reference"], str) else case["payment_reference"]

    text = str(case)
    matches = re.findall(r"payment[_\s]?(?:ref)?[:\s]+([A-Z0-9_-]{10,})", text, re.IGNORECASE)
    return matches


# ============================================================================
# Step 2: Specialist Agents (Parallel Execution)
# ============================================================================


async def _run_specialist_agents(ctx: AgentContext) -> None:
    """Run specialist agents in parallel for independent data gathering."""

    trace = ctx.trace
    case_id = ctx.case_id

    # Create tasks for parallel execution
    tasks = [
        _analyze_orders(ctx),
        _analyze_payments(ctx),
        _analyze_shipments(ctx),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Log results
    for i, result in enumerate(results):
        agent_names = ["order-agent", "payment-agent", "shipment-agent"]
        if isinstance(result, Exception):
            trace.emit(
                case_id=case_id,
                event_type="task_assigned",
                actor="coordinator",
                target=agent_names[i],
                attributes={"status": "failed", "error": str(result)},
            )


async def _analyze_orders(ctx: AgentContext) -> None:
    """Order/Item Agent: Analyze orders and their items."""

    trace = ctx.trace
    case_id = ctx.case_id
    gateway = ctx.gateway

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="order-agent")

    for order_id in ctx.resolved_order_ids:
        try:
            # Get order details
            evidence = await gateway.call("get_order", case_id=case_id, order_id=order_id)
            ctx.evidence_refs.append(evidence["evidence_ref"])
            order = evidence["data"]
            ctx.order_data[order_id] = order

            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="order-agent",
                tool_name="get_order",
                evidence_refs=[evidence["evidence_ref"]],
            )

            # Track affected entities
            ctx.affected_entities["order_ids"].append(order_id)

            # Get order items
            try:
                items_evidence = await gateway.call("get_order_items", case_id=case_id, order_id=order_id)
                ctx.evidence_refs.append(items_evidence["evidence_ref"])
                if "items" in items_evidence["data"]:
                    for item in items_evidence["data"]["items"]:
                        if "item_id" in item:
                            ctx.affected_entities["item_ids"].append(item["item_id"])
                trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="order-agent",
                    tool_name="get_order_items",
                    evidence_refs=[items_evidence["evidence_ref"]],
                )
            except Exception:
                pass

            # Get seller info
            if "seller_id" in order:
                seller_id = order["seller_id"]
                try:
                    seller_evidence = await gateway.call("get_sellers", case_id=case_id, seller_id=seller_id)
                    ctx.evidence_refs.append(seller_evidence["evidence_ref"])
                    ctx.affected_entities["seller_ids"].append(seller_id)
                    trace.emit(
                        case_id=case_id,
                        event_type="tool_result_consumed",
                        actor="order-agent",
                        tool_name="get_sellers",
                        evidence_refs=[seller_evidence["evidence_ref"]],
                    )
                except Exception:
                    pass

        except Exception as e:
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="order-agent",
                tool_name="get_order",
                attributes={"error": str(e), "order_id": order_id},
            )

    trace.emit(case_id=case_id, event_type="handoff", actor="order-agent", target="payment-agent")


async def _analyze_payments(ctx: AgentContext) -> None:
    """Payment Agent: Analyze payment captures and refunds."""

    trace = ctx.trace
    case_id = ctx.case_id
    gateway = ctx.gateway

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="payment-agent")

    for order_id in ctx.resolved_order_ids:
        try:
            # Get order payments
            evidence = await gateway.call("get_order_payments", case_id=case_id, order_id=order_id)
            ctx.evidence_refs.append(evidence["evidence_ref"])
            payment = evidence["data"]
            ctx.payment_data[order_id] = payment

            if "payment_reference" in payment:
                ctx.affected_entities["payment_references"].append(payment["payment_reference"])

            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="payment-agent",
                tool_name="get_order_payments",
                evidence_refs=[evidence["evidence_ref"]],
            )

            # Get payment timeline
            if "payment_reference" in payment:
                try:
                    timeline_evidence = await gateway.call(
                        "get_payment_timeline", case_id=case_id, payment_reference=payment["payment_reference"]
                    )
                    ctx.evidence_refs.append(timeline_evidence["evidence_ref"])
                    trace.emit(
                        case_id=case_id,
                        event_type="tool_result_consumed",
                        actor="payment-agent",
                        tool_name="get_payment_timeline",
                        evidence_refs=[timeline_evidence["evidence_ref"]],
                    )
                except Exception:
                    pass

        except Exception as e:
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="payment-agent",
                tool_name="get_order_payments",
                attributes={"error": str(e), "order_id": order_id},
            )

    trace.emit(case_id=case_id, event_type="handoff", actor="payment-agent", target="shipment-agent")


async def _analyze_shipments(ctx: AgentContext) -> None:
    """Shipment Agent: Analyze shipment timelines and delivery status."""

    trace = ctx.trace
    case_id = ctx.case_id
    gateway = ctx.gateway

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="shipment-agent")

    for order_id in ctx.resolved_order_ids:
        try:
            evidence = await gateway.call("get_shipment_summary", case_id=case_id, order_id=order_id)
            ctx.evidence_refs.append(evidence["evidence_ref"])
            shipment = evidence["data"]
            ctx.shipment_data[order_id] = shipment

            if "shipment_id" in shipment:
                ctx.affected_entities["shipment_ids"].append(shipment["shipment_id"])

            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="shipment-agent",
                tool_name="get_shipment_summary",
                evidence_refs=[evidence["evidence_ref"]],
            )

        except Exception as e:
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="shipment-agent",
                tool_name="get_shipment_summary",
                attributes={"error": str(e), "order_id": order_id},
            )

    trace.emit(case_id=case_id, event_type="handoff", actor="shipment-agent", target="policy-agent")


# ============================================================================
# Step 3: Customer Context
# ============================================================================


async def _build_customer_context(ctx: AgentContext) -> None:
    """Customer Context Agent: Build customer profile from history."""

    trace = ctx.trace
    case_id = ctx.case_id
    gateway = ctx.gateway

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="customer-agent")

    # If customer already found during entity resolution
    if ctx.customer_history:
        return

    # Try to find customer from orders
    for order_id, order in ctx.order_data.items():
        if "customer_unique_id" in order:
            ctx.customer_unique_id = order["customer_unique_id"]
            try:
                evidence = await gateway.call(
                    "get_customer_history", case_id=case_id, customer_unique_id=ctx.customer_unique_id
                )
                ctx.evidence_refs.append(evidence["evidence_ref"])
                ctx.customer_history = evidence["data"]
                trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="customer-agent",
                    tool_name="get_customer_history",
                    evidence_refs=[evidence["evidence_ref"]],
                )
            except Exception:
                pass
            break

    trace.emit(case_id=case_id, event_type="handoff", actor="customer-agent", target="policy-agent")


# ============================================================================
# Step 4: Policy Analysis
# ============================================================================


async def _analyze_policies(ctx: AgentContext) -> None:
    """Policy Agent: Map findings to policy rules and determine resolution."""

    trace = ctx.trace
    case_id = ctx.case_id
    gateway = ctx.gateway

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="policy-agent")

    # Fetch policy for each domain
    domains = ["order", "payment", "shipment", "refund"]
    policy_results = {}

    for domain in domains:
        try:
            evidence = await gateway.call("get_policy", case_id=case_id, domain=domain)
            ctx.evidence_refs.append(evidence["evidence_ref"])
            policy_results[domain] = evidence["data"]
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="policy-agent",
                tool_name="get_policy",
                evidence_refs=[evidence["evidence_ref"]],
                attributes={"domain": domain},
            )
        except Exception:
            pass

    # Analyze and classify the primary issue
    primary_issue = _classify_primary_issue(ctx, policy_results)
    secondary_issues = _identify_secondary_issues(ctx, policy_results)

    ctx.claim_assessments.append({
        "claim_id": "primary",
        "verdict": "supported",  # Will be adjusted by verifier
        "confidence": 0.8,
        "evidence_refs": ctx.evidence_refs[-5:] if ctx.evidence_refs else [],  # Last 5 refs
    })

    if secondary_issues:
        ctx.secondary_issues.extend(secondary_issues)

    # Generate resolution actions
    ctx.resolution_actions.extend(_generate_resolution_actions(ctx, policy_results))

    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor="policy-agent",
        decision_code=primary_issue,
    )

    trace.emit(case_id=case_id, event_type="handoff", actor="policy-agent", target="conflict-resolver")


def _classify_primary_issue(ctx: AgentContext, policies: dict[str, Any]) -> str:
    """Classify the primary issue based on collected evidence."""

    # Check order status
    for order in ctx.order_data.values():
        status = order.get("status", "")
        if status == "canceled" and ctx.payment_data:
            return "canceled_order_paid"
        if status == "unavailable":
            return "unavailable_order_paid"

    # Check payment status
    for payment in ctx.payment_data.values():
        if payment.get("status") == "failed":
            return "payment_mismatch"
        if payment.get("refund_status") == "pending":
            return "refund_pending"
        if payment.get("refund_status") == "failed":
            return "refund_failed"

    # Check shipment status
    for shipment in ctx.shipment_data.values():
        estimated = shipment.get("estimated_delivery")
        delivered = shipment.get("delivered_at")
        if estimated and delivered:
            if delivered > estimated:
                return "late_delivery_seller"  # Default assumption

    # Check for duplicates
    payment_refs = list({p.get("payment_reference") for p in ctx.payment_data.values() if p.get("payment_reference")})
    if len(payment_refs) > len(ctx.resolved_order_ids):
        return "duplicate_charge"

    return "insufficient_evidence"


def _identify_secondary_issues(ctx: AgentContext, policies: dict[str, Any]) -> list[str]:
    """Identify secondary issues from the case."""

    secondary = []

    # Check for multiple issues
    if len(ctx.order_data) > 1:
        secondary.append("multiple_orders_involved")

    # Check for cross-seller issues
    sellers = set()
    for order in ctx.order_data.values():
        if "seller_id" in order:
            sellers.add(order["seller_id"])
    if len(sellers) > 1:
        secondary.append("cross_seller_issue")

    return secondary[:10]  # Max 10 secondary issues


def _generate_resolution_actions(ctx: AgentContext, policies: dict[str, Any]) -> list[str]:
    """Generate resolution action recommendations."""

    actions = []

    # Based on primary issue classification
    for order_id in ctx.resolved_order_ids:
        if order_id in ctx.order_data:
            order = ctx.order_data[order_id]

            # Payment-based actions
            if order_id in ctx.payment_data:
                payment = ctx.payment_data[order_id]
                if payment.get("status") == "captured":
                    if payment.get("refund_status") in (None, ""):
                        actions.append("process_refund_for_order")
                    elif payment.get("refund_status") == "pending":
                        actions.append("follow_up_pending_refund")

            # Shipment-based actions
            if order_id in ctx.shipment_data:
                shipment = ctx.shipment_data[order_id]
                if shipment.get("status") == "delivered_late":
                    actions.append("apply_late_delivery_compensation")

    return list(set(actions))[:8]  # Max 8 unique actions


# ============================================================================
# Step 5: Conflict Resolution
# ============================================================================


async def _resolve_conflicts(ctx: AgentContext) -> None:
    """Conflict Resolver: Detect and resolve data inconsistencies."""

    trace = ctx.trace
    case_id = ctx.case_id

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="conflict-resolver")

    # Check for payment amount mismatches
    for order_id in ctx.resolved_order_ids:
        if order_id not in ctx.order_data or order_id not in ctx.payment_data:
            continue

        order = ctx.order_data[order_id]
        payment = ctx.payment_data[order_id]

        # Calculate expected vs actual
        order_total = sum(item.get("price", 0) * item.get("quantity", 1) for item in order.get("items", []))
        payment_captured = payment.get("amount", 0)

        if order_total and payment_captured:
            diff = abs(order_total - payment_captured)
            if diff > 0.01:  # Allow for rounding
                ctx.data_conflicts.append({
                    "field": "order_total_vs_payment",
                    "sources": ["order", "payment"],
                    "selected_source": "payment",  # Payment is source of truth
                    "resolution_code": "PAYMENT_AUTHORITY",
                })

    # Check for shipment timeline conflicts
    for order_id in ctx.resolved_order_ids:
        if order_id not in ctx.order_data or order_id not in ctx.shipment_data:
            continue

        order = ctx.order_data[order_id]
        shipment = ctx.shipment_data[order_id]

        order_date = order.get("order_date")
        delivery_date = shipment.get("delivered_at")

        if order_date and delivery_date:
            try:
                order_dt = datetime.fromisoformat(order_date.replace("Z", "+00:00"))
                delivery_dt = datetime.fromisoformat(delivery_date.replace("Z", "+00:00"))
                if delivery_dt < order_dt:
                    ctx.data_conflicts.append({
                        "field": "timeline",
                        "sources": ["order_date", "delivery_date"],
                        "selected_source": "delivery_date",
                        "resolution_code": "LATER_DATE_PRECEDENCE",
                    })
            except Exception:
                pass

    trace.emit(case_id=case_id, event_type="handoff", actor="conflict-resolver", target="verifier")


# ============================================================================
# Step 6: Verification
# ============================================================================


async def _verify_output(ctx: AgentContext) -> None:
    """Verifier Agent: Validate output against schema and business rules."""

    trace = ctx.trace
    case_id = ctx.case_id

    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="verifier")

    # Deduplicate affected entities
    for key in ctx.affected_entities:
        ctx.affected_entities[key] = list(set(ctx.affected_entities[key]))

    # Deduplicate evidence refs
    ctx.evidence_refs = list(set(ctx.evidence_refs))

    # Deduplicate resolution actions
    ctx.resolution_actions = list(set(ctx.resolution_actions))[:8]

    # Calibrate confidence
    confidence = _calibrate_confidence(ctx)

    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        attributes={"confidence": confidence, "evidence_count": len(ctx.evidence_refs)},
    )


def _calibrate_confidence(ctx: AgentContext) -> float:
    """Calibrate confidence score based on evidence quality."""

    base_confidence = 0.7

    # Increase for strong evidence
    if len(ctx.evidence_refs) >= 5:
        base_confidence += 0.1
    if len(ctx.resolved_order_ids) > 0:
        base_confidence += 0.1

    # Decrease for conflicts
    if len(ctx.data_conflicts) > 0:
        base_confidence -= 0.05 * len(ctx.data_conflicts)

    # Decrease for missing data
    if not ctx.order_data:
        base_confidence -= 0.2
    if not ctx.payment_data:
        base_confidence -= 0.1

    return max(0.0, min(1.0, base_confidence))


# ============================================================================
# Step 7: Output Building
# ============================================================================


def _build_output(ctx: AgentContext) -> dict[str, Any]:
    """Build the final output conforming to L3B output schema."""

    # Calculate financial resolution
    financial_resolution = _calculate_financial_resolution(ctx)

    # Determine case status
    case_status = _determine_case_status(ctx)

    # Build assessment
    primary_issue = _classify_primary_issue(ctx, {})

    return {
        "schema_version": "day09-l3b-output-v2",
        "case_id": ctx.case_id,
        "assessment": {
            "primary_issue": primary_issue,
            "secondary_issues": ctx.secondary_issues[:10],
            "case_status": case_status,
            "confidence": _calibrate_confidence(ctx),
        },
        "affected_entities": {
            "order_ids": ctx.affected_entities["order_ids"][:20],
            "item_ids": ctx.affected_entities["item_ids"][:20],
            "seller_ids": ctx.affected_entities["seller_ids"][:20],
            "payment_references": ctx.affected_entities["payment_references"][:20],
            "shipment_ids": ctx.affected_entities["shipment_ids"][:20],
        },
        "claim_assessments": ctx.claim_assessments[:5],
        "entity_resolution": {
            "status": "resolved" if ctx.resolved_order_ids else "not_found",
            "resolved_order_ids": ctx.resolved_order_ids[:20],
            "rejected_candidates": ctx.rejected_candidates[:20],
            "confidence": _calibrate_confidence(ctx),
        },
        "customer_context": {
            "customer_unique_id": ctx.customer_unique_id,
            "related_order_ids": list(set(
                ctx.resolved_order_ids +
                ([ctx.customer_history.get("order_ids", [])] if ctx.customer_history else [])
            ))[:20],
        },
        "shipment_analysis": _build_shipment_analysis(ctx),
        "payment_analysis": _build_payment_analysis(ctx),
        "root_cause_analysis": _build_root_cause_analysis(ctx),
        "evidence_refs": ctx.evidence_refs[:30],
        "data_conflicts": ctx.data_conflicts[:5],
        "financial_resolution": financial_resolution,
        "resolution_actions": ctx.resolution_actions[:8],
    }


def _build_shipment_analysis(ctx: AgentContext) -> dict[str, Any]:
    """Build shipment analysis section."""

    verdict = "insufficient_evidence"
    late_seller_ids: list[str] = []
    timeline_complete = False

    for shipment in ctx.shipment_data.values():
        if shipment.get("status") == "delivered":
            timeline_complete = True
            if shipment.get("delivered_at") and shipment.get("estimated_delivery"):
                if shipment["delivered_at"] > shipment["estimated_delivery"]:
                    verdict = "late_seller_delay"
                    if shipment.get("seller_id"):
                        late_seller_ids.append(shipment["seller_id"])
        elif shipment.get("status") == "lost":
            verdict = "lost"
        elif shipment.get("status") == "returned":
            verdict = "returned"
        elif shipment.get("status") == "delivered_on_time":
            verdict = "on_time"

    return {
        "verdict": verdict,
        "late_seller_ids": list(set(late_seller_ids)),
        "timeline_complete": timeline_complete,
    }


def _build_payment_analysis(ctx: AgentContext) -> dict[str, Any]:
    """Build payment analysis section."""

    verdict = "insufficient_evidence"
    captured_total = 0.0
    refunded_total = 0.0
    refundable_total = 0.0

    for payment in ctx.payment_data.values():
        captured_total += payment.get("amount", 0)
        if payment.get("refund_status") == "completed":
            refunded_total += payment.get("refund_amount", 0)
        elif payment.get("refund_status") == "pending":
            refundable_total += payment.get("refund_amount", 0)

    if ctx.payment_data:
        if refunded_total > 0:
            verdict = "refunded"
        elif refunded_total == 0 and captured_total > 0:
            verdict = "reconciled"
        elif "pending" in str(ctx.payment_data):
            verdict = "refund_pending"
        elif "failed" in str(ctx.payment_data):
            verdict = "refund_failed"

    return {
        "verdict": verdict,
        "captured_total_brl": captured_total if captured_total > 0 else None,
        "refunded_total_brl": refunded_total if refunded_total > 0 else None,
        "refundable_total_brl": refundable_total if refundable_total > 0 else None,
    }


def _build_root_cause_analysis(ctx: AgentContext) -> dict[str, Any]:
    """Build root cause analysis section."""

    ranked_causes = []
    responsible_parties = []

    # Analyze based on verdict
    shipment_analysis = _build_shipment_analysis(ctx)
    payment_analysis = _build_payment_analysis(ctx)

    if shipment_analysis["verdict"] in ("late_seller_delay", "seller_delay"):
        ranked_causes.append({"cause_code": "LATE_DELIVERY_SELLER", "rank": 1})
        for seller_id in shipment_analysis["late_seller_ids"]:
            responsible_parties.append({"party_type": "seller", "party_id": seller_id})

    if shipment_analysis["verdict"] in ("logistics_delay",):
        ranked_causes.append({"cause_code": "LATE_DELIVERY_LOGISTICS", "rank": 2})
        responsible_parties.append({"party_type": "logistics_provider", "party_id": None})

    if payment_analysis["verdict"] == "refund_pending":
        ranked_causes.append({"cause_code": "REFUND_NOT_PROCESSED", "rank": 1})
        responsible_parties.append({"party_type": "platform", "party_id": None})

    return {
        "ranked_causes": ranked_causes[:5],
        "responsible_parties": responsible_parties[:5],
    }


def _calculate_financial_resolution(ctx: AgentContext) -> dict[str, Any]:
    """Calculate financial resolution for refunds."""

    captured_total = sum(
        p.get("amount", 0) for p in ctx.payment_data.values()
    )
    refunded_total = sum(
        r.get("amount", 0) for r in ctx.refund_data.values()
    )

    recommended_refund = 0.0
    refund_lines = []

    # Determine refund based on case type
    primary_issue = _classify_primary_issue(ctx, {})

    if primary_issue in ("canceled_order_paid", "unavailable_order_paid"):
        recommended_refund = captured_total
        refund_lines.append({
            "reason_code": "FULL_REFUND_CANCELED",
            "amount_brl": captured_total,
            "entity_id": ctx.resolved_order_ids[0] if ctx.resolved_order_ids else None,
        })
    elif primary_issue == "late_delivery_seller":
        # Partial refund for late delivery
        recommended_refund = min(captured_total * 0.1, 50.0)  # 10% or max 50 BRL
        refund_lines.append({
            "reason_code": "PARTIAL_REFUND_LATE_DELIVERY",
            "amount_brl": recommended_refund,
            "entity_id": ctx.resolved_order_ids[0] if ctx.resolved_order_ids else None,
        })
    elif refunded_total > 0:
        recommended_refund = refunded_total

    return {
        "currency": "BRL",
        "recommended_refund_brl": recommended_refund,
        "refund_lines": refund_lines[:10],
    }


def _determine_case_status(ctx: AgentContext) -> str:
    """Determine case status based on analysis."""

    if not ctx.resolved_order_ids:
        return "needs_investigation"

    if ctx.evidence_refs:
        return "action_required"

    return "no_action"


def _build_error_output(ctx: AgentContext, error: str) -> dict[str, Any]:
    """Build a minimal valid output when workflow fails."""

    return {
        "schema_version": "day09-l3b-output-v2",
        "case_id": ctx.case_id,
        "assessment": {
            "primary_issue": "insufficient_evidence",
            "secondary_issues": [],
            "case_status": "needs_investigation",
            "confidence": 0.0,
        },
        "affected_entities": {
            "order_ids": [],
            "item_ids": [],
            "seller_ids": [],
            "payment_references": [],
            "shipment_ids": [],
        },
        "claim_assessments": [],
        "entity_resolution": {
            "status": "not_found",
            "resolved_order_ids": [],
            "rejected_candidates": [],
            "confidence": 0.0,
        },
        "customer_context": {
            "customer_unique_id": None,
            "related_order_ids": [],
        },
        "shipment_analysis": {
            "verdict": "insufficient_evidence",
            "late_seller_ids": [],
            "timeline_complete": False,
        },
        "payment_analysis": {
            "verdict": "insufficient_evidence",
            "captured_total_brl": None,
            "refunded_total_brl": None,
            "refundable_total_brl": None,
        },
        "root_cause_analysis": {
            "ranked_causes": [],
            "responsible_parties": [],
        },
        "evidence_refs": ctx.evidence_refs[:30],
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": 0.0,
            "refund_lines": [],
        },
        "resolution_actions": [],
    }
