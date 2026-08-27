import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Graph1NodeExecution, Graph1NodeStatus } from "../../types";

type Row = Record<string, unknown>;

export const META: Record<string, [string, string]> = {
  raw_profiler: ["Raw profiler", "Profile thật của dataset đã upload."],
  profiler_digest: ["Profiler digest", "Tín hiệu chất lượng chuẩn hóa cho agent."],
  data_dictionary_generator: ["Data dictionary", "Từ điển dữ liệu được LLM suy luận."],
  dataset_understanding: ["Dataset understanding", "Semantic Contract từ profile và dictionary."],
  hitl_semantic_gate: ["Semantic review", "Steward xác nhận ngữ nghĩa."],
  rule_candidate_builder: ["Rule candidates", "Ứng viên rule deterministic có evidence."],
  prompt_customizer: ["Prompt customizer", "Prompt chuyên biệt theo dataset."],
  rule_proposer: ["Rule proposer", "Rule do LLM đề xuất."],
  hitl_gate: ["Rule approval", "Steward duyệt rules cuối."],
};

export interface DisplayStage {
  key: string;
  canonicalLabel: string;
  title: string;
  description: string;
  status: Graph1NodeStatus;
  nodes: Graph1NodeExecution[];
}

const STAGE_KEYS = [
  "data_dictionary_generator", "rule_candidate_builder", "prompt_customizer",
  "rule_proposer", "hitl_gate",
];

function profileStatus(nodes: Graph1NodeExecution[]): Graph1NodeStatus {
  if (nodes.some((node) => node.status === "FAILED")) return "FAILED";
  if (nodes.length === 2 && nodes.every((node) => node.status === "SUCCEEDED")) return "SUCCEEDED";
  if (nodes.some((node) => node.status === "RUNNING" || node.status === "SUCCEEDED")) return "RUNNING";
  return "PENDING";
}

function understandingStatus(nodes: Graph1NodeExecution[]): Graph1NodeStatus {
  if (nodes.some((node) => node.status === "FAILED")) return "FAILED";
  const gate = nodes.find((node) => node.node_key === "hitl_semantic_gate");
  if (gate?.status === "WAITING_REVIEW") return "WAITING_REVIEW";
  if (nodes.length === 2 && nodes.every((node) => node.status === "SUCCEEDED")) return "SUCCEEDED";
  if (nodes.length && nodes.every((node) => node.status === "SKIPPED")) return "SKIPPED";
  if (nodes.some((node) => ["RUNNING", "SUCCEEDED"].includes(node.status))) return "RUNNING";
  return "PENDING";
}

export function buildDisplayStages(nodes: Graph1NodeExecution[]): DisplayStage[] {
  const profileNodes = ["raw_profiler", "profiler_digest"]
    .map((key) => nodes.find((node) => node.node_key === key))
    .filter((node): node is Graph1NodeExecution => Boolean(node));
  const stages: DisplayStage[] = [{
    key: "profile_info",
    canonicalLabel: "01+02",
    title: "Profile_Infor",
    description: "Hồ sơ dữ liệu và tín hiệu chất lượng trong một màn hình.",
    status: profileStatus(profileNodes),
    nodes: profileNodes,
  }];
  const understandingNodes = ["dataset_understanding", "hitl_semantic_gate"]
    .map((key) => nodes.find((node) => node.node_key === key))
    .filter((node): node is Graph1NodeExecution => Boolean(node));
  for (const key of STAGE_KEYS) {
    const node = nodes.find((item) => item.node_key === key);
    if (!node) continue;
    stages.push({
      key,
      canonicalLabel: String(node.position).padStart(2, "0"),
      title: META[key]?.[0] ?? key,
      description: META[key]?.[1] ?? "",
      status: node.status,
      nodes: [node],
    });
    if (key === "data_dictionary_generator" && understandingNodes.length) {
      stages.push({
        key: "understanding_semantic",
        canonicalLabel: "04+05",
        title: "Understanding_semantic",
        description: "Hiểu dataset, rà soát và xác nhận Semantic Contract trong một stage.",
        status: understandingStatus(understandingNodes),
        nodes: understandingNodes,
      });
    }
  }
  return stages;
}

