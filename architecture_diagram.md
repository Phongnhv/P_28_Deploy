# Architecture Diagram - RidePulse DQ

> **Trạng thái tài liệu:** Đã đối chiếu với source hiện tại ngày 24/08/2026. Phần “Kiến trúc hiện hành” dưới đây là nguồn sự thật. Các sơ đồ v1 ở phần phụ lục chỉ là đề xuất lịch sử và không được dùng để suy diễn tính năng đã triển khai.

## 0. Kiến trúc hiện hành

### 0.1. Stack và ranh giới hệ thống

| Lớp           | Implementation thực tế                                                                                            |
| -------------- | ------------------------------------------------------------------------------------------------------------------- |
| Frontend       | React + Vite + TypeScript, custom CSS, Recharts, React Markdown                                                     |
| API            | FastAPI; session cookie, CSRF, RBAC và dataset access                                                              |
| Workflow       | LangGraph với Graph 1, Graph 2 và Graph 3                                                                         |
| Persistence    | SQLAlchemy; SQLite ở local, tương thích database URL cấu hình                                                 |
| Test execution | dbt YAML deterministic +`dbt parse`; dbt CLI khi khả dụng; deterministic SQL metrics fallback ở local/dev/test |
| Anomaly        | Robust median/MAD z-score, cold-start static thresholds, business invariants, volume/freshness/execution detectors  |
| Report         | Markdown tiếng Việt; LLM structured generation hoặc deterministic fallback                                       |
| Artifact       | MinIO khi cấu hình hoặc local trace/report output                                                                |

### 0.2. Trạng thái capability

| Capability từng xuất hiện trong tài liệu cũ | Trạng thái trong source hiện tại                                  |
| ------------------------------------------------- | --------------------------------------------------------------------- |
| Isolation Forest                                  | **NOT FOUND**                                                   |
| ChromaDB/RAG retrieval cho hypothesis             | **NOT FOUND** — retrieval hiện là stub trả danh sách rỗng |
| Dagster orchestration                             | **NOT FOUND**                                                   |
| Slack/Email notification                          | **NOT FOUND**                                                   |
| Great Expectations execution                      | **NOT FOUND** trong pipeline Analysis Studio                    |
| LLM tự do sinh/sửa SQL Graph 2                  | **NOT FOUND** — Graph 2 dùng compiler/template deterministic  |
| React/Next.js + Ant Design                        | **NOT FOUND** — frontend là React/Vite/custom CSS             |

### 0.3. Luồng sản phẩm sau Graph 1

```mermaid
flowchart LR
    G1[Graph 1 Studio<br/>9 canonical nodes] --> Gate{Node 9 completed<br/>approved rules > 0}
    Gate -->|Analyze Graph 2 & 3| API[Protected Analysis API<br/>Idempotency-Key]
    API --> ORCH[Analysis workflow service]
    ORCH --> G2[Graph 2<br/>Generate → Validate → Run → Persist]
    G2 -->|success| G3[Graph 3<br/>Detect → Hypothesize → Persist → Report]
    G2 -->|failure| FAIL[FAILED<br/>Graph 3 nodes SKIPPED]
    G3 -->|success| DONE[COMPLETED]
    G3 -->|failure with Graph 2 data| PARTIAL[PARTIAL]
    DONE --> UI[Analysis Studio]
    PARTIAL --> UI
    FAIL --> UI
```

Graph 1 Studio chạy đủ 9 backend nodes. Node 9 giữ `proposed_rules`, `approved_rules` và các bộ đếm total/approved/edited/rejected/pending. Khi review, backend đồng bộ transactionally `RuleProposalModel`, snapshot `ProposedRuleModel` của chính Graph 1 run và `RuleVersionModel`. Graph 2 chỉ đọc approved-rule snapshot của run đó, không publish đè `active_rules` toàn cục.

### 0.4. Graph 2 — deterministic execution

```mermaid
flowchart LR
    TG[test_generator] --> VD[validate_dbt_project]
    VD -->|valid hoặc local dbt skipped| TR[test_runner]
    VD -->|invalid| VF[dbt_validation_failed]
    TR --> PR[persist_report]
    VF --> END1([END / FAILED])
    PR --> END2([END / Graph 3])
```

