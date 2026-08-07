# Gate 2 MVP — Phân công 4 người và kế hoạch PR

## 1. Phân công chính

| Thành viên | Vai trò | Ownership |
|---|---|---|
| Vũ Nguyễn Quốc Đạt | Product/QA & integration lead | Scope, fixture acceptance, architecture, demo script, PR/release tracking |
| Lương Trung Chiến | Product/QA & evaluation owner | 5 manual test cases, expected behaviour, README verification, evidence archive |
| Nguyễn Hoàng Vĩnh Phong | UI/integration owner | Browser UI, API integration, loading/error states, screenshot/video support |
| Nguyễn Hữu Kiên | Backend/agent owner | FastAPI, local persistence, profiler, evidence, live LLM, HITL, runner |

Nếu ownership khác thực tế, cập nhật bảng này trước PR đầu tiên; mỗi task vẫn chỉ có một
owner chính và một reviewer khác owner.

## 2. Mười PR tối thiểu

| # | PR | Owner | Reviewer | Merge condition |
|---:|---|---|---|---|
| 1 | `gate2: add deterministic taxi fixture and manifest` | Chiến | Đạt | Fixture 54 rows, defect manifest, data test |
| 2 | `gate2: add local SQLite workflow store` | Kiên | Phong | State persistence test |
| 3 | `gate2: add dataset loading and profile API` | Kiên | Chiến | API/profile tests |
| 4 | `gate2: add rule schemas and aggregate evidence guard` | Kiên | Đạt | No-raw-data test |
| 5 | `gate2: add real OpenAI rule proposer boundary` | Kiên | Chiến | Mocked automated tests + one manual live call |
| 6 | `gate2: add HITL lifecycle and audit events` | Kiên | Phong | Transition/error tests |
| 7 | `gate2: add safe local DQ rule runner and results` | Kiên | Đạt | Approved-only/result tests |
| 8 | `gate2: replace prototype with MVP dataset/profile UI` | Phong | Kiên | Browser happy/loading/error states |
| 9 | `gate2: connect proposal review and results UI` | Phong | Chiến | Manual UI flow evidence |
| 10 | `gate2: add README, diagram, eval evidence and demo checklist` | Đạt | Chiến | Clean setup/rehearsal review |

Mỗi PR cần branch riêng từ `main`, reviewer khác owner và merge tuần tự. Không dùng một
PR lớn rồi tách commit giả để đạt số lượng.

## 3. Lịch thực hiện đến 16/08

| Ngày | Mốc |
|---|---|
| 07/08 | Chốt API key, fixture, five-case rubric và PR 1–2 |
| 08–09/08 | Profile/evidence/LLM boundary (PR 3–5) |
| 10–11/08 | HITL, runner và API integration (PR 6–7) |
| 12–13/08 | UI hoàn chỉnh cho core flow (PR 8–9) |
| 14/08 | Manual live LLM cases, README, diagram, merge PR 10 |
| 15/08 | Rehearsal hai lần, quay video, fix bug qua PR nhỏ |
| 16/08 | Buffer, final review, release/tag và nộp bài |

## 4. Năm manual evaluation cases

Mỗi case phải lưu timestamp, profile aggregate input, model/rule output, reviewer
decision và DQ result; che API key và raw row values khi chụp/ghi video.

| Case | Input evidence | Expected meaningful outcome |
|---|---|---|
| E1 | `fare_amount.min < 0` | LLM đề xuất numeric range min 0; run bắt lỗi fare âm |
| E2 | `vendor_id.null_rate > 0` | LLM đề xuất not-null; run trả failed count 1 |
| E3 | `payment_type` có domain lạ | LLM đề xuất accepted values; run bắt value 99 |
| E4 | Duplicate fingerprint evidence | LLM đề xuất duplicate fingerprint hoặc không đề xuất nếu evidence chưa đủ; reviewer ghi nhận lý do |
| E5 | Profile sạch hoặc provider error | Không tự bịa serious rule; hoặc UI hiển thị recoverable LLM error, không tạo rule executable |

## 5. Video ba phút

1. 0:00–0:20: mục tiêu và safety boundary (aggregate-only).
2. 0:20–0:55: load fixture + profile.
3. 0:55–1:35: live LLM proposals và evidence references.
4. 1:35–2:05: approve/reject/edit, audit record.
5. 2:05–2:35: run approved checks và results/score.
6. 2:35–3:00: architecture diagram, five-case evidence và limitations.

Không quay API key, raw dataset row hoặc màn hình lỗi billing/credential.