export function nodeKeyToStageKey(nodeKey: string) {
  if (["raw_profiler", "profiler_digest"].includes(nodeKey)) return "profile_info";
  if (["dataset_understanding", "hitl_semantic_gate"].includes(nodeKey)) return "understanding_semantic";
  return nodeKey;
}

export function stageDuration(stage: DisplayStage) {
  const starts = stage.nodes.flatMap((node) => node.started_at ? [new Date(node.started_at).getTime()] : []);
  if (!starts.length) return "—";
  const ends = stage.nodes.flatMap((node) => node.completed_at ? [new Date(node.completed_at).getTime()] : []);
  const seconds = Math.max(0, Math.round(((ends.length ? Math.max(...ends) : Date.now()) - Math.min(...starts)) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

const record = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const records = (value: unknown): Row[] => Array.isArray(value) ? value.filter((item): item is Row => Boolean(item && typeof item === "object" && !Array.isArray(item))) : [];
const scalar = (value: unknown, fallback = "—") => value === undefined || value === null || value === "" ? fallback : String(value);
const human = (value: string) => value.replaceAll("_", " ");
const compactNumber = (value: unknown) => typeof value === "number" ? new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(value) : scalar(value);
const percent = (value: unknown) => {
  if (typeof value !== "number") return scalar(value);
  return `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(value)}%`;
};
const confidencePercent = (value: unknown) => typeof value === "number" ? percent(value <= 1 ? value * 100 : value) : scalar(value);
const array = (value: unknown) => Array.isArray(value) ? value : [];

function Technical({ value, label = "Technical output" }: { value: unknown; label?: string }) {
  return <details className="g1-technical"><summary>{label}</summary><pre>{JSON.stringify(value ?? {}, null, 2)}</pre></details>;
}

function Tags({ values, kind = "neutral" }: { values: unknown[]; kind?: string }) {
  const unique = Array.from(new Set(values.filter((value) => value !== null && value !== undefined).map(String)));
  if (!unique.length) return <span className="g1-muted">—</span>;
  return <div className="g1-tags">{unique.map((value) => {
    const tone = /no_null|unique|valid|complete/i.test(value) ? "good" : /null|negative|outlier|warning|invalid/i.test(value) ? "warning" : /constraint|primary|foreign/i.test(value) ? "constraint" : kind;
    return <span className={`g1-tag ${tone}`} key={value}>{human(value)}</span>;
  })}</div>;
}

function ValueCell({ value }: { value: unknown }) {
  const text = Array.isArray(value) ? value.map((item) => scalar(item)).join(", ") : typeof value === "object" && value ? Object.entries(record(value)).map(([key, item]) => `${human(key)}: ${scalar(item)}`).join(" · ") : scalar(value);
  return <span className="g1-cell-text">{text}</span>;
}

function Table({ rows, columns }: { rows: Row[]; columns: Array<[string, string, (value: unknown, row: Row) => ReactNode]> }) {
  if (!rows.length) return <Empty text="Không có bản ghi để hiển thị." />;
  return <div className="g1-table-wrap"><table className="g1-table"><thead><tr>{columns.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.name ?? row.column ?? index)}>{columns.map(([key, label, render]) => <td data-label={label} key={key}>{render(row[key], row)}</td>)}</tr>)}</tbody></table></div>;
}

function Metric({ label, value, hint }: { label: string; value: unknown; hint?: string }) {
  const text = scalar(value);
  const isLongText = text.length > 20;
  return <div className={`g1-metric${isLongText ? " g1-metric-text" : ""}`}><span>{label}</span><strong>{text}</strong>{hint && <small>{hint}</small>}</div>;
}

function Empty({ text = "Node sẽ cập nhật khi backend thực thi." }: { text?: string }) {
  return <div className="g1-empty"><strong>Chưa có output</strong><span>{text}</span></div>;
}

