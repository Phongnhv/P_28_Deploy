# Cấu trúc Dự án & Danh sách File cần viết (Project Structure & File Inventory)

Tài liệu này xác định chi tiết cây thư mục dự án **RidePulse DQ** và danh sách đầy đủ các file nguồn (source code files) cần triển khai dựa trên các bản thiết kế kiến trúc (`architecture_diagram.md`), quy trình Sub-Agent (`subagents_workflow.md`) và yêu cầu đề tài (`DETAI.md`).

---

## 1. Cây thư mục tổng quan (Directory Tree)

```text
P-028/
├── docs/                             # Tài liệu dự án (PRD, Architecture, Workflows)
│   ├── architecture_diagram.md       # Sơ đồ System Overview & Agent Flow
│   ├── subagents_workflow.md         # Quy trình chi tiết 4 Sub-Agents
│   ├── project_structure.md          # [File này] Cấu trúc file dự án
│   └── PRODUCT_DESIGN_REPORT.md      # Báo cáo thiết kế UX/UI
├── src/                              # Backend Core (FastAPI + LangGraph)
│   ├── main.py                       # FastAPI Entrypoint & App Config
│   ├── config.py                     # Quản lý cấu hình & Biến môi trường (.env)
│   ├── agents/                       # AI Agent Core (LangGraph Multi-Agent)
│   │   ├── stat;e.py                  # Định nghĩa AgentState trong LangGraph
│   │   ├── graph.py                  # Xây dựng StateGraph, Nodes, Edges & HITL
│   │   ├── nodes/                    # Logics của các Agent Nodes
│   │   │   ├── orchestrator_node.py  # Router điều phối State
│   │   │   ├── profiler_node.py      # Node của Profiler Sub-Agent
│   │   │   ├── rule_proposer_node.py # Node của Rule Proposer Sub-Agent
│   │   │   ├── test_generator_node.py# Node của Test Generator Sub-Agent
│   │   │   └── anomaly_detector_node.py # Node của Anomaly & Diagnosis Sub-Agent
│   │   └── tools/                    # Các Tools cho Agent sử dụng
│   │       ├── db_profiler_tool.py   # Tool quét metadata & stats SQL
│   │       ├── chroma_rag_tool.py    # Tool RAG tìm kiếm rule/lỗi lịch sử
│   │       ├── dbt_generator_tool.py # Tool sinh mã dbt test YAML/SQL
│   │       ├── ml_anomaly_tool.py    # Tool ML Isolation Forest / Z-Score
│   │       └── alert_tool.py         # Tool bắn notification Slack/Email
│   ├── api/                          # FastAPI REST API Routes
│   │   ├── dependencies.py           # Dependency Injection (DB, Services)
│   │   └── routes/                   # Endpoint Controllers
│   │       ├── datasets.py           # API kết nối & profiling dataset
│   │       ├── rules.py              # API HITL (Approve/Reject/Edit rules)
│   │       ├── executions.py         # API quản lý chạy pipeline kiểm thử
│   │       ├── anomalies.py          # API xem Anomaly & AI Root Cause Diagnosis
│   │       └── eval.py               # API xem chỉ số Precision/Recall/F1
│   ├── services/                     # Business Logic Services
│   │   ├── dataset_service.py        # Handler trích xuất metadata từ DB Target
│   │   ├── dagster_service.py        # Giao tiếp với Dagster GraphQL API
│   │   ├── llm_service.py            # LangChain / OpenAI / Gemini Wrapper
│   │   └── chroma_service.py         # Quản lý ChromaDB Client & Embeddings
│   └── models/                       # Data Models & Schemas
│       ├── db_models.py              # SQLAlchemy Models (PostgreSQL App DB)
│       └── schemas.py                # Pydantic V2 Schemas cho API
├── orchestration/                    # Dagster Pipelines & Asset Definitions
│   ├── definitions.py                # Repositories & Definitions của Dagster
│   ├── assets/                       # Assets chạy dbt tests & thu thập log
│   │   └── dq_assets.py              # Asset kiểm thử Data Quality
│   └── schedules.py                  # Lập lịch chạy định kỳ (Dagster Schedules)
├── dbt_project/                      # Project dbt lưu trữ các test sinh tự động
│   ├── dbt_project.yml               # Cấu hình dbt Project
│   ├── models/                       # Models dbt cơ sở
│   └── tests/                        # Thư mục lưu các test case .yml do Agent sinh
├── eval/                             # Đo lường & Đánh giá mô hình ML Anomaly
│   ├── eval_metrics.py               # Thư viện tính Precision, Recall, F1-Score
│   └── run_eval.py                   # Script chạy benchmark trên dữ liệu gán nhãn
├── tests/                            # Automated Testing Unit & Integration
│   ├── test_agents.py                # Test luồng chạy LangGraph Agent
│   ├── test_api.py                   # Test các endpoints FastAPI
│   └── test_ml_anomaly.py            # Test mô hình ML Anomaly Detection
├── ui_test/                          # Frontend Prototype (React + Ant Design 5.0)
│   ├── index.html                    # Single Page App Layout
│   └── js/                           # Components & State Handlers
├── docker-compose.yml                # Compose chạy Postgres, ChromaDB, Dagster, App
├── Dockerfile                        # Containerize Backend FastAPI
├── requirements.txt                  # Danh sách thư viện Python
└── README.md                         # Hướng dẫn chạy dự án
```