- `test_generator` sinh SQL check bằng template/contract và dbt YAML; không cho LLM tự do viết executable SQL.
- `validate_dbt_project` kiểm tra YAML và chạy `dbt parse`. Trạng thái `SKIPPED` phải được công khai nếu không có dbt executable; đây không phải dbt success.
- `test_runner` hỗ trợ `PASS`, `FAIL`, `ERROR`, `SKIPPED`, `RESULT_MISMATCH`; metrics chính lấy từ deterministic SQL checks.
- `persist_report` lưu `test_runs`, `test_results`, `dq_runs`, `dq_results` và JSON report.
- Analysis Studio hiển thị KPI, donut trạng thái, bar chart violation rate, metadata dbt và bảng chi tiết. Chi tiết lỗi chỉ trả source row IDs, evidence refs và error; không trả raw source values.

### 0.5. Graph 3 — anomaly và báo cáo

```mermaid
flowchart LR
    AD[anomaly_detector<br/>robust/static/business detectors] --> HA[hypothesis_agent<br/>LLM structured hoặc fallback]
    HA --> PA[persist_analysis]
    PA --> RW[report_writer<br/>Markdown LLM hoặc fallback]
    RW --> END([END])
```

- Signal được persist với detector name/version thật, score, reliability, observed value, baseline/history và evidence.
- Projection sang bảng Graph 2 chỉ gắn anomaly cho signal `target_type=RULE` có cùng `rule_id` và score `>= 0.70`.
- `report_writer` trả cả `steward_report_markdown`, `report_source=LLM|FALLBACK` và internal file path. API chỉ trả Markdown và tên file, không lộ absolute server path.
- Báo cáo có 8 phần: Executive Summary, Run Metadata, Rule Results, Anomaly Decision, Signals, Hypotheses, Priority Actions, Technical Notes.

### 0.6. Analysis API và persistence

| Endpoint                                            | Mục đích                                                             |
| --------------------------------------------------- | ----------------------------------------------------------------------- |
| `POST /api/v1/graph1-runs/{run_id}/analysis-runs` | Tạo/reuse run idempotent; chỉ STEWARD/ADMIN có quyền manage dataset |
| `GET /api/v1/analysis-runs/{id}`                  | Trạng thái tổng, phase, current node và IDs                         |
| `GET /api/v1/analysis-runs/{id}/nodes`            | Timeline 10 bước có status, duration và safe output summary         |
| `GET /api/v1/analysis-runs/{id}/result`           | Combined/partial Graph 2, Graph 3 và report                            |
| `GET /api/v1/analysis-runs/{id}/stream`           | SSE snapshots để UI theo dõi và resume                              |

`analysis_runs.graph1_run_id` là unique. `analysis_node_executions` lưu từng bước với `PENDING/RUNNING/SUCCEEDED/FAILED/SKIPPED`, timestamps, sequence, error và output summary đã loại SQL/YAML/raw path nhạy cảm.

### 0.7. Sequence Graph 1 → Analysis Studio

```mermaid
sequenceDiagram
    actor Steward
    participant UI as React/Vite UI
    participant API as FastAPI
    participant DB as SQLAlchemy DB
    participant G2 as Graph 2
    participant G3 as Graph 3

    Steward->>UI: Approve/Edit/Reject tại Node 9
    UI->>API: POST rule-review
    API->>DB: Đồng bộ 2 rule stores + RuleVersion
    API-->>UI: Graph 1 COMPLETED + counts
    Steward->>UI: Analyze Graph 2 & 3
    UI->>API: POST analysis-runs + Idempotency-Key
    API->>DB: Create/reuse analysis run + 10 node rows
    API-->>UI: 202 hoặc existing run
    UI->>API: SSE stream + GET partial result
    API->>G2: build_execution_graph().ainvoke(snapshot)
    G2->>DB: Persist test run/results/report
    API->>G3: build_anomaly_graph().ainvoke(test run)
    G3->>DB: Persist decision/signals/hypotheses/report
    API-->>UI: COMPLETED hoặc PARTIAL + combined result
    UI-->>Steward: Report + Graph 2 charts/table + Graph 3 diagnosis
```