function ProfilePresenter({ nodes }: { nodes: Graph1NodeExecution[] }) {
  const rawOutput = record(nodes.find((node) => node.node_key === "raw_profiler")?.output);
  const digestOutput = record(nodes.find((node) => node.node_key === "profiler_digest")?.output);
  const profile = record(rawOutput.dataset_profile);
  const rawSource = record(profile.source_rows ?? Object.values(profile)[0]);
  const metadata = record(rawSource.table_metadata);
  const rawColumns = record(rawSource.columns);
  const quality = record(rawSource.quality_summary);
  const digest = record(digestOutput.dataset_profile_digest);
  const digestSource = record(digest.source_rows ?? Object.values(digest)[0]);
  const digestColumns = records(digestSource.columns);
  const rows = digestColumns.map((column) => {
    const rawColumn = record(rawColumns[String(column.name)]);
    return { ...rawColumn, ...column, range: column.range ?? (rawColumn.min !== undefined || rawColumn.max !== undefined ? [rawColumn.min, rawColumn.max] : undefined), values: column.values ?? rawColumn.sample_values };
  });
  if (!Object.keys(rawSource).length && !Object.keys(digestSource).length) return <Empty />;
  return <div className="g1-presenter">
    <div className="g1-metric-grid five">
      <Metric label="ROWS" value={compactNumber(metadata.total_rows ?? digestSource.rows)} />
      <Metric label="COLUMNS" value={Object.keys(rawColumns).length || digestColumns.length} />
      <Metric label="COMPLETENESS" value={percent(quality.completeness_score)} />
      <Metric label="VALIDITY" value={percent(quality.validity_score)} />
      <Metric label="DUPLICATE RATE" value={percent(quality.duplicate_rate)} />
    </div>
    <div className="g1-section-heading"><div><strong>Column profile</strong><span>{rows.length} columns</span></div></div>
    <Table rows={rows} columns={[
      ["name", "Name", (value) => <code className="g1-inline-code">{scalar(value)}</code>],
      ["type", "Type", (value) => <span>{scalar(value)}</span>],
      ["role", "Role", (value) => <Tags values={[value]} kind="info" />],
      ["null_pct", "Null %", (value) => percent(value)],
      ["signals", "Signals", (value) => <Tags values={array(value)} />],
      ["values", "Values", (value) => <ValueCell value={value} />],
      ["range", "Range", (value) => <ValueCell value={value} />],
    ]} />
    <Technical value={{ raw_profiler: rawOutput, profiler_digest: digestOutput }} />
  </div>;
}

function DictionaryTables({ value, source }: { value: unknown; source?: unknown }) {
  const dictionary = record(value);
  const tables = record(dictionary.tables);
  if (!Object.keys(tables).length) return <GenericRenderer value={dictionary} />;
  return <div className="g1-table-sections">{Object.entries(tables).map(([tableKey, tableValue]) => {
    const table = record(tableValue);
    return <section className="g1-data-section" key={tableKey}><header><div><span>TABLE</span><h3>{scalar(table.table_name, tableKey)}</h3></div>{source !== undefined && <span className="g1-chip">Source: {scalar(source)}</span>}</header><p>{scalar(table.description, "Chưa có mô tả bảng.")}</p><Table rows={records(table.columns)} columns={[
      ["name", "Name", (value) => <code className="g1-inline-code">{scalar(value)}</code>],
      ["description", "Description", (value) => <ValueCell value={value} />],
      ["semantic_type", "Semantic type", (value) => <Tags values={[value]} kind="info" />],
      ["business_role", "Business role", (value) => <Tags values={[value]} kind="constraint" />],
      ["nullable_expected", "Nullable", (value) => <span className={`g1-boolean ${value ? "yes" : "no"}`}>{value ? "Yes" : "No"}</span>],
      ["governance_notes", "Governance notes", (value) => <Tags values={array(value)} />],
    ]} /></section>;
  })}</div>;
}

export interface SemanticReviewProps {
  editable: boolean;
  contract: Row;
  busy: boolean;
  onColumnChange: (tableKey: string, index: number, field: string, value: string | boolean | number) => void;
  onConfirm: () => void;
}

