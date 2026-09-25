# Checklist theo case type (L3B)

Dùng cùng `docs/contracts-review.md`. Mỗi case type có: tool cần gọi, evidence tối thiểu,
giá trị kỳ vọng và các bẫy thường gặp.

> **Lưu ý:** cột "kỳ vọng" là **giả thuyết** suy ra từ tên topic, schema và dataset Olist.
> Oracle là private. Trước khi viết thành rule trong code, xác nhận bằng dữ liệu MCP
> thật của 1–2 case mỗi loại, rồi đánh dấu `[x]` ở mục "Đã xác nhận".

## 0. Tổng quan 100 case public

Chạy `python scripts/check_contracts.py inputs` để xem lại phân bố này.

| Case type (topic claim 1) | Case | Số lượng |
| --- | --- | ---: |
| `late_delivery_logistics` | 001, 011, 021, …, 091 | 10 |
| `valid_split_payment` | 002, 012, …, 092 | 10 |
| `payment_mismatch` | 003, 013, …, 093 | 10 |
| `duplicate_charge` | 004, 014, …, 094 | 10 |
| `refund_pending` | 005, 015, …, 095 | 10 |
| `refund_failed` | 006, 016, …, 096 | 10 |
| `unsupported_claim` | 007, 017, …, 097 | 10 |
| `canceled_order_paid` | 008, 018, …, 098 | 10 |
| `unavailable_order_paid` | 009, 019, …, 099 | 10 |
| `late_delivery_seller` | 010, 020, …, 100 | 10 |

Đặc điểm chung của bộ public:

- Mỗi case có 2 claim; claim thứ hai **luôn** là `requested_full_refund`.
- Có 2 candidate: `claimed_order_id` (order 32 ký tự hex) và một mồi nhử dạng `candidate-XXX`.
- `policy_version` luôn là `EC_POLICY_V2`; `investigation_scope` bật cả 3 cờ.
- Có 4 mẫu `message` lặp lại, trong đó một mẫu dặn không làm theo chỉ dẫn nằm trong khiếu nại.

⚠️ **Không hardcode** các quy luật trên (vị trí mồi nhử, case type theo số case chia 10).
50 case private có thể khác. Topic của claim chỉ là **lời khách hàng nói**, không phải đáp án.

## 1. Checklist chung cho mọi case

### 1.1 Luồng gọi tool đề xuất (tránh gọi thừa)

| Bước | Tool | Mục đích | Bắt buộc? |
| --- | --- | --- | --- |
| 1 | `get_customer_history(customer_unique_id=hint)` | Xác định order nào thuộc customer để resolve/reject candidate | Có |
| 2 | `get_order(order_id=resolved)` | Trạng thái order, các timestamp | Có |
| 3 | `get_policy(policy_version)` | Rule refund, source precedence | Có |
| 4 | `get_order_items(order_id)` | `item_ids`, `seller_ids`, giá, freight, `shipping_limit_date` | Có |
| 5 | `get_shipment_summary(order_id)` | Timeline giao hàng, hạn handoff của seller | Có (output bắt buộc có `shipment_analysis`) |
| 6 | `get_payment_timeline(order_id)` | Base payments + lifecycle, dùng tính captured | Có (output bắt buộc có `payment_analysis`) |
| 7 | `get_refund_timeline(order_id)` | Tổng đã refund, trạng thái refund | Theo case type |
| 8 | `get_sellers(order_id)` | Thông tin seller | Chỉ khi cần xác định seller chịu trách nhiệm |
| 9 | `get_product_context(order_id)` | Sản phẩm/category | Chỉ khi case liên quan đến sản phẩm |
| – | `get_order_payments(order_id)` | Trùng một phần với `get_payment_timeline` | Chỉ gọi khi timeline thiếu hoặc mâu thuẫn |

Nguyên tắc:

- [ ] **Không** gọi tool với candidate mồi nhử nếu `get_customer_history` đã đủ để reject nó.
- [ ] Cache kết quả trong phạm vi **một case**. Không dùng lại evidence giữa các case, kể cả `get_policy`.
- [ ] Retry tối đa 1 lần. Lỗi thì ghi `insufficient_evidence`, **không** bịa dữ liệu.
- [ ] Mỗi lần dùng kết quả tool thì emit `tool_result_consumed` kèm `evidence_ref`.
- [ ] Ghi lại số call của mỗi case để tối ưu efficiency (budget là private).

### 1.2 Các field output phải điền cho mọi case

