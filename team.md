# Phân công nhóm — K4 L3B Multi-Agent MCP + A2A

## 👥 Thành viên

| Vai trò | Họ tên | MSSV |
|---|---|---|
| **Người 1** — Infrastructure · Semantic | Nguyễn Lê Ngọc Bảo | 2A202602852 |
| **Người 2** — Schema · Evidence | Đặng Văn Thái Anh | 2A202602407 |
| **Người 3** — Consistency · Financial | Nguyễn Anh Hoàng | 2A202602816 |
| **Người 4** — Schema · Workflow · Validation | Nguyễn Thành Vinh | 2A202602889 |
| **Người 5** — Integration · Package · Submit | Bùi Đức Thành | 2A202602364 |

## 📌 Hiện trạng (cập nhật trước khi chia việc)

- Workflow đã hoàn thiện: `src/student_agent/workflow.py` (agents, A2A, trace) và
  `src/student_agent/analysis.py` (logic phân tích). Thiết kế xem `ARCHITECTURE.md`.
- Đã nộp 3 bản, điểm public tốt nhất: **v3 = 91.9951** (hard gates: 0).
  Bản **v4** (`dist/submission-v4.zip`) đang chờ chấm.
- Mỗi case dùng 5–7 MCP call (ngân sách ước tính ~6 call/case).
- `pytest -q` có 1 test fail **có chủ đích** khi máy có `inputs/`
  (`test_release_safety` kiểm tra repo phát hành không chứa đề) — bỏ qua test này ở local.

---

## 📅 GIAI ĐOẠN 1: SETUP & VERIFICATION — Người 1 + 2 (15–20 phút)

### 👤 Người 1 — Nguyễn Lê Ngọc Bảo: Infrastructure Check

- [ ] Chạy `pytest -q` (chỉ chấp nhận fail `test_release_safety`)
- [ ] Chạy `day09 validate-inputs` → `OK: l3b / l3b-competition-v1 / 100 cases`
- [ ] Kiểm tra `.env` đúng config (`COMPETITION_API_URL`, `COMPETITION_TEAM_API_KEY`, `MCP_ENDPOINT`)
- [ ] Verify MCP endpoint truy cập được: `day09 mcp-tools` (phải thấy 10 tool)
- [ ] Kiểm tra `inputs/` đủ 100 file

### 👤 Người 2 — Đặng Văn Thái Anh: Schema & Contracts

- [ ] Review `contracts/schemas/*.schema.json` (**không sửa**, schema là chân lý)
- [ ] Nắm cấu trúc `l3b-output-v2.schema.json` (13 field bắt buộc, không thêm field lạ)
- [ ] Nắm `trace-event-v1.schema.json` (7 loại event)
- [ ] Chuẩn bị script kiểm tra (`scripts/validate_inputs.py`, `day09 validate`)
- [ ] Đối chiếu checklist loại case trong `docs/case-types-checklist.md`

### ✅ Giai đoạn 1 DONE khi

- [ ] pytest pass (trừ `test_release_safety`)
- [ ] `validate-inputs` OK
- [ ] 100 input files confirmed
- [ ] `day09 mcp-tools` liệt kê đủ tool

---

## 📅 GIAI ĐOẠN 2: PARALLEL CASE REVIEW — cả 5 người (60–90 phút)

### 📊 Chia case (20 case/người)

| Người | Họ tên | Cases | Focus |
|---|---|---|---|
| **Người 1** | Nguyễn Lê Ngọc Bảo | `L3B_CASE_001` → `020` | Semantic + Classification |
| **Người 2** | Đặng Văn Thái Anh | `L3B_CASE_021` → `040` | Evidence + Provenance |
| **Người 3** | Nguyễn Anh Hoàng | `L3B_CASE_041` → `060` | Consistency + Financial |
| **Người 4** | Nguyễn Thành Vinh | `L3B_CASE_061` → `080` | Schema + Workflow |
| **Người 5** | Bùi Đức Thành | `L3B_CASE_081` → `100` | Integration + Final |

> Mỗi range có đủ 10 loại issue (mỗi loại 2 case) vì input xoay vòng theo thứ tự
> late_logistics → split → mismatch → duplicate → refund_pending → refund_failed →
> unsupported → canceled → unavailable → late_seller.