function SemanticPresenter({ stage, review }: { stage: DisplayStage; review?: SemanticReviewProps }) {
  const understanding = stage.nodes.find((node) => node.node_key === "dataset_understanding");
  const gate = stage.nodes.find((node) => node.node_key === "hitl_semantic_gate");
  const understandingOutput = record(understanding?.output);
  const gateOutput = record(gate?.output);
  const gateContract = record(gateOutput.semantic_contract);
  const generatedContract = record(understandingOutput.semantic_contract);
  const contract = review?.editable && Object.keys(review.contract).length
    ? review.contract
    : Object.keys(gateContract).length ? gateContract : generatedContract;
  const tables = record(contract.tables);
  if (!Object.keys(contract).length) return <Empty />;
  return <div className="g1-presenter">
    <div className={`g1-summary-strip ${review?.editable ? "review" : ""}`}><strong>Semantic Contract</strong><span>{review?.editable ? "Cần steward xác nhận trước khi Graph tiếp tục" : `Review: ${scalar(gateOutput.decision ?? contract.status, "Generated from profile and dictionary")}`}</span></div>
    {Object.entries(tables).map(([tableKey, tableValue]) => { const table = record(tableValue); const columns = records(table.columns); return <section className="g1-data-section" key={tableKey}>
      <div className="g1-metric-grid three"><Metric label="DOMAIN" value={table.domain} /><Metric label="STATUS" value={contract.status} /><Metric label="COLUMNS" value={columns.length} /></div>
      <div className="g1-purpose-card"><span>PURPOSE</span><p>{scalar(table.table_purpose, "Chưa có mô tả mục đích.")}</p></div>
      <div className="g1-table-wrap"><table className="g1-table g1-semantic-table"><thead><tr><th>Name</th><th>Description</th><th>Semantic type</th><th>Business role</th><th>Nullable</th><th>Confidence</th></tr></thead><tbody>{columns.map((column, index) => <tr key={scalar(column.name, String(index))}>
        <td data-label="Name">{review?.editable ? <input aria-label={`Name ${tableKey} ${index}`} value={scalar(column.name, "")} onChange={(event) => review.onColumnChange(tableKey, index, "name", event.target.value)} /> : <code className="g1-inline-code">{scalar(column.name)}</code>}</td>
        <td data-label="Description">{review?.editable ? <textarea rows={2} aria-label={`Description ${tableKey} ${index}`} value={scalar(column.description, "")} onInput={(event) => { event.currentTarget.style.height = "auto"; event.currentTarget.style.height = `${event.currentTarget.scrollHeight}px`; }} onChange={(event) => review.onColumnChange(tableKey, index, "description", event.target.value)} /> : <ValueCell value={column.description} />}</td>
        <td data-label="Semantic type">{review?.editable ? <input aria-label={`Semantic type ${tableKey} ${index}`} value={scalar(column.semantic_type, "")} onChange={(event) => review.onColumnChange(tableKey, index, "semantic_type", event.target.value)} /> : <Tags values={[column.semantic_type]} kind="info" />}</td>
        <td data-label="Business role">{review?.editable ? <input aria-label={`Business role ${tableKey} ${index}`} value={scalar(column.business_role, "")} onChange={(event) => review.onColumnChange(tableKey, index, "business_role", event.target.value)} /> : <Tags values={[column.business_role]} kind="constraint" />}</td>
        <td data-label="Nullable"><input type="checkbox" aria-label={`Nullable ${tableKey} ${index}`} checked={Boolean(column.nullable_expected)} disabled={!review?.editable} onChange={(event) => review?.onColumnChange(tableKey, index, "nullable_expected", event.target.checked)} /></td>
        <td data-label="Confidence">{review?.editable ? <input type="number" min="0" max="1" step="0.01" aria-label={`Confidence ${tableKey} ${index}`} value={Number(column.confidence ?? 0)} onChange={(event) => review.onColumnChange(tableKey, index, "confidence", Number(event.target.value))} /> : confidencePercent(column.confidence)}</td>
      </tr>)}</tbody></table></div>
      <div className="g1-split-sections"><section><h4>Relationships</h4><GenericRenderer value={table.relationships} /></section><section><h4>Business assumptions</h4><GenericRenderer value={table.business_assumptions} /></section></div>
    </section>; })}
    {review?.editable && <div className="g1-semantic-action"><div><strong>HITL Semantic Gate</strong><span>Xác nhận bảng trên để tiếp tục sang Rule candidates.</span></div><button type="button" className="button primary" disabled={review.busy || !Object.keys(tables).length} onClick={review.onConfirm}>{review.busy ? "Đang xác nhận…" : "Xác nhận và tiếp tục"}</button></div>}
    <Technical value={{ dataset_understanding: understandingOutput, hitl_semantic_gate: gateOutput }} label="Technical contract JSON" />
  </div>;
}