---

## Phụ lục A — Sơ đồ v1 lịch sử (không phản ánh source hiện tại)

## 1. System Overview Diagram (v1)

```mermaid
graph TB
    %% Client Layer
    subgraph Client ["Client Layer (Presentation)"]
        Steward["👨‍💻 Data Steward<br/>(HITL Approval & Execution)"]
        Viewer["👁️ Viewer<br/>(Read-Only Dashboard)"]
        UI["Web Application Frontend<br/>(React + Ant Design 5.0)"]
        Steward --> UI
        Viewer --> UI
    end

    %% Backend Layer
    subgraph Backend ["FastAPI Backend Layer"]
        Gateway["API Gateway"]
        AuthMod["Auth & RBAC Controller"]
        DatasetMod["Dataset & Metadata Handler"]
        HITLMod["HITL Rule Workflow Controller"]
        AlertMod["Alert & Diagnostic Handler"]

        Gateway --> AuthMod
        Gateway --> DatasetMod
        Gateway --> HITLMod
        Gateway --> AlertMod
    end

    %% Core AI Agent Engine Layer
    subgraph AIAgent ["AI Agent Core (LangGraph Framework)"]
        Orchestrator["Agent Orchestrator"]
        MemoryMgr["Memory & Context Manager"]

        subgraph Agents ["Agent Modules"]
            Profiler["1. Profiler Agent<br/>(Dataset Survey & Metadata Stats)"]
            RuleProposer["2. Rule Proposer Agent<br/>(Generate Rules & Confidence Scores)"]
            TestGenerator["3. Test Generator Agent<br/>(dbt Core / Great Expectations Code)"]
            AnomalyDetector["4. Anomaly & Diagnosis Agent<br/>(Isolation Forest & ML Diagnostics)"]
        end

        Orchestrator --> MemoryMgr
        Orchestrator --> Profiler
        Orchestrator --> RuleProposer
        Orchestrator --> TestGenerator
        Orchestrator --> AnomalyDetector
    end

    %% Data & Execution Layer
    subgraph DataLayer ["Data & Execution Layer"]
        PostgresDB[("PostgreSQL Database<br/>(App Metadata, HITL Rules, Audit Logs)")]
        VectorDB[("ChromaDB Vector Store<br/>(Rule History & RAG Diagnosis Context)")]
        Dagster[("⚡ Dagster Orchestrator<br/>(Automated Pipeline & Scheduled Runs)")]
        TargetDW[("Target Data Warehouse / Databases<br/>(dich_vu_xe_trips, payments, etc.)")]
    end

    %% External Services Layer
    subgraph External ["External Services"]
        LLM["LLM Service<br/>(OpenAI GPT-4o / Gemini)"]
        Notifier["Notification Channels<br/>(Slack / Email Webhooks)"]
    end

    %% Inter-layer Connections
    UI -->|REST API / WebSockets| Gateway
    HITLMod -->|Trigger LangGraph Flow| Orchestrator

    %% Storage & Execution Connections
    MemoryMgr -->|Persist state| PostgresDB
    MemoryMgr -->|Semantic Search| VectorDB

    Profiler -->|Survey Schema & Stats| TargetDW
    TestGenerator -->|Deploy Generated Tests| Dagster
    Dagster -->|Run dbt/GX Tests| TargetDW
    Dagster -->|Report Failure / Anomaly| AlertMod

    RuleProposer -->|Prompt for Rule Ideas| LLM
    AnomalyDetector -->|Prompt for Root Cause Diagnosis| LLM

    AlertMod -->|Send Instant Alerts| Notifier
```

## 2. Agent Flow Diagram (v1) - LangGraph StateGraph

