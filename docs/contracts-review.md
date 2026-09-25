# Contracts review (L3B)

Tổng hợp review `contracts/` và các rule mà `scripts/check_contracts.py` kiểm tra.
Không sửa file trong `contracts/`: đó là contract đã phát hành, scorer dùng bản gốc.

## 1. Danh sách schema

| File | Validate cái gì | Được gọi ở |
| --- | --- | --- |
| `l3b-output-v2.schema.json` | `outputs/<case_id>.json` | `Contracts.validate_output` (`cli.py`, `submission.py`) |
| `l3a-output-v2.schema.json` | Không dùng trực tiếp, nhưng L3B `$ref` sang `$defs` của file này | cùng chỗ trên |
| `trace-event-v1.schema.json` | từng dòng `traces/trace.jsonl` | `TraceWriter.emit`, `validate_artifacts` |
| `mcp-evidence-response-v1.schema.json` | response của mọi MCP tool | `EvidenceGateway.call` |
| `submission-manifest-v2.schema.json` | `manifest.json` trong ZIP | `package_submission` |

L3B dùng các `$defs` của L3A: `primaryIssue`, `entities`, `idSet`, `claimAssessment`,
`rootCause`, `evidenceRefs`, `dataConflict`, `financialResolution`. Không xoá file L3A.

## 2. Output schema (`day09-l3b-output-v2`)

Có 13 field bắt buộc, cộng thêm `claim_assessments` là tuỳ chọn. Nên luôn có
`claim_assessments` vì mỗi input có claims. Mọi object đều có `additionalProperties: false`.

| Field | Agent điền | Ràng buộc chính |
| --- | --- | --- |
| `schema_version` | coordinator | const `day09-l3b-output-v2` |
| `case_id` | coordinator | trùng input, pattern `^[A-Z0-9][A-Z0-9_-]{2,63}$` |
| `assessment` | coordinator / verifier | `primary_issue` (11 enum), `secondary_issues` ≤ 10 string unique, `case_status`, `confidence` 0–1 |
| `affected_entities` | order/product | 5 mảng `idSet`: `order_ids`, `item_ids`, `seller_ids`, `payment_references`, `shipment_ids` |
| `claim_assessments` | verifier | ≤ 5, mỗi claim có `verdict` (4 enum), `confidence`, `evidence_refs` |
| `entity_resolution` | entity agent | `status` ∈ `resolved`/`ambiguous`/`not_found`, `resolved_order_ids`, `rejected_candidates`, `confidence` |
| `customer_context` | customer agent | `customer_unique_id` string hoặc `null`, `related_order_ids` |
| `shipment_analysis` | shipment agent | `verdict` (7 enum), `late_seller_ids`, `timeline_complete` bool |
| `payment_analysis` | payment agent | `verdict` (7 enum), 3 số tiền BRL ≥ 0 hoặc `null` |
| `root_cause_analysis` | conflict / policy | `ranked_causes` ≤ 5, `responsible_parties` ≤ 5 |
| `evidence_refs` | tất cả | ≤ 30, unique, pattern `^ev_[A-Za-z0-9_-]{20,96}$` |
| `data_conflicts` | conflict resolver | ≤ 5; `sources` có 2–5 phần tử |
| `financial_resolution` | payment / policy | `currency` const `BRL`, `recommended_refund_brl` ≥ 0 (**không null**), `refund_lines` ≤ 10 |
| `resolution_actions` | coordinator | ≤ 8 string unique, 1–80 ký tự |

Enum hay dùng:

- `primary_issue`: `canceled_order_paid`, `unavailable_order_paid`, `late_delivery_seller`,
  `late_delivery_logistics`, `valid_split_payment`, `payment_mismatch`, `duplicate_charge`,
  `refund_pending`, `refund_failed`, `unsupported_claim`, `insufficient_evidence`.
- `shipment_analysis.verdict`: `on_time`, `seller_delay`, `logistics_delay`, `lost`,
  `returned`, `conflicting`, `insufficient_evidence`.
- `payment_analysis.verdict`: `reconciled`, `capture_mismatch`, `duplicate_capture`,
  `refund_pending`, `refund_failed`, `refunded`, `insufficient_evidence`.
- `responsible_parties.party_type`: `seller`, `platform`, `logistics_provider`,
  `payment_provider`, `customer`, `unknown`.

Bẫy dễ fail schema:

- `cause_code` phải là UPPER_SNAKE: `^[A-Z][A-Z0-9_]{2,79}$`. Ví dụ `CARRIER_DELAY` hợp lệ.
- `rank` là số nguyên 1–5.
- Mọi `idSet` có `uniqueItems` và tối đa 20 phần tử.
- Tiền là `number`, không phải string. Chỉ 3 field trong `payment_analysis` được phép `null`.

