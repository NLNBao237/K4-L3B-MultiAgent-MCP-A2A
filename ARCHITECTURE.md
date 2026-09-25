# L3B Architecture Record

Team: K4-L3B
Variant: L3B

## 1. System overview

A coordinator dispatches specialist agents over an A2A message envelope. Specialists own
disjoint MCP tools; the policy agent decides, the conflict agent reconciles sources and an
independent verifier re-checks the assembled output before the CLI finalizes the case.

```
                         ┌───────────────┐
  case_received (CLI) ─▶ │  coordinator  │ ◀─────────────── output_verified ─────────┐
                         └──────┬────────┘                                           │
        resolve_entity          │ task_assigned (A2A)                                │
          ▼                     ▼ (parallel)                                          │
   ┌─────────────┐   ┌─────────────┐ ┌───────────────┐ ┌──────────────┐               │
   │ entity-agent│   │ order-agent │ │ payment-agent │ │ policy-agent │               │
   └─────┬───────┘   └─────┬───────┘ └──────┬────────┘ └──────┬───────┘               │
         │ entity_resolved │ order_analyzed │ payment/refund    │                       │
         └────────────────▶│◀───────────────┘  timeline         │                       │
                           ▼ (conditional)                      │                       │
                   ┌────────────────┐                           │                       │
                   │ shipment-agent │ shipment_analyzed ───────▶│ issue_decided          │
                   └────────────────┘                           ▼                       │
                                                        ┌────────────────┐              │
                                                        │ conflict-agent │ sources_reconciled
                                                        └───────┬────────┘              │
                                                                ▼                       │
                                                           ┌──────────┐                 │
                                                           │ verifier │ ────────────────┘
                                                           └──────────┘
```

Code: `src/student_agent/workflow.py` (agents, A2A, trace) and
`src/student_agent/analysis.py` (pure, unit-tested evidence reasoning).

## 2. Agent ownership

| Actor | Input | Responsibility | MCP tools | Output / handoff |
|---|---|---|---|---|
| coordinator | case JSON | Dispatch, decide which optional evidence is needed | none | `task_assigned` to every agent |
| entity-agent | candidates, customer hint | Resolve the complained order, reject candidates not owned by the customer, customer context | `get_customer_history` (fallback `get_order` per well-formed candidate) | `entity_resolved` |
| order-agent | order id | Order row, items, sellers, product context; seller verification when policy blames a seller | `get_order`, `get_order_items`, `get_product_context`, `get_sellers` (conditional) | `order_analyzed`, `seller_verified` |
| payment-agent | order id | Captures, reconciliation events, refund lifecycle | `get_payment_timeline`, `get_refund_timeline` (conditional) | `payment_timeline_collected`, `refund_timeline_collected` |
| shipment-agent | order id | Carrier handoff vs shipping limit, delivery vs promise, late events | `get_shipment_summary` (conditional) | `shipment_analyzed` |
| policy-agent | all findings | Classify primary issue, apply `EC_POLICY_V2` rule | `get_policy` | `policy_decided`, `issue_decided` |
| conflict-agent | findings | Record source disagreements and the selected source | none | `sources_reconciled` |
| verifier | assembled output | Independent invariant checks and repair, confidence adjustment | none | `verification_completed`, `output_verified` |

## 3. Entity resolution and A2A protocol

### Entity resolution

1. Candidates = `claimed_order_id` + `candidate_order_ids`.
2. `get_customer_history(customer_unique_id_hint)` returns the customer's authoritative orders.
   A candidate is accepted only if it belongs to that customer; the others (e.g.
   `candidate-001`) are listed in `rejected_candidates` **without extra calls**.
3. If no history is available, only well-formed 32-hex candidates are verified with
   `get_order` (malformed ids would only produce audited error calls).
4. Status: `resolved` (one owned candidate or the claimed one), `ambiguous`, `not_found`.

### Authoritative timeline

The history/items/payment/shipment data of one order contains **several versions** of the
order (one is a distractor). The version the complaint is about is the one whose
**estimated delivery most recently matured before `opened_at`** (a customer complains after
the promise window closes; versions purchased after the complaint or with an open promise
cannot be its subject). Every event, capture, item shipping limit and refund is attributed
to the version it belongs to:

- timestamped events → latest version purchased not after the event;
- refunds → the version whose capture they reverse (amount match), then time;
- versions sharing one timestamp form an ambiguous group; the issue that the evidence
  supports *and* the customer claimed is chosen, confidence is lowered.

### A2A envelope

```python
@dataclass(frozen=True)
class A2AMessage:
    message_id: str       # msg-<case>-<seq>
    case_id: str
    sender: str
    recipient: str
    task_type: str        # entity_resolved, order_analyzed, issue_decided, ...
    correlation_id: str   # <case>:<task_type>
    payload: dict         # scalar summary, mirrored in trace attributes
```

Every `send` emits a `handoff` event (actor=sender, target=recipient, evidence refs used).

### Handoff conditions

| From | To | Condition |
|---|---|---|
| coordinator | entity-agent | always first |
| coordinator | order/payment/policy agents | order resolved (run in parallel) |
| coordinator | shipment-agent | selected version delivered late **or** a late-delivery claim |
| coordinator | payment-agent (refund) | refund claim, or payment evidence explains nothing and delivery was on time |
| policy-agent | order-agent (sellers) | policy rule names a seller as responsible |
| policy-agent | conflict-agent | issue decided |
| conflict-agent | verifier | sources reconciled |
| verifier | coordinator | output verified |

### Timeout and loop prevention