```mermaid
flowchart TD
    %% Entry Point
    START([🚀 Trigger: Dataset Selected / Scheduled Pipeline]) --> Orchestrator{🤖 Agent Orchestrator<br/>LangGraph State Router}

    %% 1. Profiler Flow
    Orchestrator -->|1. Route to Profiler| ProfilerNode[1. Profiler Sub-Agent]
    ProfilerNode --> QueryMetadataTool["Tool: Run DuckDB / SQL Stats Query"]
    QueryMetadataTool --> GenProfileStats["Generate Schema & Column Profile JSON"]
    GenProfileStats --> StateUpdate1["Update State: dataset_profile"]
    StateUpdate1 --> Orchestrator

    %% 2. Rule Proposer Flow
    Orchestrator -->|2. Route to Rule Proposer| ProposerNode[2. Rule Proposer Sub-Agent]
    ProposerNode --> VectorRAG["Tool: Query ChromaDB Vector Store (Past Rules)"]
    VectorRAG --> LLMRuleGen["LLM Reasoning: Suggest Rules & Confidence Scores"]
    LLMRuleGen --> StateUpdate2["Update State: proposed_rules"]
    StateUpdate2 --> HITLBreak

    %% 3. HITL Interrupt Point (Human-In-The-Loop)
    subgraph HITL_Node ["⏸️ Human-In-The-Loop (HITL Breakpoint)"]
        HITLBreak["Data Steward Review Modal<br/>(Approve / Reject / Edit)"]
        HITLBreak --> Decision{Steward Action}
        Decision -->|Edit Rule| EditRule["Update Threshold / Severity"]
        EditRule --> HITLBreak
        Decision -->|Reject| RejectRule["Mark Rule Rejected"]
        RejectRule --> HITLBreak
        Decision -->|Approve & Submit| ApproveRule["Mark Rules Approved"]
    end

    ApproveRule --> StateUpdate3["Update State: approved_rules"]
    StateUpdate3 --> Orchestrator

    %% 4. Test Generator Flow
    Orchestrator -->|3. Route to Test Generator| TestGenNode[3. Test Generator Sub-Agent]
    TestGenNode --> GenCodeTool["Tool: Render dbt Core / Great Expectations Test Code"]
    GenCodeTool --> SyntaxCheck{Syntax Valid?}
    SyntaxCheck -->|No - Syntax Error| TestRetryLoop["🤖 Agentic Loop: LLM Fixes Code Syntax"]
    TestRetryLoop --> GenCodeTool
    SyntaxCheck -->|Yes - Valid| DeployDagster["Tool: Push Test Suite to Dagster Orchestrator"]
    DeployDagster --> ExecPipeline["Run Scheduled dbt Test Pipeline"]
    ExecPipeline --> StateUpdate4["Update State: test_results"]
    StateUpdate4 --> Orchestrator

    %% 5. Anomaly & Diagnosis Flow
    Orchestrator -->|4. Route to Anomaly & Diagnosis| AnomalyNode[4. Anomaly & Diagnosis Sub-Agent]
    AnomalyNode --> MLTool["Tool: Run Isolation Forest / Z-Score ML Engine"]
    MLTool --> AnomalyCheck{Anomaly Detected?}
    AnomalyCheck -->|No Anomaly| AllPass["Generate Clean Data Health Summary"]
    AnomalyCheck -->|Yes - Anomaly Found| RootCauseLLM["LLM Reasoning: Analyze Logs & Query ChromaDB RAG"]
    RootCauseLLM --> GenDiagnosis["Generate Root Cause Diagnosis & SQL Fix Script"]
    GenDiagnosis --> AlertNotifier["Tool: Trigger Slack / Email Alert Notification"]

    AllPass --> END([🏁 End State / Update Dashboard])
    AlertNotifier --> END([🏁 End State / Update Dashboard])
```

## 2.2. Simplified Agent Flow Diagram (v2) - Sequential Flow

