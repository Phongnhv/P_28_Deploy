# Architecture Diagram - RidePulse DQ

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

## 3. Component Details

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Frontend | React / Next.js + Ant Design 5.0 | User interface for Data Steward (HITL) and Viewer |
| Backend | FastAPI | REST API Server, RBAC, Gateway & Agent integration |
| Agent Engine | LangGraph | AI Agent Orchestration (Profiler, Proposer, Generator, Anomaly Detector) |
| Scheduler / Runner | Dagster | Automated pipeline execution, scheduled test execution |
| Application DB | PostgreSQL | Primary storage for metadata, rules status, test logs, user roles |
| Vector Store | ChromaDB | Embeddings, rule history & diagnosis RAG context |
| Target Data | Snowflake / BigQuery / Postgres | Operational databases (`dich_vu_xe_trips`, etc.) |
| LLM Service | OpenAI / Gemini | Reasoning engine for rule generation & root cause diagnosis |