### 📋 Checklist cho mỗi case (~10 phút/case)

```
┌──────────────────────────────────────────────────────────────┐
│ CASE_ID: ___________                                         │
├──────────────────────────────────────────────────────────────┤
│ A. SCHEMA (2 phút)                                           │
│    □ schema_version = "day09-l3b-output-v2"                  │
│    □ case_id khớp tên file                                   │
│    □ primary_issue là enum hợp lệ                            │
│    □ confidence ∈ [0, 1] (không để 1.0 khi có mâu thuẫn)     │
│    □ case_status là enum hợp lệ                              │
├──────────────────────────────────────────────────────────────┤
│ B. SEMANTIC (3 phút)                                         │
│    □ primary_issue đúng theo evidence (không tin claim mù)   │
│    □ Chọn đúng timeline: bản có hạn giao đã qua gần nhất     │
│      trước opened_at (bản còn lại là nhiễu)                  │
│    □ secondary_issues hợp lý                                 │
│    □ affected_entities đủ order/item/seller                  │
│    □ root_cause_analysis có ranked_causes                    │
├──────────────────────────────────────────────────────────────┤
│ C. EVIDENCE (3 phút)                                         │
│    □ evidence_refs đúng format ev_...                        │
│    □ Chỉ trích dẫn evidence hỗ trợ kết luận (ref thừa bị trừ)│
│    □ Không dùng evidence chéo case                           │
│    □ Mỗi ref có event tool_result_consumed trong trace       │
├──────────────────────────────────────────────────────────────┤
│ D. CONSISTENCY (2 phút)                                      │
│    □ recommended_refund_brl ≤ captured_total_brl             │
│    □ refund_lines cộng lại = recommended_refund_brl          │
│    □ no_action ⇒ refund = 0; action = policy action          │
│    □ lỗi seller ⇒ party seller ∈ affected_entities.seller_ids│
│    □ resolution_actions ≤ 8, không trùng                     │
│    □ entity_resolution.status đúng, candidate giả bị reject  │
├──────────────────────────────────────────────────────────────┤
│ E. WORKFLOW (pass/fail)                                      │
│    □ case_received là event đầu, chỉ 1 lần                   │
│    □ task_assigned / handoff đúng thứ tự                     │
│    □ policy_decided + verification_completed tồn tại         │
│    □ case_finalized là event cuối                            │
└──────────────────────────────────────────────────────────────┘
```

### 📝 Template báo cáo (mỗi người nộp 1 bản)

```markdown
## REPORT: <Họ tên> — <MSSV>
### Range: L3B_CASE_XXX → L3B_CASE_YYY

#### ✅ PASSED (X cases)
- L3B_CASE_001: OK
...

#### ❌ FAILED (Y cases)
| Case ID | Issue | Fix Required |
|---------|-------|--------------|
| L3B_CASE_005 | ... | ... |

#### ⚠️ WARNINGS (Z cases)
- L3B_CASE_008: ...
```

> Nộp report trước khi sang Giai đoạn 3.

---

## 📅 GIAI ĐOẠN 3: FIXES & ITERATION — Người 1 + 2 + 3 (30–45 phút)

### 👤 Người 1 — Nguyễn Lê Ngọc Bảo: Semantic Fixes

- [ ] Sửa phân loại `primary_issue` (`analysis.py`: `select_timeline`, `issue_signals`, `classify`)
- [ ] Rà `secondary_issues`
- [ ] Verify `root_cause_analysis`
- [ ] Rà verdict trong `claim_assessments`

### 👤 Người 2 — Đặng Văn Thái Anh: Evidence Fixes

- [ ] Bổ sung evidence bắt buộc còn thiếu / bỏ ref không liên quan
- [ ] Rà thứ tự & điều kiện gọi MCP (`workflow.py`: `solve_case`)
- [ ] Kiểm tra trace events khớp evidence
- [ ] Verify provenance (ref chỉ đến từ gateway, đúng case)

### 👤 Người 3 — Nguyễn Anh Hoàng: Consistency Fixes

- [ ] Sửa tính toán tiền (`build_output`, `PaymentFacts.captured_for`)
- [ ] Hiệu chuẩn confidence (`classify`, `verifier`)
- [ ] Rà entity resolution (`entity_agent`)
- [ ] Rà `data_conflicts` (`conflict_agent`)