```mermaid
flowchart TD
    START([🚀 Bắt đầu: Chọn Dataset]) --> ProfilerNode[1. Profiler Agent]
  
    %% 1. Profiler
    ProfilerNode -->|Đọc Metadata & Quét SQL| ProfileStats[Bảng thống kê Dữ liệu & Metadata JSON]
  
    %% 2. Rule Proposer (LLM Reasoning)
    ProfileStats --> RuleProposerNode[2. LLM Rule Proposer]
    RuleProposerNode -->|LLM Reasoning & Suggest Rules| ProposedRules[Đề xuất bộ Rule Chất lượng]
  
    %% 3. HITL (Human-In-The-Loop)
    ProposedRules --> HITLNode["⏸️ Human-In-The-Loop (HITL)"]
    subgraph HITL_Section ["Vai trò Data Steward"]
        HITLNode -->|Duyệt / Từ chối / Chỉnh sửa| ApprovedRules[Bộ Rule đã phê duyệt]
    end
  
    %% 4. Test Generator
    ApprovedRules --> TestGenNode[3. Test Generator Agent]
    TestGenNode -->|Sinh mã kiểm thử dbt / GX| TestScripts[Bài test sinh tự động]
  
    %% 5. Test Runner & Anomaly Detector
    TestScripts --> TestRunnerNode[4. Thực thi kiểm thử]
    TestRunnerNode -->|Chạy test trên Database| TestResults[Kết quả kiểm thử]
    TestResults --> AnomalyCheckNode{5. Phát hiện Bất thường Anomaly?}
  
    %% 6. Diagnostic & Report
    AnomalyCheckNode -->|Có lỗi / Bất thường| DiagnoseAnomaly[Chẩn đoán nguyên nhân & Phân tích]
    AnomalyCheckNode -->|Bình thường| DashboardNode[6. Dashboard & Báo cáo]
    DiagnoseAnomaly --> DashboardNode
  
    DashboardNode --> END([🏁 Kết thúc: Gửi thông báo tới Data Steward])
```

## 2.3. Agent Flow Diagram (v3) - Three-Run LangGraph Architectures (New)

### 2.3.1. Run 1: Proposal Graph (Luồng đề xuất Rules)

Graph này chịu trách nhiệm khảo sát cấu trúc bảng, xây dựng Data Dictionary, lấy ý kiến xác nhận Semantic Contract của Data Steward, sau đó cho LLM đề xuất các rules chất lượng phù hợp với bảng dữ liệu.

```mermaid
flowchart TD
    %% Styling
    classDef startEnd fill:#f4f5f7,stroke:#5c6bc0,stroke-width:2px,color:#333;
    classDef stateNode fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px,color:#333;
    classDef hitlNode fill:#ffe082,stroke:#ffb300,stroke-width:2px,color:#333;
    classDef routeNode fill:#e0f2f1,stroke:#009688,stroke-width:2px,color:#333;

    START([🚀 Start: run_proposal_graph])
    RouteEntry{"Route Entry (Semantic Status)"}
  
    RawProfiler["raw_profiler_node<br/>(Run Profiler on Dataset)"]
    ProfilerDigest["profiler_digest_node<br/>(Analyze profile stats)"]
    DataDictGen["data_dictionary_generator_node<br/>(Generate Data Dictionary)"]
    DatasetUnder["dataset_understanding_node<br/>(Analyze semantic meaning)"]
    HITLSemantic["⏸️ hitl_semantic_gate_node<br/>(HITL Semantic Contract Review)"]
  
    RuleCandBuilder["rule_candidate_builder_node<br/>(Build candidate rule list)"]
    PromptCust["prompt_customizer_node<br/>(Customize system prompt for LLM)"]
    RuleProposer["rule_proposer_node<br/>(LLM generates rule suggestions)"]
    HITLGate["hitl_gate_node<br/>(Persist Proposed Rules)"]
  
    END([🏁 END])

    class START,END startEnd;
    class RawProfiler,ProfilerDigest,DataDictGen,DatasetUnder,RuleCandBuilder,PromptCust,RuleProposer stateNode;
    class HITLSemantic,HITLGate hitlNode;
    class RouteEntry routeNode;

    %% Edges
    START --> RouteEntry
    RouteEntry -->|"confirmed / auto_confirm"| RuleCandBuilder
    RouteEntry -->|"draft / none"| RawProfiler

    RawProfiler --> CondProfiler{"Should Continue?"}
    CondProfiler -->|"error / pause"| END
    CondProfiler -->|success| ProfilerDigest

    ProfilerDigest --> CondDigest{"Has Normalized Dict?"}
    CondDigest -->|error| END
    CondDigest -->|yes| DatasetUnder
    CondDigest -->|no| DataDictGen

    DataDictGen --> CondDict{"Should Continue?"}
    CondDict -->|"error / pause"| END
    CondDict -->|success| DatasetUnder

    DatasetUnder --> CondUnder{"Should Continue?"}
    CondUnder -->|"error / pause"| END
    CondUnder -->|success| HITLSemantic

    HITLSemantic --> CondSemantic{"Should Continue?"}
    CondSemantic -->|pause_reason: AWAITING_SEMANTIC_REVIEW| END
    CondSemantic -->|"success / confirmed"| RuleCandBuilder

    RuleCandBuilder --> CondBuilder{"Should Continue?"}
    CondBuilder -->|"error / pause"| END
    CondBuilder -->|success| PromptCust

    PromptCust --> CondPrompt{"Should Continue?"}
    CondPrompt -->|"error / pause"| END
    CondPrompt -->|success| RuleProposer

    RuleProposer --> HITLGate
    HITLGate --> END
```