function EvidencePanel({ candidate }: { candidate: Row }) {
  const items = records(candidate.evidence_items);
  return <div className="g1-inline-evidence"><header><div><span>EVIDENCE FOR</span><strong>{scalar(candidate.column, "Table-level rule")}</strong></div><span>{items.length} items</span></header>{items.length ? <Table rows={items.map((item) => ({ ...item, column: candidate.column }))} columns={[
    ["column", "Column", (value) => <code className="g1-inline-code">{scalar(value)}</code>], ["source_type", "Source type", (value) => <Tags values={[value]} kind="info" />], ["metric", "Metric", (value) => scalar(value)], ["value", "Observed value", (value) => <ValueCell value={value} />], ["id", "Reference", (value) => <details className="g1-cell-details"><summary>Technical reference</summary><code>{scalar(value)}</code></details>],
  ]} /> : <Empty text="Candidate này không trả evidence items." />}</div>;
}

function CandidatePresenter({ output }: { output: Row }) {
  const candidates = records(output.rule_candidates);
  const [selected, setSelected] = useState<string | null>(null);
  if (!candidates.length) return <Empty />;
  return <div className="g1-candidates"><div className="g1-summary-strip"><strong>{candidates.length} candidates</strong><span>Chọn một candidate để xem evidence ngay bên dưới.</span></div>{candidates.map((candidate, index) => {
    const id = scalar(candidate.candidate_id ?? candidate.id, `candidate-${index + 1}`); const open = selected === id;
    return <div className="g1-candidate-row" key={id}><button type="button" aria-expanded={open} onClick={() => setSelected(open ? null : id)}><div><span>{id}</span><strong>{scalar(candidate.column, "Table-level")} · {scalar(candidate.rule_type)}</strong></div><div><Tags values={Object.keys(record(candidate.parameters))} kind="constraint" /><span className="g1-evidence-count">{records(candidate.evidence_items).length} evidence</span></div></button>{open && <EvidencePanel candidate={candidate} />}</div>;
  })}<Technical value={output} /></div>;
}