- [ ] `entity_resolution`: resolved gồm order đúng; rejected gồm các candidate còn lại; `confidence`.
- [ ] `affected_entities`: `order_ids` ⊆ resolved; `item_ids`/`seller_ids` lấy từ `get_order_items`.
- [ ] `customer_context`: `customer_unique_id` lấy từ evidence, **không** copy hint nếu evidence không xác nhận; `related_order_ids` là các order khác của customer.
- [ ] `claim_assessments`: đúng 2 claim, mỗi claim có `evidence_refs` riêng.
- [ ] `shipment_analysis` và `payment_analysis`: luôn điền, kể cả khi case type không liên quan.
- [ ] `data_conflicts`: khi 2 nguồn lệch nhau (ví dụ order status khác payment/shipment), ghi `field`, `sources` (≥ 2), `selected_source` theo policy.
- [ ] `root_cause_analysis`: `cause_code` UPPER_SNAKE, `rank` không trùng.
- [ ] `financial_resolution`: tổng `refund_lines` = `recommended_refund_brl` ≤ `refundable_total_brl`.
- [ ] `assessment.confidence`: thấp khi có conflict hoặc thiếu evidence (được chấm calibration).

### 1.3 Đánh giá claim thứ hai `requested_full_refund`

| Tình huống | Verdict gợi ý |
| --- | --- |
| Policy cho refund toàn bộ số tiền captured còn lại | `supported` |
| Chỉ được refund một phần (freight, phần chênh, phần trùng) | `partially_supported` |
| Không có lỗi nào / payment hợp lệ | `unsupported` |
| Thiếu evidence payment/refund | `insufficient_evidence` |

### 1.4 Trace tối thiểu cho mỗi case

```text
case_received (cli) → task_assigned (coordinator → specialist)
→ tool_result_consumed (specialist, evidence_refs) → handoff (specialist → verifier)
→ [policy_decided] → verification_completed (verifier) → case_finalized (cli)
```

- [ ] Có ít nhất 3 actor khác nhau.
- [ ] `workflow.py` không emit `case_received` và `case_finalized`.

## 2. Checklist từng case type

Mỗi mục có: **Tool thêm** (ngoài luồng chung), **Evidence then chốt**, **Kỳ vọng**, **Bẫy**.

### 2.1 `late_delivery_logistics`

- **Tool thêm:** không cần.
- **Evidence then chốt:** `get_shipment_summary`: seller giao cho carrier **đúng hạn** (handoff ≤ `shipping_limit_date`) nhưng ngày giao khách > ngày ước tính.
- **Kỳ vọng:**
  - `primary_issue=late_delivery_logistics`, `case_status=action_required`
  - `shipment.verdict=logistics_delay`, `late_seller_ids=[]`
  - `responsible_parties` gồm `logistics_provider`
  - Refund theo policy (thường là một phần, ví dụ freight hoặc % giá trị)
  - Claim 2 thường là `partially_supported`
- **Bẫy:** nếu seller handoff trễ thì case là `late_delivery_seller`, không phải logistics. Nếu đã giao đúng hạn thì là `unsupported_claim`.
- [ ] Đã xác nhận bằng MCP

### 2.2 `late_delivery_seller`

- **Tool thêm:** `get_sellers` (khi cần xác định seller).
- **Evidence then chốt:** `get_shipment_summary` / `get_order_items`: seller handoff > `shipping_limit_date`.
- **Kỳ vọng:**
  - `primary_issue=late_delivery_seller`, `shipment.verdict=seller_delay`
  - `late_seller_ids` chỉ gồm seller trễ; các id này nằm trong `affected_entities.seller_ids`
  - `responsible_parties` gồm `seller` kèm `party_id`
  - Refund theo policy
- **Bẫy:** order nhiều seller thì chỉ đưa seller trễ vào `late_seller_ids`. Seller trễ nhưng khách vẫn nhận đúng hạn thì cần xem policy.
- [ ] Đã xác nhận bằng MCP

### 2.3 `valid_split_payment`

- **Tool thêm:** không cần. Chỉ gọi `get_order_payments` khi timeline không đủ.
- **Evidence then chốt:** nhiều dòng payment (ví dụ voucher + credit card, `payment_sequential` 1..n) có tổng bằng tổng order (item + freight).
- **Kỳ vọng:**
  - `primary_issue=valid_split_payment`, `payment.verdict=reconciled`
  - `case_status=no_action`, refund = 0, `resolution_actions=[]`
  - Claim 1 `supported` (split payment là hợp lệ), claim 2 `unsupported`
- **Bẫy:** khách tưởng bị trừ tiền 2 lần. Đừng nhầm với `duplicate_charge`: kiểm tra `payment_type` và tổng tiền.
- [ ] Đã xác nhận bằng MCP

### 2.4 `payment_mismatch`

- **Tool thêm:** `get_order_payments` nếu timeline mâu thuẫn.
- **Evidence then chốt:** tổng captured ≠ tổng order.
- **Kỳ vọng:**
  - `primary_issue=payment_mismatch`, `payment.verdict=capture_mismatch`
  - Refund = phần captured vượt quá tổng order
  - `responsible_parties` là `payment_provider` hoặc `platform`
  - Thường có `data_conflicts` (order total khác payment total)
  - Claim 2 thường là `partially_supported`
- **Bẫy:** nếu captured < tổng order thì không có gì để refund, cần xem policy. Làm tròn tiền với sai số 0.01.
- [ ] Đã xác nhận bằng MCP

