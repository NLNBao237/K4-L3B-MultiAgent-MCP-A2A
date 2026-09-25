"""Deterministic evidence analysis used by the specialist agents.

The MCP data for one order can contain several versions of the order timeline (for example a
row that was purchased after the complaint was opened). Every function here is pure so the
reasoning can be unit-tested offline without calling the gateway.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PRIMARY_ISSUES = (
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "payment_mismatch",
    "duplicate_charge",
    "refund_pending",
    "refund_failed",
    "unsupported_claim",
    "insufficient_evidence",
)

# When one authoritative timeline shows several signals, the most specific one wins.
ISSUE_PRIORITY = (
    "canceled_order_paid",
    "unavailable_order_paid",
    "refund_failed",
    "refund_pending",
    "duplicate_charge",
    "payment_mismatch",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
)

PAYMENT_VERDICTS = {
    "payment_mismatch": "capture_mismatch",
    "duplicate_charge": "duplicate_capture",
    "refund_pending": "refund_pending",
    "refund_failed": "refund_failed",
    "insufficient_evidence": "insufficient_evidence",
}

REFUND_DONE_STATUSES = {"completed", "succeeded", "refunded", "confirmed", "processed"}
AMOUNT_TOLERANCE = 0.01


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_amount(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def unique(values: Iterable[Any]) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value is not None and value != "" and value not in seen:
            seen.append(value)
    return seen


# ---------------------------------------------------------------------------
# Timeline versions
# ---------------------------------------------------------------------------


@dataclass
class TimelineSelection:
    """Which order-row version is authoritative for the complaint."""

    rows: list[dict[str, Any]]
    selected: int | None
    group: tuple[int, ...]  # versions sharing the selected purchase timestamp
    before_complaint: tuple[int, ...]

    @property
    def row(self) -> dict[str, Any] | None:
        return None if self.selected is None else self.rows[self.selected]

    @property
    def ambiguous(self) -> bool:
        return len(self.group) > 1


def select_timeline(rows: Sequence[dict[str, Any]], opened_at: str | None) -> TimelineSelection:
    """Pick the order version the complaint is about.

    A customer complains once the delivery promise has matured, so the authoritative version
    is the one whose estimated delivery most recently passed before ``opened_at``. Versions
    purchased after the complaint, or whose promise was still open, cannot be its subject.
    """
    rows = list(rows)
    purchases = [parse_ts(row.get("order_purchase_timestamp")) for row in rows]
    promises = [parse_ts(row.get("order_estimated_delivery_date")) for row in rows]
    opened = parse_ts(opened_at)
    valid = [i for i, ts in enumerate(purchases) if ts is not None]
    if not valid:
        return TimelineSelection(rows, 0 if rows else None, (0,) if rows else (), ())
    if opened is None:
        return _select(rows, tuple(valid), purchases, purchases, tuple(valid), latest=True)
    matured = tuple(
        i for i in valid if purchases[i] <= opened and (promises[i] or purchases[i]) <= opened
    )
    if matured:
        keys = [promises[i] or purchases[i] for i in range(len(rows))]
        return _select(rows, matured, keys, purchases, matured, latest=True)
    purchased = tuple(i for i in valid if purchases[i] <= opened)
    if purchased:
        return _select(rows, purchased, purchases, purchases, purchased, latest=True)
    # Every version is after the complaint: fall back to the closest one.
    return _select(rows, tuple(valid), purchases, purchases, (), latest=False)


def _select(
    rows: list[dict[str, Any]],
    pool: tuple[int, ...],
    keys: Sequence[datetime | None],
    purchases: Sequence[datetime | None],
    before: tuple[int, ...],
    latest: bool,
) -> TimelineSelection:
    pick = max if latest else min
    best = pick(keys[i] for i in pool)
    group = tuple(i for i in pool if keys[i] == best)
    purchase = purchases[group[-1]]
    group = tuple(i for i in group if purchases[i] == purchase)
    return TimelineSelection(rows, group[-1], group, before)


def version_of(rows: Sequence[dict[str, Any]], moment: Any) -> tuple[int, ...]:
    """Versions a timestamped event belongs to (latest purchase not after the event)."""
    ts = parse_ts(moment)
    purchases = [parse_ts(row.get("order_purchase_timestamp")) for row in rows]
    if ts is None:
        return ()
    eligible = [i for i, p in enumerate(purchases) if p is not None and p <= ts]
    if not eligible:
        known = [i for i, p in enumerate(purchases) if p is not None]
        if not known:
            return ()
        first = min(purchases[i] for i in known)
        return tuple(i for i in known if purchases[i] == first)
    best = max(purchases[i] for i in eligible)
    return tuple(i for i in eligible if purchases[i] == best)


def belongs(rows: Sequence[dict[str, Any]], group: Sequence[int], moment: Any) -> bool:
    versions = version_of(rows, moment)
    return bool(versions) and set(versions) <= set(group)


# ---------------------------------------------------------------------------
# Specialist findings
# ---------------------------------------------------------------------------


@dataclass
class PaymentFacts:
    captures: list[float] = field(default_factory=list)
    mismatch_amounts: list[float] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)  # {"amount", "status"}
    payment_types: list[str] = field(default_factory=list)
    has_timeline: bool = False
    has_refund_timeline: bool = False

    @property
    def captured_total(self) -> float:
        return round(sum(self.captures), 2)

    @property
    def refunded_total(self) -> float:
        return round(
            sum(
                (r["amount"] for r in self.refunds if r["status"] in REFUND_DONE_STATUSES), 0.0
            ),
            2,
        )

    def refund_statuses(self) -> set[str]:
        return {r["status"] for r in self.refunds}

    def captured_for(self, issue: str, ambiguous: bool) -> float:
        """Captured amount of the selected version.

        When two versions share one purchase timestamp their captures cannot be separated by
        time, so only the captures that explain the verified issue are attributed to it.
        """
        if not ambiguous or not self.captures:
            return self.captured_total
        captures = self.captures
        if issue in ("valid_split_payment", "duplicate_charge"):
            repeated = [a for a in set(captures) if captures.count(a) >= 2]
            if repeated:
                amount = max(repeated, key=captures.count)
                return round(amount * 2, 2)
        if issue in ("refund_pending", "refund_failed"):
            amounts = {r["amount"] for r in self.refunds}
            matching = [a for a in captures if a in amounts]
            if matching:
                return matching[-1]
        if issue == "payment_mismatch" and self.mismatch_amounts:
            return self.mismatch_amounts[-1]
        return captures[-1]


def payment_facts(
    rows: Sequence[dict[str, Any]],
    group: Sequence[int],
    timeline: dict[str, Any] | None,
    refund_timeline: dict[str, Any] | None,
) -> PaymentFacts:
    facts = PaymentFacts()
    if timeline:
        facts.has_timeline = True
        all_captures: list[tuple[tuple[int, ...], float]] = []
        for event in timeline.get("events") or []:
            versions = version_of(rows, event.get("event_at"))
            amount = to_amount(event.get("amount_brl"))
            kind = str(event.get("event_type", "")).lower()
            status = str(event.get("status", "")).lower()
            if kind == "captured" and status != "failed":
                all_captures.append((versions, amount))
                if set(versions) <= set(group):
                    facts.captures.append(amount)
            elif "mismatch" in kind and status != "resolved" and set(versions) <= set(group):
                facts.mismatch_amounts.append(amount)
        facts.payment_types = unique(
            str(p.get("payment_type")) for p in timeline.get("payments") or []
        )
    else:
        all_captures = []
    if refund_timeline:
        facts.has_refund_timeline = True
        for event in refund_timeline.get("events") or []:
            if "refund" not in str(event.get("event_type", "")).lower():
                continue
            amount = to_amount(event.get("amount_brl"))
            if _refund_in_group(rows, group, event.get("event_at"), amount, all_captures):
                facts.refunds.append(
                    {"amount": amount, "status": str(event.get("status", "")).lower()}
                )
    return facts


def _refund_in_group(
    rows: Sequence[dict[str, Any]],
    group: Sequence[int],
    moment: Any,
    amount: float,
    captures: list[tuple[tuple[int, ...], float]],
) -> bool:
    """A refund belongs to the version whose capture it reverses, not merely the nearest one."""
    ts = parse_ts(moment)
    purchases = [parse_ts(row.get("order_purchase_timestamp")) for row in rows]
    earlier = {
        i for i, p in enumerate(purchases) if p is not None and (ts is None or p <= ts)
    }
    owners = {
        v
        for versions, value in captures
        if abs(value - amount) <= AMOUNT_TOLERANCE
        for v in versions
        if v in earlier
    }
    if owners:
        latest = max(purchases[i] for i in owners)
        owners = {i for i in owners if purchases[i] == latest}
        return owners <= set(group)
    return belongs(rows, group, moment)


@dataclass
class ShipmentFacts:
    delivered: bool
    late: bool
    seller_late: bool
    logistics_event: bool
    seller_event: bool
    timeline_complete: bool
    late_seller_ids: list[str]


def shipment_facts(
    row: dict[str, Any] | None,
    rows: Sequence[dict[str, Any]],
    group: Sequence[int],
    items: Sequence[dict[str, Any]],
    summary: dict[str, Any] | None,
) -> ShipmentFacts:
    if not row:
        return ShipmentFacts(False, False, False, False, False, False, [])
    carrier = parse_ts(row.get("order_delivered_carrier_date"))
    delivered_at = parse_ts(row.get("order_delivered_customer_date"))
    estimated = parse_ts(row.get("order_estimated_delivery_date"))
    purchase = parse_ts(row.get("order_purchase_timestamp"))
    late = bool(delivered_at and estimated and delivered_at > estimated)

    limits: list[tuple[str | None, datetime]] = []
    for item in items:
        limit = parse_ts(item.get("shipping_limit_date"))
        if limit and belongs(rows, group, item.get("shipping_limit_date")):
            limits.append((item.get("seller_id"), limit))
    if summary:
        for entry in summary.get("shipping_limits") or []:
            limit = parse_ts(entry.get("shipping_limit_at"))
            if limit and belongs(rows, group, entry.get("shipping_limit_at")):
                limits.append((entry.get("seller_id"), limit))
    late_sellers = unique(
        seller for seller, limit in limits if carrier is not None and carrier > limit
    )

    logistics_event = seller_event = False
    if summary:
        for event in summary.get("events") or []:
            if "late" not in str(event.get("event_type", "")).lower():
                continue
            if not belongs(rows, group, event.get("event_at")):
                continue
            actor = str(event.get("actor", "")).lower()
            if actor == "seller":
                seller_event = True
            elif actor:
                logistics_event = True

    seller_late = bool(late_sellers) or (seller_event and not logistics_event)
    if seller_event and not late_sellers:
        late_sellers = unique(seller for seller, _ in limits)
    return ShipmentFacts(
        delivered=delivered_at is not None,
        late=late or seller_event or logistics_event,
        seller_late=seller_late,
        logistics_event=logistics_event,
        seller_event=seller_event,
        timeline_complete=all([purchase, carrier, delivered_at, estimated]),
        late_seller_ids=late_sellers if seller_late else [],
    )


# ---------------------------------------------------------------------------
# Issue classification
# ---------------------------------------------------------------------------


def order_items_total(items: Sequence[dict[str, Any]], rows, group) -> float | None:
    selected = [
        item for item in items if belongs(rows, group, item.get("shipping_limit_date"))
    ] or list(items[:1])
    if not selected:
        return None
    # The same item id can be repeated by the other timeline version; keep one per id.
    by_id: dict[str, float] = {}
    for item in selected:
        key = str(item.get("order_item_id"))
        by_id.setdefault(key, to_amount(item.get("price")) + to_amount(item.get("freight_value")))
    return round(sum(by_id.values()), 2)


def issue_signals(
    row: dict[str, Any] | None,
    payments: PaymentFacts,
    shipment: ShipmentFacts,
    items_total: float | None,
) -> list[str]:
    """Every primary issue the authoritative evidence supports, most specific first."""
    if not row:
        return []
    status = str(row.get("order_status", "")).lower()
    signals: set[str] = set()
    paid = payments.captured_total > 0 or not payments.has_timeline
    if status == "canceled" and paid:
        signals.add("canceled_order_paid")
    if status == "unavailable" and paid:
        signals.add("unavailable_order_paid")
    statuses = payments.refund_statuses()
    if "failed" in statuses:
        signals.add("refund_failed")
    if "pending" in statuses or "requested" in statuses:
        signals.add("refund_pending")
    if payments.mismatch_amounts:
        signals.add("payment_mismatch")

    captures = payments.captures
    repeated = [a for a in set(captures) if captures.count(a) >= 2]
    if repeated:
        expected = items_total
        for amount in repeated:
            pair_total = round(amount * captures.count(amount), 2)
            if expected is not None and abs(pair_total - expected) <= AMOUNT_TOLERANCE:
                signals.add("valid_split_payment")
            else:
                signals.add("duplicate_charge")
    if shipment.late and status == "delivered":
        signals.add("late_delivery_seller" if shipment.seller_late else "late_delivery_logistics")
    return [issue for issue in ISSUE_PRIORITY if issue in signals]


@dataclass
class Classification:
    primary_issue: str
    signals: list[str]
    claim_supported: bool
    ambiguous: bool
    confidence: float


def classify(
    signals: Sequence[str],
    claimed_topics: Sequence[str],
    timeline_ambiguous: bool,
    has_core_evidence: bool,
) -> Classification:
    if not has_core_evidence:
        return Classification("insufficient_evidence", list(signals), False, True, 0.35)
    claimed = [t for t in claimed_topics if t in PRIMARY_ISSUES]
    if not signals:
        supported = "unsupported_claim" in claimed or not claimed
        return Classification("unsupported_claim", [], supported, False, 0.85)
    matching = [t for t in claimed if t in signals]
    if len(signals) == 1:
        issue = signals[0]
        return Classification(issue, list(signals), issue in claimed, False,
                              0.9 if issue in claimed else 0.75)
    if matching:
        # Several signals (usually two versions sharing one timestamp): the evidence supports
        # the customer's claimed issue, so that one is the independently verified answer.
        return Classification(matching[0], list(signals), True, True,
                              0.8 if timeline_ambiguous else 0.85)
    return Classification(signals[0], list(signals), False, True, 0.6)


# ---------------------------------------------------------------------------
# Policy application
# ---------------------------------------------------------------------------


def policy_rule(policy: dict[str, Any] | None, issue: str) -> dict[str, Any]:
    rules = (policy or {}).get("rules") or {}
    rule = rules.get(issue)
    if isinstance(rule, dict):
        return rule
    if issue == "insufficient_evidence":
        return {
            "case_status": "needs_investigation",
            "recommended_action": "request_more_evidence",
            "refund_brl": 0.0,
            "responsible_parties": [{"party_type": "unknown", "party_id": None}],
        }
    return {
        "case_status": "needs_investigation",
        "recommended_action": "manual_review",
        "refund_brl": 0.0,
        "responsible_parties": [{"party_type": "unknown", "party_id": None}],
    }


def responsible_parties(
    rule: dict[str, Any], seller_ids: Sequence[str], late_seller_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Policy party types, bound to the sellers observed in this case's evidence."""
    parties: list[dict[str, Any]] = []
    for party in rule.get("responsible_parties") or []:
        party_type = party.get("party_type", "unknown")
        if party_type == "seller":
            candidates = list(late_seller_ids) or list(seller_ids)
            for seller in candidates or [None]:
                parties.append({"party_type": "seller", "party_id": seller})
        else:
            parties.append({"party_type": party_type, "party_id": party.get("party_id")})
    deduped: list[dict[str, Any]] = []
    for party in parties:
        if party not in deduped:
            deduped.append(party)
    return deduped[:5] or [{"party_type": "unknown", "party_id": None}]
