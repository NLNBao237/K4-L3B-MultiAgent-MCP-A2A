# L3B Architecture Record

Team: K4-TeamXX-<TenNhom>
Variant: L3B

## 1. System overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              COORDINATOR                                    │
│                    (solve_case entry point)                                │
│                              │                                              │
│            ┌─────────────────┼─────────────────┐                            │
│            ▼                 ▼                 ▼                            │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                     │
│    │  Entity      │  │  Order/Item  │  │  Customer    │                     │
│    │  Resolver    │  │  Agent       │  │  Context     │                     │
│    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                     │
│           │                 │                 │                              │
│           └─────────────────┼─────────────────┘                              │
│                             ▼                                               │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                    │
│    │  Payment     │  │  Shipment    │  │  Policy      │                    │
│    │  Agent       │  │  Agent        │  │  Agent       │                    │
│    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                    │
│           │                 │                 │                              │
│           └─────────────────┼─────────────────┘                              │
│                             ▼                                               │
│                    ┌──────────────┐                                         │
│                    │  Conflict    │                                         │
│                    │  Resolver    │                                         │
│                    └──────┬───────┘                                         │
│                           ▼                                                  │
│                    ┌──────────────┐                                         │
│                    │  Verifier    │                                         │
│                    │  Agent       │                                         │
│                    └──────┬───────┘                                         │
│                           ▼                                                  │
│                     [OUTPUT]                                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
|-------|-------|-------------|------------------|----------------|
| Coordinator | case dict | Điều phối workflow, gọi specialists | list_tools, no direct evidence | Dispatch tasks |
| Entity Resolver | case data | Resolve order IDs từ candidate clues | get_order, get_customer_history | resolved_order_ids |
| Order/Item Agent | order_ids | Phân tích order, items, sellers | get_order, get_item, get_seller | order_analysis |
| Customer Context Agent | customer_id | Lịch sử mua hàng, behavior patterns | get_customer_history | customer_profile |
| Payment Agent | order_ids, payment_refs | Phân tích capture, refunds | get_payment, get_refund | payment_verdict |
| Shipment Agent | order_ids, shipment_ids | Timeline, delays, logistics | get_shipment | shipment_verdict |
| Policy Agent | all analyses | Map issues → policy rules | get_policy | policy_recommendations |
| Conflict Resolver | conflicting data | Resolve source conflicts | (no MCP tools) | resolved_conflicts |
| Verifier Agent | all outputs | Final validation, confidence calibration | (no MCP tools) | verified_output |

## 3. Entity resolution và A2A protocol

### Entity Resolution Strategy

1. **Direct ID extraction**: Parse case description for exact order IDs
2. **Customer-based lookup**: If customer ID provided, fetch all orders
3. **Candidate ranking**: Score candidates by evidence overlap
4. **Confidence threshold**: Require ≥0.7 confidence to accept resolution

### A2A Message Envelope

```python
@dataclass
class AgentMessage:
    sender: str           # agent name
    recipient: str | None  # None = broadcast
    case_id: str
    task_type: str        # "resolve_entity", "analyze_payment", etc.
    payload: dict          # data being passed
    correlation_id: str   # for tracing
```

### Handoff Conditions

| From | To | Condition |
|------|----|----------|
| Coordinator | Entity Resolver | case has no exact order_id OR needs customer context |
| Entity Resolver | Order Agent | order_ids resolved |
| Entity Resolver | Customer Agent | customer_unique_id needed |
| Order Agent | Payment Agent | Payment refs found in order |
| Order Agent | Shipment Agent | Shipment IDs found in order |
| All Specialists | Conflict Resolver | Data conflicts detected |
| Conflict Resolver | Verifier | All conflicts resolved |
| Verifier | Coordinator | Output validated |

### Timeout & Loop Prevention

- **Max agent calls per case**: 50 MCP calls total
- **Retry budget**: 2 retries per tool with exponential backoff
- **Loop detection**: Track (agent, task_type) pairs, fail if >3 same pair

## 4. Evidence và conflict lifecycle

### Evidence Collection Rules

1. **Always use `case_id`**: Every MCP call MUST include case_id
2. **Store evidence_ref**: Save every returned `evidence_ref` for traceability
3. **Single-use**: Each evidence_ref used for one purpose only
4. **Cross-case forbidden**: Never use evidence from different cases

### Evidence → Output Mapping

```
MCP Tool Call → evidence_ref → stored in output.evidence_refs
                                    ↓
                            trace.emit(tool_result_consumed, evidence_refs=[...])
```

### Conflict Detection & Resolution