## 3. Trace event (`day09-trace-event-v1`)

- Có 6 field bắt buộc; `TraceWriter` tự sinh `schema_version`, `event_id`, `occurred_at`.
- `event_type` có 7 giá trị: `case_received`, `task_assigned`, `tool_result_consumed`,
  `handoff`, `policy_decided`, `verification_completed`, `case_finalized`.
- Scorer bắt buộc 5 event: `case_received`, `task_assigned`, `handoff`,
  `verification_completed`, `case_finalized`.
- `cli.py` đã emit `case_received` (đầu) và `case_finalized` (cuối). `workflow.py`
  chỉ emit phần ở giữa, **không emit lại** hai event này.
- Điểm workflow gồm: đủ event, thứ tự receive → finalize, nhiều actor phối hợp,
  và evidence được link vào trace. Mọi `evidence_ref` trong output phải xuất hiện
  trong một event `tool_result_consumed` của đúng case.
- Giới hạn: tối đa 20 `evidence_refs` mỗi event; `attributes` tối đa 20 key, giá trị scalar.
- Không ghi prompt, chain-of-thought hay API key vào trace.

## 4. MCP evidence envelope

Mỗi response có `schema_version`, `evidence_ref`, `result_hash`, `domain`, `data` và
`warnings` (tuỳ chọn). Chỉ dùng `evidence_ref` do MCP trả về; không sửa, không tự tạo,
không dùng lại giữa các case. Mọi call đều bị audit và tính vào efficiency.

Tool hiện có (lấy từ `day09 mcp-tools`), tất cả đều nhận `case_id`:

| Tool | Tham số thêm | Mô tả |
| --- | --- | --- |
| `get_order` | `order_id` | order row authoritative |
| `get_order_items` | `order_id` | item + seller rows |
| `get_order_payments` | `order_id` | payment rows + lifecycle |
| `get_payment_timeline` | `order_id` | base payments + payment lifecycle events |
| `get_refund_timeline` | `order_id` | refund lifecycle events |
| `get_shipment_summary` | `order_id` | delivery timestamps, seller handoff limits, shipment events |
| `get_sellers` | `order_id` | seller records của các item |
| `get_product_context` | `order_id` | products + translated categories |
| `get_customer_history` | `customer_unique_id` | lịch sử order của customer |
| `get_policy` | `policy_version` | policy machine-readable |

## 5. Rule mà `scripts/check_contracts.py` kiểm tra

Các rule consistency là suy ra từ mô tả trong `scoring-policy-v2.json` (oracle là
private). Nếu nhóm thống nhất khác, sửa trong script.

Input (`inputs`):

- có `policy_version`, `customer_request`, `candidate_order_ids`;
- `claims` không rỗng, `claim_id` unique; topic nằm trong enum `primaryIssue` hoặc
  `requested_full_refund` (nếu không thì chỉ cảnh báo);
- `candidate_order_ids` là mảng string unique; cảnh báo khi `claimed_order_id` null
  hoặc không nằm trong candidates;
- in phân bố case type.

Output + trace (`outputs`):

| Nhóm | Rule |
| --- | --- |
| Hard gate | pass schema; `case_id` khớp; `evidence_refs` không rỗng; mọi ref có trong `tool_result_consumed` của đúng case; một ref không được dùng cho 2 case |
| Entity | resolved ∩ rejected = ∅; resolved ∪ rejected ⊆ candidates; `resolved` ⇒ có order; `not_found` ⇒ không có order; `affected_entities.order_ids` ⊆ resolved |
| Claim | `claim_assessments` khớp 1-1 với claims input; ref của claim ⊆ `evidence_refs` |
| Shipment | `seller_delay` ⇔ có `late_seller_ids` và có party `seller`; `late_seller_ids` ⊆ `affected_entities.seller_ids` |
| Tiền | tổng `refund_lines` = `recommended_refund_brl`; `recommended_refund_brl` ≤ `refundable_total_brl`; `refundable_total_brl` ≤ `captured_total_brl` − `refunded_total_brl`; payment `insufficient_evidence` ⇒ không refund |
| Status | `no_action` ⇒ refund = 0 và không có action; `action_required` ⇒ có action |
| Root cause | `rank` không trùng |
| Trace | đủ 5 event bắt buộc; đầu là `case_received`, cuối là `case_finalized`, mỗi loại chỉ 1 lần; ≥ 3 actor |

Chạy:

```bash
python scripts/check_contracts.py inputs
python scripts/check_contracts.py outputs
```

Ghi chú: trên máy đã giải nén input, `tests/test_release_safety.py` sẽ fail. Đây là
hành vi mong đợi, vì test đó kiểm tra repo sạch trước khi phát hành.