---

## 2. Danh sách Chi tiết các File cần viết theo từng Module

### 🤖 A. Core AI Agent Engine (`src/agents/`)
| File Path | Vai trò & Nội dung cần triển khai |
| :--- | :--- |
| [src/agents/state.py](file:///d:/ai_thuc_chien/P-028/src/agents/state.py) | Định nghĩa `AgentState` (TypedDict) lưu trữ toàn bộ context chạy luồng (dataset_id, profile, proposed_rules, approved_rules, execution_results, anomalies). |
| [src/agents/graph.py](file:///d:/ai_thuc_chien/P-028/src/agents/graph.py) | Xây dựng `StateGraph`, kết nối các nodes, định nghĩa `interrupt_before` tại node HITL để dừng chờ người dùng duyệt rule. |
| [src/agents/nodes/orchestrator_node.py](file:///d:/ai_thuc_chien/P-028/src/agents/nodes/orchestrator_node.py) | Node phân luồng trạng thái, kiểm tra điều kiện chuyển tiếp bước tiếp theo. |
| [src/agents/nodes/profiler_node.py](file:///d:/ai_thuc_chien/P-028/src/agents/nodes/profiler_node.py) | Gọi `db_profiler_tool`, thu thập chỉ số stats và dùng LLM tổng hợp `dataset_profile`. |
| [src/agents/nodes/rule_proposer_node.py](file:///d:/ai_thuc_chien/P-028/src/agents/nodes/rule_proposer_node.py) | Gọi `chroma_rag_tool`, kết hợp LLM để đề xuất danh sách rules kèm điểm tin cậy (Confidence score). |
| [src/agents/nodes/test_generator_node.py](file:///d:/ai_thuc_chien/P-028/src/agents/nodes/test_generator_node.py) | Biên dịch `approved_rules` thành mã dbt YML, thực hiện Agentic Loop lặp tự sửa lỗi nếu sai cú pháp. |
| [src/agents/nodes/anomaly_detector_node.py](file:///d:/ai_thuc_chien/P-028/src/agents/nodes/anomaly_detector_node.py) | Chạy `ml_anomaly_tool` tìm điểm bất thường, dùng LLM + RAG giải thích nguyên nhân gốc rễ (Root Cause Diagnosis). |
| [src/agents/tools/db_profiler_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/db_profiler_tool.py) | Thực thi các truy vấn SQL thống kê (Null count, Min/Max, Distinct) bằng DuckDB/SQLAlchemy. |
| [src/agents/tools/chroma_rag_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/chroma_rag_tool.py) | Đăng ký & truy vấn vector embeddings cho lịch sử rule và log sự cố lỗi. |
| [src/agents/tools/dbt_generator_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/dbt_generator_tool.py) | Sinh file YAML/SQL tương thích dbt Core từ cấu trúc JSON Rule Specs. |
| [src/agents/tools/ml_anomaly_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/ml_anomaly_tool.py) | Bọc mô hình `IsolationForest` & thuật toán `Z-Score` từ `scikit-learn` thành Tool. |
| [src/agents/tools/alert_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/alert_tool.py) | Gửi webhook cảnh báo sự cố dữ liệu tới Slack/Email. |

---

### 🌐 B. FastAPI Backend Layer (`src/api/` & `src/services/`)
| File Path | Vai trò & Nội dung cần triển khai |
| :--- | :--- |
| [src/main.py](file:///d:/ai_thuc_chien/P-028/src/main.py) | Khởi tạo app FastAPI, đăng ký Router, cấu hình CORS Middleware và xử lý sự kiện Startup/Shutdown. |
| [src/config.py](file:///d:/ai_thuc_chien/P-028/src/config.py) | Sử dụng `pydantic-settings` đọc các cấu hình từ file `.env` (DB URL, LLM API Key, Chroma host). |
| [src/models/db_models.py](file:///d:/ai_thuc_chien/P-028/src/models/db_models.py) | Khai báo các bảng PostgreSQL: `User`, `Dataset`, `Rule`, `RuleExecution`, `AnomalyAlert`. |
| [src/models/schemas.py](file:///d:/ai_thuc_chien/P-028/src/models/schemas.py) | Khai báo Pydantic Request/Response DTOs cho các API endpoints. |
| [src/api/dependencies.py](file:///d:/ai_thuc_chien/P-028/src/api/dependencies.py) | Quản lý DB Session, Authentication RBAC (Steward/Viewer) và Service Injectors. |
| [src/api/routes/datasets.py](file:///d:/ai_thuc_chien/P-028/src/api/routes/datasets.py) | APIs kết nối Data Source, danh sách bảng, trigger Profiling. |
| [src/api/routes/rules.py](file:///d:/ai_thuc_chien/P-028/src/api/routes/rules.py) | APIs cho Data Steward thực hiện HITL (Approve, Reject, Edit rule thresholds). |
| [src/api/routes/executions.py](file:///d:/ai_thuc_chien/P-028/src/api/routes/executions.py) | APIs kích hoạt pipeline chạy test và truy vấn console log thực thi từ Dagster. |
| [src/api/routes/anomalies.py](file:///d:/ai_thuc_chien/P-028/src/api/routes/anomalies.py) | APIs trả về danh sách đốm đỏ Anomaly & chi tiết AI Diagnosis Modal. |
| [src/api/routes/eval.py](file:///d:/ai_thuc_chien/P-028/src/api/routes/eval.py) | APIs cung cấp chỉ số đánh giá mô hình ML (Precision, Recall, F1-Score). |
| [src/services/dataset_service.py](file:///d:/ai_thuc_chien/P-028/src/services/dataset_service.py) | Service kết nối Read-Only vào kho dữ liệu Target (`dich_vu_xe_trips`...) để trích xuất schema. |
| [src/services/dagster_service.py](file:///d:/ai_thuc_chien/P-028/src/services/dagster_service.py) | Client gửi request GraphQL sang Dagster Daemon để kích hoạt DAG run. |
| [src/services/llm_service.py](file:///d:/ai_thuc_chien/P-028/src/services/llm_service.py) | Bọc kết nối với OpenAI GPT-4o / Gemini API với retry logic và streaming handler. |
| [src/services/chroma_service.py](file:///d:/ai_thuc_chien/P-028/src/services/chroma_service.py) | Quản lý ChromaDB collection, embedding model và hàm tìm kiếm RAG. |

---

### ⚡ C. Automation & Orchestration (`orchestration/` & `dbt_project/`)
| File Path | Vai trò & Nội dung cần triển khai |
| :--- | :--- |
| [orchestration/definitions.py](file:///d:/ai_thuc_chien/P-028/orchestration/definitions.py) | Khai báo các Dagster Definitions, Assets, Jobs và Sensors. |
| [orchestration/assets/dq_assets.py](file:///d:/ai_thuc_chien/P-028/orchestration/assets/dq_assets.py) | Dagster Software-Defined Asset thực thi các lệnh `dbt test` và ghi kết quả vào PostgreSQL. |
| [orchestration/schedules.py](file:///d:/ai_thuc_chien/P-028/orchestration/schedules.py) | Cấu hình lịch chạy kiểm thử tự động (ví dụ: Hàng ngày vào lúc 02:00 AM). |
| [dbt_project/dbt_project.yml](file:///d:/ai_thuc_chien/P-028/dbt_project/dbt_project.yml) | Cấu hình dbt Project tương thích với PostgreSQL / Snowflake target. |

---

### 📊 D. Evaluation & Testing (`eval/` & `tests/`)
| File Path | Vai trò & Nội dung cần triển khai |
| :--- | :--- |
| [eval/eval_metrics.py](file:///d:/ai_thuc_chien/P-028/eval/eval_metrics.py) | Thuật toán tính toán Precision, Recall, F1-Score từ dữ liệu gán nhãn lỗi thực tế. |
| [eval/run_eval.py](file:///d:/ai_thuc_chien/P-028/eval/run_eval.py) | Script kích hoạt benchmark đánh giá độ chính xác cảnh báo (giảm false positive). |
| [tests/test_agents.py](file:///d:/ai_thuc_chien/P-028/tests/test_agents.py) | Unit test kiểm thử luồng chạy của từng Node và State Graph trong LangGraph. |
| [tests/test_api.py](file:///d:/ai_thuc_chien/P-028/tests/test_api.py) | Integration test cho toàn bộ hệ thống API FastAPI. |
| [tests/test_ml_anomaly.py](file:///d:/ai_thuc_chien/P-028/tests/test_ml_anomaly.py) | Unit test đo đạc mô hình ML Isolation Forest trên dữ liệu giả lập. |

---

## 3. Lộ trình Triển khai (Implementation Roadmap)

1. **Giai đoạn 1 (Backend Core & Agent Foundation):**
   - Viết `src/config.py`, `src/models/`, `src/services/dataset_service.py`.
   - Hoàn thiện `src/agents/state.py`, `src/agents/tools/`, `src/agents/nodes/` và `src/agents/graph.py`.
2. **Giai đoạn 2 (API & HITL Workflow):**
   - Viết các router API trong `src/api/routes/` (`datasets.py`, `rules.py`, `executions.py`).
   - Đảm bảo luồng HITL cho phép Data Steward Approve/Reject/Edit rule.
3. **Giai đoạn 3 (Dagster Integration & ML Anomaly):**
   - Hoàn thiện `orchestration/` và `dbt_generator_tool.py`.
   - Triển khai `ml_anomaly_tool.py` và API chẩn đoán nguyên nhân gốc (`anomalies.py`).
4. **Giai đoạn 4 (Eval, UI Integration & Polish):**
   - Hoàn thiện module `eval/` đo lường Precision/Recall/F1.
   - Kết nối UI Prototype (`ui_test/`) với Backend FastAPI.