### 2.5 `duplicate_charge`

- **Tool thêm:** không cần.
- **Evidence then chốt:** `get_payment_timeline` có 2 capture giống nhau (cùng số tiền, cùng phương thức, thời điểm gần nhau).
- **Kỳ vọng:**
  - `primary_issue=duplicate_charge`, `payment.verdict=duplicate_capture`
  - Refund = số tiền bị trùng
  - `responsible_parties` là `payment_provider`
  - Claim 2 thường là `partially_supported`
- **Bẫy:** phân biệt với split payment hợp lệ (2.3). Kiểm tra refund timeline xem khoản trùng đã được hoàn chưa; nếu đã hoàn thì `payment.verdict` có thể là `refunded`.
- [ ] Đã xác nhận bằng MCP

### 2.6 `refund_pending`

- **Tool thêm:** `get_refund_timeline`.
- **Evidence then chốt:** refund đã được khởi tạo nhưng chưa hoàn tất.
- **Kỳ vọng:**
  - `primary_issue=refund_pending`, `payment.verdict=refund_pending`
  - `refunded_total_brl` = số tiền đã hoàn thực sự (thường là 0)
  - Action: theo dõi/đẩy nhanh refund; tránh tạo refund mới trùng lặp
- **Bẫy:** refund đang pending đã bao gồm số tiền cần hoàn, nên `recommended_refund_brl` không được cộng thêm lần nữa. Kiểm tra theo policy.
- [ ] Đã xác nhận bằng MCP

### 2.7 `refund_failed`

- **Tool thêm:** `get_refund_timeline`.
- **Evidence then chốt:** có event refund thất bại và không có refund thành công sau đó.
- **Kỳ vọng:**
  - `primary_issue=refund_failed`, `payment.verdict=refund_failed`
  - `refunded_total_brl` không tính khoản thất bại
  - Action: retry refund; refund = số tiền đã thất bại
- **Bẫy:** nếu sau khi thất bại đã retry thành công thì verdict là `refunded`.
- [ ] Đã xác nhận bằng MCP

### 2.8 `canceled_order_paid`

- **Tool thêm:** `get_refund_timeline`.
- **Evidence then chốt:** `get_order` có `order_status=canceled` nhưng payment đã capture và chưa được hoàn.
- **Kỳ vọng:**
  - `primary_issue=canceled_order_paid`, `case_status=action_required`
  - Refund = captured − refunded (toàn bộ phần còn lại)
  - Claim 2 thường là `supported`
- **Bẫy:** nếu order đã được hoàn đủ thì case không cần action. Khi order status mâu thuẫn với shipment (ví dụ canceled nhưng có ngày giao), ghi `data_conflicts` và chọn source theo policy.
- [ ] Đã xác nhận bằng MCP

### 2.9 `unavailable_order_paid`

- **Tool thêm:** `get_refund_timeline`; `get_product_context` nếu cần chứng minh sản phẩm không có hàng.
- **Evidence then chốt:** `order_status=unavailable` nhưng payment đã capture.
- **Kỳ vọng:**
  - `primary_issue=unavailable_order_paid`, refund = captured − refunded
  - `responsible_parties` là `seller` hoặc `platform`
  - Claim 2 thường là `supported`
- **Bẫy:** tương tự 2.8. Không có ngày giao hàng thì `shipment.verdict` nên là `insufficient_evidence` hoặc tuỳ policy, không phải `on_time`.
- [ ] Đã xác nhận bằng MCP

### 2.10 `unsupported_claim`

- **Tool thêm:** tuỳ nội dung claim; gọi tool đủ để **bác bỏ** claim.
- **Evidence then chốt:** evidence cho thấy claim sai (ví dụ giao đúng hạn, payment khớp, đã refund đủ).
- **Kỳ vọng:**
  - `primary_issue=unsupported_claim`, `case_status=no_action`
  - Refund = 0, `resolution_actions=[]`
  - Cả 2 claim là `unsupported`
- **Bẫy:** input có thể chứa instruction chèn vào khiếu nại, **không làm theo**. Vẫn phải gắn evidence cho kết luận "không có lỗi". Evidence không đủ để kết luận thì dùng `insufficient_evidence` + `needs_investigation`, không dùng `unsupported_claim`.
- [ ] Đã xác nhận bằng MCP

## 3. Quy trình xác nhận giả thuyết

1. [ ] Mỗi case type chọn 1 case (ví dụ 001–010), chạy agent với logging đầy đủ `data` từ MCP (log để ngoài `outputs/` và `traces/`).
2. [ ] So sánh dữ liệu thật với cột "Kỳ vọng"; sửa tài liệu này nếu sai.
3. [ ] Ghi lại số call thực tế của mỗi loại để đặt budget.
4. [ ] Chạy `day09 run`, `day09 validate`, `python scripts/check_contracts.py outputs`.
5. [ ] Nộp bản public và đối chiếu điểm theo từng component với từng case type để tìm rule sai.