const promptMarkdownComponents: Components = {
  a: ({ children, node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer">{children}</a>,
  table: ({ children, node: _node, ...props }) => <div className="g1-markdown-table-wrap"><table {...props}>{children}</table></div>,
};

function PromptMarkdown({ content, table }: { content: string; table: string }) {
  if (!content.trim()) return <div className="g1-prompt-empty" role="status">Prompt không có nội dung.</div>;
  return <div className="g1-prompt-markdown" aria-label={`Rendered prompt for ${table}`}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={promptMarkdownComponents} skipHtml>{content}</ReactMarkdown>
  </div>;
}

function PromptPresenter({ output }: { output: Row }) {
  const prompts = record(output.specialized_system_prompts ?? output.customized_prompts);
  const [copied, setCopied] = useState("");
  if (!Object.keys(prompts).length) return <Empty />;
  return <div className="g1-prompt-list">{Object.entries(prompts).map(([table, prompt]) => { const content = scalar(prompt, ""); return <article className="g1-prompt-card" key={table}><header><div><span>TABLE PROMPT</span><strong>{table}</strong></div><div><span className="g1-chip success">READY</span><span>{content.length.toLocaleString()} chars</span><button type="button" aria-label={`Copy prompt for ${table}`} onClick={() => { void navigator.clipboard.writeText(content); setCopied(table); }}>{copied === table ? "Copied" : "Copy"}</button><span className="g1-sr-only" aria-live="polite">{copied === table ? `Prompt for ${table} copied` : ""}</span></div></header><PromptMarkdown content={content} table={table} /></article>; })}<Technical value={output} /></div>;
}

function confidenceValue(rule: Row) {
  if (typeof rule.confidence_score === "number") return rule.confidence_score;
  const confidence = record(rule.confidence);
  return confidence.overall;
}

const usedParameters = (rule: Row) => Object.entries(record(rule.parameters)).filter(([, value]) => value !== null && value !== undefined && value !== "" && (!Array.isArray(value) || value.length));
const ruleEvidence = (rule: Row) => array(rule.selected_evidence_refs ?? rule.evidence_refs ?? rule.evidence);

function RuleCondition({ rule }: { rule: Row }) {
  const parameters = usedParameters(rule);
  return <div className="g1-condition"><Tags values={[rule.rule_type]} kind="info" />{parameters.length ? <dl>{parameters.map(([key, value]) => <div key={key}><dt>{human(key)}</dt><dd><ValueCell value={value} /></dd></div>)}</dl> : <span className="g1-muted">Không có tham số</span>}</div>;
}

function RulesTable({ rules, reviewStatus }: { rules: Row[]; reviewStatus?: string }) {
  if (!rules.length) return <Empty text="Không có rule để hiển thị." />;
  return <div className="g1-table-wrap g1-rule-table-wrap"><table className="g1-table g1-rule-table"><thead><tr><th>Rule</th><th>Mục đích kiểm tra</th><th>Target</th><th>Điều kiện</th><th>Risk</th><th>Confidence</th><th>Evidence</th>{reviewStatus !== undefined && <th>Review</th>}</tr></thead><tbody>{rules.map((rule, index) => { const refs = ruleEvidence(rule); return <tr key={scalar(rule.rule_id, String(index))}>
    <td data-label="Rule"><div className="g1-rule-identity"><code>{scalar(rule.rule_id, `RULE-${index + 1}`)}</code><strong>{scalar(rule.rule_name ?? rule.rule_description, "Unnamed rule")}</strong></div></td>
    <td data-label="Mục đích kiểm tra"><div className="g1-rule-purpose"><strong>{scalar(rule.rule_description ?? rule.business_rationale, "Chưa có mô tả.")}</strong><span>{scalar(rule.ai_reasoning ?? rule.business_rationale, "Chưa có reasoning.")}</span></div></td>
    <td data-label="Target"><div className="g1-rule-target"><span>{scalar(rule.table_name, "Table")}</span><code>{scalar(rule.column, "Table-level")}</code></div></td>
    <td data-label="Điều kiện"><RuleCondition rule={rule} /></td>
    <td data-label="Risk"><div className="g1-risk"><Tags values={[rule.severity]} kind="warning" /><Tags values={[rule.dimension]} kind="constraint" /></div></td>
    <td data-label="Confidence"><strong className="g1-table-score">{confidencePercent(confidenceValue(rule))}</strong></td>
    <td data-label="Evidence"><div className="g1-rule-evidence"><strong>{refs.length} refs</strong><Tags values={refs} kind="info" /></div></td>
    {reviewStatus !== undefined && <td data-label="Review"><span className={`g1-review-state ${reviewStatus.toLowerCase()}`}>{human(reviewStatus)}</span></td>}
  </tr>; })}</tbody></table></div>;
}

function RulePresenter({ output }: { output: Row }) {
  const rules = records(output.proposed_rules);
  const errors = records(output.rule_proposal_errors);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [severity, setSeverity] = useState("");
  const [dimension, setDimension] = useState("");
  const options = (key: string) => Array.from(new Set(rules.map((rule) => scalar(rule[key], "")).filter(Boolean))).sort();
  const filtered = useMemo(() => rules.filter((rule) => {
    const haystack = [rule.rule_id, rule.rule_name, rule.rule_description, rule.table_name, rule.column, rule.rule_type, rule.ai_reasoning].map((value) => scalar(value, "").toLowerCase()).join(" ");
    return (!query || haystack.includes(query.toLowerCase())) && (!type || scalar(rule.rule_type, "") === type) && (!severity || scalar(rule.severity, "") === severity) && (!dimension || scalar(rule.dimension, "") === dimension);
  }), [rules, query, type, severity, dimension]);
  const confidences = rules.map(confidenceValue).filter((value): value is number => typeof value === "number");
  const averageConfidence = confidences.length ? confidences.reduce((sum, value) => sum + (value <= 1 ? value : value / 100), 0) / confidences.length : undefined;
  const coveredColumns = new Set(rules.map((rule) => scalar(rule.column, "")).filter(Boolean));
  const withEvidence = rules.filter((rule) => ruleEvidence(rule).length).length;
  const counts = (key: string) => Array.from(new Set(rules.map((rule) => scalar(rule[key], "")).filter(Boolean))).map((value) => `${human(value)} · ${rules.filter((rule) => scalar(rule[key], "") === value).length}`);
  return <div className="g1-presenter">{errors.map((error, index) => <div className="g1-batch-error" role="alert" key={index}><strong>{scalar(error.table, "Unknown table")} · batch {scalar(error.batch)}</strong><span>{scalar(error.error)}</span></div>)}{rules.length ? <>
    <div className="g1-metric-grid four"><Metric label="TOTAL RULES" value={rules.length} /><Metric label="COLUMNS PROTECTED" value={coveredColumns.size} /><Metric label="AVG CONFIDENCE" value={confidencePercent(averageConfidence)} /><Metric label="EVIDENCE COVERAGE" value={`${withEvidence}/${rules.length}`} /></div>
    <div className="g1-rule-statistics"><section><span>RULE TYPES</span><Tags values={counts("rule_type")} kind="info" /></section><section><span>SEVERITY</span><Tags values={counts("severity")} kind="warning" /></section><section><span>DIMENSIONS</span><Tags values={counts("dimension")} kind="constraint" /></section></div>
    <div className="g1-rule-toolbar"><label><span>Tìm rule</span><input type="search" value={query} placeholder="Tên, cột hoặc mô tả…" onChange={(event) => setQuery(event.target.value)} /></label><label><span>Type</span><select value={type} onChange={(event) => setType(event.target.value)}><option value="">Tất cả</option>{options("rule_type").map((value) => <option key={value}>{value}</option>)}</select></label><label><span>Severity</span><select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">Tất cả</option>{options("severity").map((value) => <option key={value}>{value}</option>)}</select></label><label><span>Dimension</span><select value={dimension} onChange={(event) => setDimension(event.target.value)}><option value="">Tất cả</option>{options("dimension").map((value) => <option key={value}>{value}</option>)}</select></label><span className="g1-filter-count">{filtered.length}/{rules.length} rules</span></div>
    <RulesTable rules={filtered} />
  </> : !errors.length && <Empty text="Node 8 chưa sinh rule hợp lệ." />}<Technical value={output} /></div>;
}

function ReviewPresenter({ output }: { output: Row }) {
  const metadata = record(output.metadata);
  const rules = records(output.proposed_rules);
  const blockedBy = output.blocked_by;
  if (blockedBy) return <div className="g1-blocked"><strong>Stage bị bỏ qua</strong><p>{scalar(output.reason)}</p><span>Blocked by: {scalar(blockedBy)}</span></div>;
  const total = Number(metadata.rules_saved ?? rules.length);
  const approved = Number(output.approved_count ?? 0);
  const edited = Number(output.edited_count ?? 0);
  const rejected = Number(output.rejected_count ?? 0);
  const pending = Math.max(0, total - approved - edited - rejected);
  const reviewStatus = scalar(output.decision ?? metadata.hitl_status, "AWAITING_REVIEW");
  return <div className="g1-presenter"><div className="g1-metric-grid six"><Metric label="TOTAL RULES" value={total} /><Metric label="APPROVED" value={approved} /><Metric label="EDITED" value={edited} /><Metric label="REJECTED" value={rejected} /><Metric label="PENDING" value={pending} /><Metric label="REVIEWER" value={output.reviewer ?? "Awaiting steward"} /></div><div className="g1-summary-strip"><strong>{reviewStatus}</strong><span>Rules được persist sau khi Node 8 thành công và chỉ hoàn tất khi steward quyết định.</span></div><RulesTable rules={rules} reviewStatus={reviewStatus} /><Technical value={output} /></div>;
}

export function GenericRenderer({ value }: { value: unknown }): ReactNode {
  if (value === null || value === undefined || value === "") return <span className="g1-muted">—</span>;
  if (typeof value !== "object") return <span>{scalar(value)}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="g1-muted">Không có dữ liệu</span>;
    if (value.every((item) => item === null || typeof item !== "object")) return <Tags values={value} />;
    const rows = records(value);
    if (rows.length === value.length) {
      const keys = Array.from(new Set(rows.flatMap(Object.keys))).slice(0, 7);
      return <Table rows={rows} columns={keys.map((key) => [key, human(key), (item: unknown) => typeof item === "object" ? <ValueCell value={item} /> : scalar(item)])} />;
    }
    return <div className="g1-generic-list">{value.map((item, index) => <GenericRenderer value={item} key={index} />)}</div>;
  }
  const entries = Object.entries(record(value));
  if (!entries.length) return <span className="g1-muted">Không có dữ liệu</span>;
  return <dl className="g1-key-value">{entries.map(([key, item]) => <div key={key}><dt>{human(key)}</dt><dd>{typeof item === "object" ? <ValueCell value={item} /> : scalar(item)}</dd></div>)}</dl>;
}

export function StagePresenter({ stage, semanticReview }: { stage: DisplayStage; semanticReview?: SemanticReviewProps }) {
  if (stage.key === "profile_info") return <ProfilePresenter nodes={stage.nodes} />;
  if (stage.key === "understanding_semantic") return <SemanticPresenter stage={stage} review={semanticReview} />;
  const node = stage.nodes[0];
  if (!node || !Object.keys(node.output ?? {}).length) return <Empty />;
  const output = record(node.output);
  if (stage.key === "data_dictionary_generator") return <div className="g1-presenter"><DictionaryTables value={output.normalized_data_dictionary ?? output.data_dictionary} source={output.data_dictionary_source} /><Technical value={output} /></div>;
  if (stage.key === "rule_candidate_builder") return <CandidatePresenter output={output} />;
  if (stage.key === "prompt_customizer") return <PromptPresenter output={output} />;
  if (stage.key === "rule_proposer") return <RulePresenter output={output} />;
  if (stage.key === "hitl_gate") return <ReviewPresenter output={output} />;
  return <div className="g1-presenter"><GenericRenderer value={output} /><Technical value={output} /></div>;
}

export interface EvidenceSummary { column: string; count: number; sources: string[]; metrics: string[] }
export function stageEvidence(stage: DisplayStage): EvidenceSummary[] {
  const byColumn = new Map<string, { count: number; sources: Set<string>; metrics: Set<string> }>();
  const walk = (value: unknown, column = "Table-level") => {
    if (Array.isArray(value)) { value.forEach((item) => walk(item, column)); return; }
    if (!value || typeof value !== "object") return;
    const item = record(value);
    const nextColumn = typeof item.column === "string" ? item.column : column;
    if (typeof item.source_type === "string" && (item.id || item.metric)) {
      const bucket = byColumn.get(nextColumn) ?? { count: 0, sources: new Set<string>(), metrics: new Set<string>() };
      bucket.count += 1; bucket.sources.add(item.source_type); if (item.metric) bucket.metrics.add(String(item.metric)); byColumn.set(nextColumn, bucket);
    }
    Object.values(item).forEach((child) => walk(child, nextColumn));
  };
  stage.nodes.forEach((node) => walk(node.output));
  return Array.from(byColumn, ([column, item]) => ({ column, count: item.count, sources: [...item.sources], metrics: [...item.metrics] }));
}

export function EvidenceOverview({ evidence }: { evidence: EvidenceSummary[] }) {
  if (!evidence.length) return <Empty text="Stage này không trả evidence có cấu trúc." />;
  return <Table rows={evidence as unknown as Row[]} columns={[
    ["column", "Column", (value) => <code className="g1-inline-code">{scalar(value)}</code>], ["count", "Evidence", (value) => scalar(value)], ["sources", "Sources", (value) => <Tags values={array(value)} kind="info" />], ["metrics", "Metrics", (value) => <Tags values={array(value)} />],
  ]} />;
}