- Straight-line workflow (no agent re-entry, so no loops).
- One call per (tool, arguments) per case (per-case cache); hard cap 12 calls per case.
- Each call has a 120 s timeout. Tool errors (`is_error`, e.g. no refund record) are
  deterministic and never retried. Transport failures abort the case; the CLI reconnects
  (max 5 times, exponential back-off) and re-runs only that case.

## 4. Evidence and conflict lifecycle

- Every call carries the case's own `case_id`; refs come only from gateway responses and are
  never edited or shared across cases (the context is created per case).
- Each consumed result emits `tool_result_consumed` with its `evidence_ref`; the verifier
  drops any output ref that was not consumed in this case, so every output ref is linked to
  the trace.
- `claim_assessments[].evidence_refs` are the refs relevant to the decided issue.

| Conflict | Detection | Resolution |
|---|---|---|
| Several order versions in history | >1 row for the order | `LATEST_MATURED_PROMISE_BEFORE_COMPLAINT` (or `SAME_TIMESTAMP_CLAIM_VERIFIED`) |
| `get_order` row ≠ selected version | status / purchase / delivery differ | select `get_customer_history`, `PRE_COMPLAINT_TIMELINE_AUTHORITATIVE` |
| Late-event actor vs. shipping-limit math | both seller and logistics events | select shipping limit, `HANDOFF_VS_SHIPPING_LIMIT` |
| Claim vs. evidence | claimed topic not among evidence signals | evidence wins, claim marked `unsupported`, lower confidence |

## 5. Decision rules

| Evidence in the selected version | Primary issue |
|---|---|
| status `canceled` + capture | `canceled_order_paid` |
| status `unavailable` + capture | `unavailable_order_paid` |
| refund event `failed` / `pending` | `refund_failed` / `refund_pending` |
| equal captures summing to more than items total | `duplicate_charge` |
| open `reconciliation_mismatch` | `payment_mismatch` |
| delivered after promise, carrier handoff after shipping limit (or seller late event) | `late_delivery_seller` |
| delivered after promise, handoff on time | `late_delivery_logistics` |
| equal captures summing exactly to items total | `valid_split_payment` |
| none of the above | `unsupported_claim` |
| no order / payment evidence | `insufficient_evidence` |

The policy rule for the issue gives `case_status`, `recommended_action` and `refund_brl`.
The refund is capped by `captured - refunded`; policy seller placeholders are bound to the
seller ids observed in this case's items/sellers evidence.

## 6. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event |
|---|:---:|---|---|
| Tool returns error (no record) | 0 | treat as "no record" | `tool_result_consumed` with `decision_code=NO_RECORD` |
| Call timeout / transport error | case re-run after reconnect (≤5) | abort run after 5 | new `task_assigned` sequence |
| Entity not found | 0 | `insufficient_evidence`, `needs_investigation` | `handoff entity_resolved status=not_found` |
| Source conflict | 0 | conflict-agent decision | `handoff sources_reconciled` |
| Invalid assembled output | 0 | verifier repairs, confidence −0.05 per fix | `verification_completed decision_code=CORRECTED` |

Efficiency: 6 base calls (history, order, items, product, policy, payment timeline), plus
shipment summary / refund timeline / sellers only when the decision needs them — 6–8 calls
per case, never the unused `get_order_payments` (subsumed by the payment timeline) and never
calls on malformed candidate ids.

## 7. Verification invariants

- `case_id` equals the input; schema `day09-l3b-output-v2` (also validated by the CLI).
- Output `evidence_refs` ⊆ refs consumed in this case, unique.
- `recommended_refund_brl` ≤ captured; refund lines sum to the recommendation.
- `no_action` ⇒ no refund; action is the policy action for the decided issue.
- Seller parties and `late_seller_ids` ⊆ `affected_entities.seller_ids`.
- Confidence: 0.9 when evidence and claim agree on one signal, 0.75 when evidence contradicts
  the claim, 0.8–0.85 for multi-signal/ambiguous versions, 0.35 without core evidence.

## 8. Reproducibility

- Python 3.11+, dependencies in `pyproject.toml`; native asyncio, no LLM, no randomness in
  decisions (only trace event ids are random).
- `day09 validate-inputs && day09 run && day09 validate && day09 package`.
- Offline tests: `pytest -q tests/test_workflow.py` (synthetic fake gateway).
- Cases run sequentially; calls inside one case run in parallel on one MCP session.

## 9. MCP tool inventory (from `day09 mcp-tools`)

| Tool | Args | Domain | Used |
|---|---|---|---|
| `get_customer_history` | customer_unique_id | customer | always |
| `get_order` | order_id | order | always (fallback resolver) |
| `get_order_items` | order_id | item | always |
| `get_product_context` | order_id | product | when `include_product_context` |
| `get_policy` | policy_version | policy | always |
| `get_payment_timeline` | order_id | payment | always |
| `get_shipment_summary` | order_id | shipment | late version or late claim |
| `get_refund_timeline` | order_id | refund | refund claim or unexplained payment |
| `get_sellers` | order_id | seller | seller-responsible issue |
| `get_order_payments` | order_id | payment | not used (subset of timeline) |

## 10. Trace events

| Event | Emitted by | When |
|---|---|---|
| `case_received` / `case_finalized` | coordinator (CLI) | once per case, around `solve_case` |
| `task_assigned` | coordinator | each dispatched task (`decision_code` = task type) |
| `tool_result_consumed` | owning specialist | each MCP result, with its `evidence_ref` |
| `handoff` | sender agent | each A2A message |
| `policy_decided` | policy-agent | primary issue + policy action |
| `verification_completed` | verifier | after independent checks |
