# Case Types Checklist - Day09 L3B

**Team:** K4-L3B  
**Stage:** Giai đoạn 1 - Schema & Contracts Setup  
**Date:** 2026-09-25

---

## 1. Primary Issue Types

| # | Issue Code | Mô tả | Agent xử lý | Priority |
|---|------------|-------|-------------|----------|
| 1 | `canceled_order_paid` | Đơn bị hủy nhưng đã thanh toán | Payment Agent | 🔴 High |
| 2 | `unavailable_order_paid` | Đơn hàng không có hàng | Payment Agent | 🔴 High |
| 3 | `late_delivery_seller` | Giao hàng trễ do người bán | Shipment Agent | 🟡 Medium |
| 4 | `late_delivery_logistics` | Giao hàng trễ do logistics | Shipment Agent | 🟡 Medium |
| 5 | `valid_split_payment` | Thanh toán chia nhóm hợp lệ | Payment Agent | 🟡 Medium |
| 6 | `payment_mismatch` | Số tiền không khớp | Payment Agent | 🔴 High |
| 7 | `duplicate_charge` | Thanh toán trùng lặp | Payment Agent | 🔴 High |
| 8 | `refund_pending` | Hoàn tiền đang chờ | Payment Agent | 🟡 Medium |
| 9 | `refund_failed` | Hoàn tiền thất bại | Payment Agent | 🔴 High |
| 10 | `unsupported_claim` | Claim không được hỗ trợ | Policy Agent | 🟢 Low |
| 11 | `insufficient_evidence` | Không đủ bằng chứng | Verifier Agent | 🟡 Medium |

---

## 2. Shipment Verdict Types

| # | Verdict | Mô tả | Checkpoint |
|---|---------|-------|------------|
| 1 | `on_time` | Giao đúng hạn | shipment.delivered_at <= order.expected_delivery |
| 2 | `seller_delay` | Trễ do người bán | shipped_at > promised_shipping_at |
| 3 | `logistics_delay` | Trễ do logistics | delivered_at > expected_delivery |
| 4 | `lost` | Mất hàng | tracking shows lost/stuck |
| 5 | `returned` | Bị trả lại | return status detected |
| 6 | `conflicting` | Dữ liệu mâu thuẫn | Multiple sources disagree |
| 7 | `insufficient_evidence` | Không đủ dữ liệu | Missing shipment records |

---

## 3. Payment Verdict Types

| # | Verdict | Mô tả | Checkpoint |
|---|---------|-------|------------|
| 1 | `reconciled` | Thanh toán khớp | capture == sum(items) |
| 2 | `capture_mismatch` | Số tiền capture không khớp | capture != sum(items) |
| 3 | `duplicate_capture` | Capture trùng lặp | Multiple captures for same order |
| 4 | `refund_pending` | Refund đang chờ | refund.status == pending |
| 5 | `refund_failed` | Refund thất bại | refund.status == failed |
| 6 | `refunded` | Đã hoàn tiền | refund.status == completed |
| 7 | `insufficient_evidence` | Không đủ dữ liệu | Missing payment records |

---

## 4. Case Status Types

| # | Status | Khi nào | Action |
|---|--------|---------|--------|
| 1 | `action_required` | Cần thực hiện refund/action | Queue for processing |
| 2 | `no_action` | Không cần action | Close case |
| 3 | `needs_investigation` | Cần điều tra thêm | Escalate to senior |

---

## 5. Entity Resolution Status

| # | Status | Mô tả | Confidence Threshold |
|---|--------|-------|---------------------|
| 1 | `resolved` | Entity đã được xác định | >= 0.7 |
| 2 | `ambiguous` | Nhiều candidates, không chắc chắn | < 0.7 |
| 3 | `not_found` | Không tìm thấy entity | N/A |

---

## 6. Claim Topics (from input)

| # | Topic | Mô tả |
|---|-------|-------|
| 1 | `late_delivery_logistics` | Giao trễ do logistics |
| 2 | `requested_full_refund` | Yêu cầu hoàn tiền toàn bộ |
| 3 | *(to be updated)* | Review all input files |

---

## 7. Root Cause Categories

| # | Cause Code Prefix | Mô tả |
|---|-------------------|-------|
| 1 | `SELLER_` | Lỗi từ người bán |
| 2 | `PLATFORM_` | Lỗi từ nền tảng |
| 3 | `LOGISTICS_` | Lỗi từ đơn vị vận chuyển |
| 4 | `PAYMENT_` | Lỗi thanh toán |
| 5 | `CUSTOMER_` | Lỗi từ khách hàng |

---

## 8. Responsible Party Types

| # | Party Type | Agent phụ trách |
|---|-------------|-----------------|
| 1 | `seller` | Order Agent |
| 2 | `platform` | Coordinator |
| 3 | `logistics_provider` | Shipment Agent |
| 4 | `payment_provider` | Payment Agent |
| 5 | `customer` | Customer Context Agent |
| 6 | `unknown` | Verifier Agent |

---

## 9. Validation Checklist per Case

### Pre-Processing
- [ ] case_id matches filename
- [ ] opened_at is valid ISO format
- [ ] customer_request.message is not empty
- [ ] At least one claim in claims array
- [ ] candidate_order_ids is not empty

### Entity Resolution
- [ ] At least one order_id in affected_entities
- [ ] entity_resolution.status is set
- [ ] entity_resolution.confidence >= 0.7 for "resolved"
- [ ] resolved_order_ids > 0 if status = resolved

### Assessment
- [ ] primary_issue is valid enum value
- [ ] case_status is valid enum value
- [ ] confidence is between 0 and 1
- [ ] secondary_issues max 10 items, unique

### Analysis
- [ ] shipment_analysis.verdict is valid enum
- [ ] payment_analysis.verdict is valid enum
- [ ] root_cause_analysis has ranked_causes
- [ ] root_cause_analysis has responsible_parties

### Financial
- [ ] financial_resolution.currency = "BRL"
- [ ] refund_lines sum <= captured_total_brl
- [ ] refund_lines max 10 items
- [ ] recommended_refund_brl >= 0

### Evidence
- [ ] evidence_refs max 30 items, unique
- [ ] All evidence_refs match pattern `^ev_[A-Za-z0-9_-]{20,96}$`
- [ ] data_conflicts max 5 items
- [ ] resolution_actions max 8 items, unique

---

## 10. Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Người 1 (Infrastructure) | | | |
| Người 2 (Schema) | | | |
| Team Lead | | | |

---

*Document auto-generated for Giai đoạn 1 verification*