| Conflict Type | Detection | Resolution |
|---------------|----------|-----------|
| Payment amount mismatch | Compare capture vs sum(items) | Use payment capture as source of truth |
| Delivery date conflict | Compare order vs shipment dates | Flag as "conflicting", use latest date |
| Order status inconsistency | Compare order status vs payment status | Escalate to Conflict Resolver |
| Missing evidence | Required field returns null | Mark as "insufficient_evidence" |

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event |
|---------|:------------:|----------|------------|
| MCP timeout | 2 retries | Return partial with warning | tool_result_consumed + warning |
| Entity not found | 0 retries | Mark status="not_found" | handoff with decision_code |
| Source conflict | 1 retry | Conflict Resolver decision | policy_decided |
| Invalid specialist result | 0 retries | Re-run specialist OR skip | verification_completed |

### Efficiency Strategy

- **Cache strategy**: Cache customer → orders mapping within case
- **Query budget**: Max 5 tool calls per entity type
- **Parallel calls**: Independent agents can run concurrently (asyncio.gather)
- **Early termination**: Stop when all required evidence collected

## 6. Verification invariants

Before finalize, verify:

- [ ] `case_id` matches input
- [ ] `schema_version` = "day09-l3b-output-v2"
- [ ] `assessment.primary_issue` is valid enum value
- [ ] `assessment.confidence` ∈ [0, 1]
- [ ] `affected_entities` contains at least one order_id OR reason for none
- [ ] All `evidence_refs` are unique and non-empty
- [ ] `data_conflicts` resolution codes are populated
- [ ] `financial_resolution.refund_lines` sum ≤ captured_total_brl
- [ ] `root_cause_analysis.responsible_parties` party_types are valid
- [ ] `resolution_actions` max 8 items, unique

## 7. Reproducibility

- **Python**: 3.11+
- **Framework**: Native asyncio with dataclass-based agents
- **Dependencies**: See pyproject.toml (pinned versions)
- **Concurrency**: Max 1 case at a time (sequential processing)
- **Random seed**: None (deterministic)
- **Run command**: `day09 run`
- **Validation**: `day09 validate`

## 8. MCP Tool Inventory

Discovered via `day09 mcp-tools`:

| Tool | Domain | Primary Use |
|------|--------|-------------|
| get_order | order | Fetch order details by ID |
| get_item | item | Fetch item/product details |
| get_seller | seller | Fetch seller info |
| get_customer_history | customer | Customer purchase history |
| get_payment | payment | Payment capture details |
| get_refund | refund | Refund records |
| get_shipment | shipment | Shipment tracking/timeline |
| get_policy | policy | Policy rules and thresholds |

## 9. Output Schema Summary (L3B-specific)

L3B adds to L3A:

```python
{
    "schema_version": "day09-l3b-output-v2",
    "case_id": str,                    # e.g., "L3B_CASE_001"
    "assessment": {
        "primary_issue": primaryIssue, # enum
        "secondary_issues": [str, ...], # max 10
        "case_status": enum,            # action_required | no_action | needs_investigation
        "confidence": float             # 0-1
    },
    "affected_entities": {
        "order_ids": [str, ...],
        "item_ids": [str, ...],
        "seller_ids": [str, ...],
        "payment_references": [str, ...],
        "shipment_ids": [str, ...]
    },
    "entity_resolution": {             # L3B specific
        "status": "resolved" | "ambiguous" | "not_found",
        "resolved_order_ids": [str, ...],
        "rejected_candidates": [str, ...],
        "confidence": float
    },
    "customer_context": {               # L3B specific
        "customer_unique_id": str | null,
        "related_order_ids": [str, ...]
    },
    "shipment_analysis": {             # L3B specific
        "verdict": enum,
        "late_seller_ids": [str, ...],
        "timeline_complete": bool
    },
    "payment_analysis": {              # L3B specific
        "verdict": enum,
        "captured_total_brl": float | null,
        "refunded_total_brl": float | null,
        "refundable_total_brl": float | null
    },
    "root_cause_analysis": {...},      # shared with L3A
    "evidence_refs": [str, ...],       # ev_... format
    "data_conflicts": [...],            # shared with L3A
    "financial_resolution": {...},     # shared with L3A
    "resolution_actions": [str, ...]    # max 8
}
```

## 10. Event Types for Trace

| Event | When | Required Fields |
|-------|------|----------------|
| case_received | Case loaded | case_id, actor="coordinator" |
| task_assigned | Task dispatched to agent | case_id, actor, target |
| handoff | Control transferred | case_id, actor, target |
| tool_result_consumed | MCP response processed | case_id, actor, tool_name, evidence_refs |
| policy_decided | Policy applied | case_id, actor, decision_code |
| verification_completed | Output validated | case_id, actor |
| case_finalized | Case complete | case_id, actor="coordinator" |