### 2.3.2. Run 2: Execution Graph (Luồng thực thi kiểm thử)

Graph này thực hiện sinh mã kiểm thử dbt, biên dịch/validate cú pháp, chạy bộ kiểm thử trên cơ sở dữ liệu đích, và lưu trữ kết quả chạy test.

```mermaid
flowchart TD
    %% Styling
    classDef startEnd fill:#f4f5f7,stroke:#5c6bc0,stroke-width:2px,color:#333;
    classDef stateNode fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px,color:#333;
    classDef errorNode fill:#ffebee,stroke:#ef5350,stroke-width:2px,color:#333;
    classDef routeNode fill:#e0f2f1,stroke:#009688,stroke-width:2px,color:#333;

    START([🚀 Start: run_execution_graph])
  
    TestGen["test_generator_node<br/>(Render dbt Core tests)"]
    ValidateDbt["validate_dbt_project_node<br/>(Validate project compile)"]
    RouteValidation{"Validate Success?"}
  
    DbtFailed["dbt_validation_failed<br/>(Fail run & persist error)"]
    TestRunner["test_runner_node<br/>(Run dbt test suite)"]
    PersistReport["persist_report_node<br/>(Persist results & report)"]
  
    END([🏁 END])

    class START,END startEnd;
    class TestGen,ValidateDbt,TestRunner,PersistReport stateNode;
    class DbtFailed errorNode;
    class RouteValidation routeNode;

    %% Edges
    START --> TestGen
    TestGen --> ValidateDbt
    ValidateDbt --> RouteValidation
  
    RouteValidation -->|Yes: run| TestRunner
    RouteValidation -->|No: fail| DbtFailed
  
    DbtFailed --> END
    TestRunner --> PersistReport
    PersistReport --> END
```

### 2.3.3. Run 3: Anomaly Graph (Luồng phát hiện & Chẩn đoán bất thường)

Graph này chạy sau khi hoàn tất kiểm thử, phát hiện các tín hiệu bất thường bằng mô hình học máy (Isolation Forest, Z-Score), suy luận tìm nguyên nhân lỗi nhờ RAG, đề xuất giả thuyết và viết báo cáo chi tiết cho Steward.

```mermaid
flowchart TD
    %% Styling
    classDef startEnd fill:#f4f5f7,stroke:#5c6bc0,stroke-width:2px,color:#333;
    classDef stateNode fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px,color:#333;

    START([🚀 Start: run_anomaly_graph])
  
    AnomalyDetect["anomaly_detector_node<br/>(Isolation Forest & ML baseline)"]
    HypothesisAgent["hypothesis_agent<br/>(steward_insights_node / RAG reasoning)"]
    PersistAnalysis["persist_analysis_node<br/>(Save signals & hypotheses)"]
    ReportWriter["report_writer_node<br/>(Write markdown report)"]
  
    END([🏁 END])

    class START,END startEnd;
    class AnomalyDetect,HypothesisAgent,PersistAnalysis,ReportWriter stateNode;

    %% Edges
    START --> AnomalyDetect
    AnomalyDetect --> HypothesisAgent
    HypothesisAgent --> PersistAnalysis
    PersistAnalysis --> ReportWriter
    ReportWriter --> END
```

### 2.3.4. Quy trình tuần tự tương tác (Sequential Workflow)

Sơ đồ mô tả sự phối hợp tuần tự giữa Data Steward, Giao diện Web, ba Agent StateGraph (Run 1, 2, 3) và Cơ sở dữ liệu ứng dụng.