### ✅ Giai đoạn 3 DONE khi

- [ ] Người 1: semantic checks pass
- [ ] Người 2: evidence checks pass
- [ ] Người 3: consistency checks pass
- [ ] `pytest -q tests/test_workflow.py` pass, `ruff check .` sạch
- [ ] `day09 run` lại (nếu có sửa code) và `day09 validate` pass

---

## 📅 GIAI ĐOẠN 4: FINAL VALIDATION & SUBMIT — Người 4 + 5 (20–30 phút)

### 👤 Người 4 — Nguyễn Thành Vinh: Full Validation

- [ ] Chạy `day09 validate` → `OK: 100 outputs / <N> trace events`
- [ ] Rà lại nhanh cả 100 case
- [ ] Kiểm tra số MCP call/case (mục tiêu ≤ 6–7; call thừa làm giảm efficiency)
- [ ] Verify trace đủ lifecycle cho mọi case
- [ ] Final schema compliance check

### 👤 Người 5 — Bùi Đức Thành: Package & Submit

- [ ] `day09 package --output dist/submission.zip`
- [ ] Kiểm tra ZIP chỉ có `manifest.json`, `trace.jsonl`, `outputs/` ở root (không thư mục bọc)
- [ ] Kiểm tra dung lượng (< 12 MB) và không lộ API key
- [ ] Smoke test ngẫu nhiên 5 case
- [ ] Upload lên workspace `/l3b`, chọn bản điểm cao nhất làm final

### ✅ Giai đoạn 4 DONE khi

- [ ] `day09 validate` OK
- [ ] `dist/submission.zip` đã tạo
- [ ] ZIP chứa: `manifest.json`, `trace.jsonl`, `outputs/*.json` (100 file)
- [ ] ZIP < 12 MB
- [ ] Upload thành công, đã chọn final

---

## 📊 Timeline

```
Phút:        0    15   30   45   60   75   90  105  120  135  150  165  180
             |----|----|----|----|----|----|----|----|----|----|----|----|----|
Bảo    (N1): [S]  [==========PARALLEL REVIEW 001-020==========] [FIX] [ V ]
Thái Anh(N2): [S]  [==========PARALLEL REVIEW 021-040==========] [FIX] [ V ]
Hoàng  (N3):       [==========PARALLEL REVIEW 041-060==========] [FIX] [ V ]
Vinh   (N4):       [==========PARALLEL REVIEW 061-080==========] [VAL] [ P ]
Thành  (N5):       [==========PARALLEL REVIEW 081-100==========] [PKG] [ U ]
             |----|----|----|----|----|----|----|----|----|----|----|----|----|
             [==SETUP==][==========REVIEW==========][==FIX==][====FINAL====]

S = Setup · FIX = Sửa lỗi · V/VAL = Validation · P/PKG = Package · U = Upload
```

---

## 🎯 Quick command sheet

```bash
# Người 1 — Setup
pytest -q && day09 validate-inputs && day09 mcp-tools

# Cả nhóm — xem nhanh kết quả một range (đổi 1 21 thành 21 41, 41 61, 61 81, 81 101)
python -c "import json;[print(f'L3B_CASE_{i:03d}', (o:=json.load(open(f'outputs/L3B_CASE_{i:03d}.json',encoding='utf-8')))['assessment']['primary_issue'], o['assessment']['case_status'], o['financial_resolution']['recommended_refund_brl'], len(o['evidence_refs'])) for i in range(1,21)]"

# Giai đoạn 3 — sau khi sửa code
pytest -q tests/test_workflow.py && ruff check .
day09 run

# Giai đoạn 4 — Final
day09 validate
day09 package --output dist/submission.zip
```

---

## 📋 Deliverable checklist

- [ ] **Giai đoạn 1:** pytest pass · validate-inputs OK · mcp-tools OK
- [ ] **Giai đoạn 2:** 5 report đã nộp · đủ 100 case đã review
- [ ] **Giai đoạn 3:** fix đã áp dụng · `day09 validate` pass
- [ ] **Giai đoạn 4:** `dist/submission.zip` đã tạo · upload `/l3b` · chọn final

> ⚠️ Không commit `.env`, `inputs/`, `outputs/`, `traces/`, `dist/` (đã có trong `.gitignore`).