```mermaid
sequenceDiagram
    autonumber
    actor Steward as 👨‍💻 Data Steward
    participant UI as Web UI / API Gateway
    participant Run1 as 🤖 Proposal Graph (Run 1)
    participant Run2 as ⚙️ Execution Graph (Run 2)
    participant Run3 as 🧠 Anomaly Graph (Run 3)
    participant DB as 💾 PostgreSQL DB

    %% Run 1
    Steward->>UI: Chọn Dataset & Bắt đầu phân tích
    UI->>Run1: Kích hoạt run_proposal_graph
    activate Run1
    Run1->>Run1: Chạy raw_profiler_node & profiler_digest_node
    Run1->>Run1: Chạy dataset_understanding_node
  
    alt Chưa có Semantic Contract
        Run1->>DB: Lưu draft Semantic Contract & trạng thái AWAITING_SEMANTIC_REVIEW
        Run1-->>UI: Yêu cầu phê duyệt Semantic Contract
        Steward->>UI: Duyệt Semantic Contract
        UI->>Run1: Chạy lại với trạng thái confirmed
    end
  
    Run1->>Run1: Chạy rule_candidate_builder_node & prompt_customizer_node
    Run1->>Run1: LLM đề xuất rules (rule_proposer_node)
    Run1->>DB: Lưu các rules nháp (status: PENDING) qua hitl_gate_node
    deactivate Run1
  
    %% HITL Rules Review
    Steward->>UI: Xem proposed rules, Duyệt / Sửa / Từ chối
    UI->>DB: Cập nhật status rules (APPROVED / REJECTED)
    Steward->>UI: Publish Approved Rules sang Active Ruleset
    UI->>DB: Kích hoạt active_rules

    %% Run 2
    Steward->>UI: Chạy Test Pipeline (hoặc tự động chạy định kỳ)
    UI->>Run2: Kích hoạt run_execution_graph
    activate Run2
    Run2->>DB: Lấy active_rules từ DB
    Run2->>Run2: Sinh code dbt (test_generator_node)
    Run2->>Run2: Biên dịch và Validate dbt project
  
    alt Compile Validation Hợp lệ
        Run2->>Run2: Chạy dbt test suite trên target DB (test_runner_node)
        Run2->>DB: Lưu kết quả test (persist_report_node)
    else Lỗi Compile / Cú pháp
        Run2->>DB: Lưu trạng thái FAILED (dbt_validation_failed)
    end
    deactivate Run2

    %% Run 3
    UI->>Run3: Kích hoạt run_anomaly_graph (tự động sau khi chạy test)
    activate Run3
    Run3->>Run3: Phân tích ML, Isolation Forest (anomaly_detector_node)
    Run3->>Run3: LLM chẩn đoán nguyên nhân lỗi RAG (hypothesis_agent)
    Run3->>DB: Lưu trữ Anomaly Signals & Giả thuyết chẩn đoán
    Run3->>Run3: Viết Báo cáo Steward Markdown (report_writer_node)
    deactivate Run3

    Run3-->>Steward: Gửi Báo cáo (Steward Report) & Alert Slack
```

## 3. Component Details

| Component          | Technology                       | Purpose                                                                  |
| ------------------ | -------------------------------- | ------------------------------------------------------------------------ |
| Frontend           | React / Next.js + Ant Design 5.0 | User interface for Data Steward (HITL) and Viewer                        |
| Backend            | FastAPI                          | REST API Server, RBAC, Gateway & Agent integration                       |
| Agent Engine       | LangGraph                        | AI Agent Orchestration (Profiler, Proposer, Generator, Anomaly Detector) |
| Scheduler / Runner | Dagster                          | Automated pipeline execution, scheduled test execution                   |
| Application DB     | PostgreSQL                       | Primary storage for metadata, rules status, test logs, user roles        |
| Vector Store       | ChromaDB                         | Embeddings, rule history & diagnosis RAG context                         |
| Target Data        | Snowflake / BigQuery / Postgres  | Operational databases (`dich_vu_xe_trips`, etc.)                       |
| LLM Service        | OpenAI / Gemini                  | Reasoning engine for rule generation & root cause diagnosis              |
