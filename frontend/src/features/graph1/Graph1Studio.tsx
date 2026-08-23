import { useEffect, useMemo, useState } from "react";
import "./graph1-studio.css";

type IconProps = { size?: number; className?: string };
const makeIcon = (path: React.ReactNode) => function StudioIcon({ size = 18, className }: IconProps) {
  return <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{path}</svg>;
};
const Check = makeIcon(<path d="m5 12 4 4L19 6"/>);
const CheckCircle2 = makeIcon(<><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></>);
const ChevronRight = makeIcon(<path d="m9 6 6 6-6 6"/>);
const ArrowRight = makeIcon(<><path d="M5 12h14"/><path d="m14 7 5 5-5 5"/></>);
const AlertTriangle = makeIcon(<><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 9v4M12 17h.01"/></>);
const CircleDot = makeIcon(<><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/></>);
const Clock3 = makeIcon(<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>);
const Database = makeIcon(<><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></>);
const ShieldCheck = makeIcon(<><path d="M12 3 5 6v5c0 4.5 2.7 7.8 7 10 4.3-2.2 7-5.5 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-5"/></>);
const LockKeyhole = makeIcon(<><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v2"/></>);
const GitBranch = makeIcon(<><circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10M8 9c6 0 4-3 8-3"/></>);
const Gauge = makeIcon(<><path d="M4 18a8 8 0 1 1 16 0"/><path d="m12 14 4-4M7 18h10"/></>);
const BookOpenText = makeIcon(<><path d="M4 5h5a3 3 0 0 1 3 3v11a3 3 0 0 0-3-3H4V5ZM20 5h-5a3 3 0 0 0-3 3v11a3 3 0 0 1 3-3h5V5Z"/><path d="M6 9h3M15 9h3"/></>);
const SearchCheck = makeIcon(<><circle cx="10" cy="10" r="6"/><path d="m14.5 14.5 5 5M7.5 10l2 2 3-4"/></>);
const Lightbulb = makeIcon(<><path d="M9 18h6M10 21h4M8 14a6 6 0 1 1 8 0c-1 .8-1 2-1 2H9s0-1.2-1-2Z"/></>);
const MessageSquareText = makeIcon(<><path d="M5 5h14v11H9l-4 4V5Z"/><path d="M8 9h8M8 12h5"/></>);
const Sparkles = makeIcon(<><path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2L12 3ZM18 14l.8 2.2L21 17l-2.2.8L18 20l-.8-2.2L15 17l2.2-.8L18 14Z"/></>);
const Fingerprint = makeIcon(<><path d="M7 12a5 5 0 0 1 10 0c0 4-1 7-3 9M4 12a8 8 0 0 1 16 0M10 12c0 5-1 7-2 8M13 12c0 3-.3 5-1 7"/></>);
const FileJson2 = makeIcon(<><path d="M6 3h8l4 4v14H6V3Z"/><path d="M14 3v5h5M10 11l-2 2 2 2M14 11l2 2-2 2"/></>);
const Braces = makeIcon(<path d="M9 4H7a2 2 0 0 0-2 2v3l-2 3 2 3v3a2 2 0 0 0 2 2h2M15 4h2a2 2 0 0 1 2 2v3l2 3-2 3v3a2 2 0 0 1-2 2h-2"/>);
const Play = makeIcon(<path d="m8 5 11 7-11 7V5Z"/>);
const RotateCcw = makeIcon(<><path d="M4 4v6h6"/><path d="M5.5 17a8 8 0 1 0 .5-10L4 10"/></>);
const TableProperties = makeIcon(<><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/></>);
const Tags = makeIcon(<><path d="M4 4h7l9 9-7 7-9-9V4Z"/><circle cx="8" cy="8" r="1"/></>);

type NodeStatus = "completed" | "running" | "waiting" | "queued";
type OutputKind =
  | "profile"
  | "digest"
  | "dictionary"
  | "understanding"
  | "semantic-gate"
  | "candidates"
  | "prompt"
  | "proposals"
  | "rule-gate";

type GraphNode = {
  id: string;
  label: string;
  technicalName: string;
  description: string;
  duration: string;
  kind: OutputKind;
  status: NodeStatus;
};

const baseNodes: GraphNode[] = [
  { id: "01", label: "Raw profiler", technicalName: "raw_profiler", description: "Quét schema, thống kê và tín hiệu chất lượng thô.", duration: "1.8s", kind: "profile", status: "completed" },
  { id: "02", label: "Profiler digest", technicalName: "profiler_digest", description: "Nén profile thành các tín hiệu có thể hành động.", duration: "0.7s", kind: "digest", status: "completed" },
  { id: "03", label: "Data dictionary", technicalName: "data_dictionary_generator", description: "Suy luận ngữ nghĩa và vai trò cho từng cột.", duration: "2.1s", kind: "dictionary", status: "completed" },
  { id: "04", label: "Dataset understanding", technicalName: "dataset_understanding", description: "Tổng hợp mục đích, grain và rủi ro nghiệp vụ.", duration: "2.4s", kind: "understanding", status: "completed" },
  { id: "05", label: "Semantic review", technicalName: "hitl_semantic_gate", description: "Steward xác nhận ngữ nghĩa trước khi sinh rule.", duration: "4m 12s", kind: "semantic-gate", status: "completed" },
  { id: "06", label: "Rule candidates", technicalName: "rule_candidate_builder", description: "Chuyển evidence thành tập ứng viên có traceability.", duration: "1.1s", kind: "candidates", status: "completed" },
  { id: "07", label: "Prompt customizer", technicalName: "prompt_customizer", description: "Đóng gói context và guardrails cho proposer.", duration: "0.5s", kind: "prompt", status: "completed" },
  { id: "08", label: "Rule proposer", technicalName: "rule_proposer", description: "Đề xuất rule có confidence và bằng chứng.", duration: "3.7s", kind: "proposals", status: "completed" },
  { id: "09", label: "Rule approval", technicalName: "hitl_gate", description: "Steward duyệt rule trước khi chuyển sang Graph 2.", duration: "2m 08s", kind: "rule-gate", status: "completed" },
];

const IconByKind = {
  profile: Database,
  digest: Gauge,
  dictionary: BookOpenText,
  understanding: SearchCheck,
  "semantic-gate": ShieldCheck,
  candidates: Lightbulb,
  prompt: MessageSquareText,
  proposals: Sparkles,
  "rule-gate": LockKeyhole,
};

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return <div className="g1-metric"><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div>;
}

function Evidence({ children }: { children: React.ReactNode }) {
  return <span className="g1-evidence"><Fingerprint size={13} />{children}</span>;
}

function OutputPanel({ kind }: { kind: OutputKind }) {
  if (kind === "profile") return <>
    <div className="g1-metric-grid"><Metric label="Rows scanned" value="48,260" note="100% sample" /><Metric label="Columns" value="12" note="4 semantic types" /><Metric label="Completeness" value="96.8%" note="1,544 null cells" /><Metric label="Duplicates" value="1.24%" note="598 rows" /></div>
    <div className="g1-section-heading"><div><TableProperties size={17} /><strong>Column health</strong></div><span>12 columns analyzed</span></div>
    <div className="g1-table-wrap"><table className="g1-table"><thead><tr><th>Column</th><th>Type</th><th>Null</th><th>Distinct</th><th>Signal</th></tr></thead><tbody>
      <tr><td><code>ride_id</code></td><td>string</td><td>0%</td><td>48,260</td><td><span className="g1-chip success">Candidate key</span></td></tr>
      <tr><td><code>pickup_zone</code></td><td>string</td><td>2.8%</td><td>84</td><td><span className="g1-chip warning">Missing values</span></td></tr>
      <tr><td><code>fare_amount</code></td><td>decimal</td><td>0.4%</td><td>6,182</td><td><span className="g1-chip danger">17 negatives</span></td></tr>
      <tr><td><code>payment_type</code></td><td>string</td><td>0%</td><td>7</td><td><span className="g1-chip success">Stable domain</span></td></tr>
    </tbody></table></div>
  </>;
  if (kind === "digest") return <>
    <div className="g1-callout warning"><AlertTriangle size={19} /><div><strong>3 signals need attention</strong><p>Negative fares, sparse pickup zones and duplicate ride identifiers may affect billing analytics.</p></div></div>
    <div className="g1-signal-list">
      <div><span className="g1-signal-rank high">HIGH</span><div><strong>fare_amount violates positive monetary semantics</strong><p>17 values fall below 0; minimum observed value is −42.50.</p><Evidence>profile.columns.fare_amount.min</Evidence></div></div>
      <div><span className="g1-signal-rank medium">MED</span><div><strong>pickup_zone is unexpectedly incomplete</strong><p>1,351 rows have no pickup zone although dropoff zone is present.</p><Evidence>profile.columns.pickup_zone.null_rate</Evidence></div></div>
      <div><span className="g1-signal-rank low">LOW</span><div><strong>Potential duplicate journeys</strong><p>598 rows share the same ride_id fingerprint.</p><Evidence>profile.duplicate_groups</Evidence></div></div>
    </div>
  </>;
  if (kind === "dictionary") return <>
    <div className="g1-summary-strip"><BookOpenText size={18}/><span>AI generated <strong>12 definitions</strong>; 10 high-confidence and 2 need steward review.</span></div>
    <div className="g1-card-grid">
      {[['ride_id','Unique identifier for one completed ride','IDENTIFIER','0.99'],['pickup_zone','Operational zone where the passenger boarded','LOCATION','0.92'],['fare_amount','Final passenger fare in the settlement currency','MONEY','0.96'],['payment_type','Tender category used to settle the ride','CATEGORY','0.89']].map(([name,desc,type,score]) => <article className="g1-definition-card" key={name}><div><code>{name}</code><span>{score}</span></div><p>{desc}</p><footer><Tags size={13}/>{type}</footer></article>)}
    </div>
  </>;
  if (kind === "understanding") return <>
    <div className="g1-understanding-hero"><div className="g1-icon-tile"><SearchCheck size={23}/></div><div><span>INFERRED DATASET PURPOSE</span><h3>Ride settlement event ledger</h3><p>Mỗi dòng đại diện cho một chuyến đi đã hoàn tất, dùng để đối soát doanh thu, hiệu suất vùng và hành vi thanh toán.</p></div><span className="g1-confidence">94% confidence</span></div>
    <div className="g1-fact-grid"><div><span>Grain</span><strong>1 row / completed ride</strong></div><div><span>Primary entity</span><strong>Ride</strong></div><div><span>Likely key</span><strong><code>ride_id</code></strong></div><div><span>Time anchor</span><strong><code>pickup_at</code></strong></div></div>
    <div className="g1-section-heading"><div><GitBranch size={17}/><strong>Business relationships</strong></div></div>
    <div className="g1-relation"><code>pickup_at</code><ArrowRight size={16}/><code>dropoff_at</code><span>must be chronological</span></div><div className="g1-relation"><code>trip_distance</code><ArrowRight size={16}/><code>fare_amount</code><span>expected positive correlation</span></div>
  </>;
  if (kind === "semantic-gate") return <>
    <div className="g1-approval-hero approved"><div><CheckCircle2 size={24}/><span>STEWARD DECISION</span><h3>Semantic model approved</h3><p>Approved by Mai Nguyen · Data Steward · 10:42 today</p></div><span>APPROVED</span></div>
    <div className="g1-review-grid"><div><span>Definitions reviewed</span><strong>12 / 12</strong></div><div><span>Edits made</span><strong>2</strong></div><div><span>Open concerns</span><strong>0</strong></div></div>
    <div className="g1-note"><MessageSquareText size={17}/><div><strong>Steward note</strong><p>“fare_amount” is the settled amount after discounts. Negative values are only valid when <code>transaction_type = REFUND</code>.</p></div></div>
  </>;
  if (kind === "candidates") return <>
    <div className="g1-metric-grid three"><Metric label="Candidates" value="8" note="from 14 signals"/><Metric label="Evidence coverage" value="100%" note="all traceable"/><Metric label="Priority high" value="3" note="billing impact"/></div>
    <div className="g1-candidate-list">
      {[['C-01','ride_id must be unique','Uniqueness','HIGH','duplicate_groups'],['C-02','fare_amount must be non-negative unless refund','Conditional range','HIGH','semantic_note + min'],['C-03','dropoff_at must occur after pickup_at','Cross-field','HIGH','business_relationship'],['C-04','payment_type must use known domain','Accepted values','MED','distinct_values']].map(([id,title,type,priority,source]) => <article key={id}><span>{id}</span><div><strong>{title}</strong><p>{type}</p></div><span className={`g1-chip ${priority === 'HIGH' ? 'danger':'warning'}`}>{priority}</span><Evidence>{source}</Evidence></article>)}
    </div>
  </>;
  if (kind === "prompt") return <>
    <div className="g1-prompt-meta"><span><Braces size={15}/> Template <strong>rule_proposer.v3</strong></span><span>2,184 tokens</span><span>temperature 0.1</span></div>
    <div className="g1-code-panel"><div><span>COMPILED PROMPT</span><button type="button" aria-label="Prompt is read only"><LockKeyhole size={14}/> Read only</button></div><pre>{`ROLE\nYou are a conservative data-quality rule architect.\n\nDATASET CONTEXT\nGrain: one completed ride per row\nKey: ride_id\nPurpose: settlement and operations analytics\n\nCONFIRMED SEMANTICS\n- fare_amount is settled fare after discounts\n- negative fare is valid only for REFUND transactions\n\nGUARDRAILS\n- Every proposal must cite candidate and evidence IDs\n- Do not invent domains absent from the profile\n- Return strict JSON matching RuleProposalSchema`}</pre></div>
  </>;
  if (kind === "proposals") return <>
    <div className="g1-summary-strip"><Sparkles size={18}/><span><strong>6 proposals generated</strong> from 8 candidates · 2 low-confidence candidates omitted.</span></div>
    <div className="g1-proposal-list">
      <article><div className="g1-rule-icon"><Fingerprint size={18}/></div><div><span>RULE-001 · UNIQUENESS</span><strong>Every ride_id must identify exactly one row</strong><p><code>unique(ride_id)</code></p><Evidence>C-01 · duplicate_groups</Evidence></div><span className="g1-score">98<small>%</small></span></article>
      <article><div className="g1-rule-icon"><Gauge size={18}/></div><div><span>RULE-002 · CONDITIONAL RANGE</span><strong>Fare must be non-negative for non-refund rides</strong><p><code>transaction_type = 'REFUND' OR fare_amount &gt;= 0</code></p><Evidence>C-02 · steward semantic note</Evidence></div><span className="g1-score">96<small>%</small></span></article>
      <article><div className="g1-rule-icon"><Clock3 size={18}/></div><div><span>RULE-003 · CROSS-FIELD</span><strong>Dropoff must occur after pickup</strong><p><code>dropoff_at &gt; pickup_at</code></p><Evidence>C-03 · business_relationship</Evidence></div><span className="g1-score">94<small>%</small></span></article>
    </div>
  </>;
  return <>
    <div className="g1-approval-hero approved"><div><ShieldCheck size={24}/><span>FINAL GRAPH 1 DECISION</span><h3>5 rules approved for execution</h3><p>1 proposal rejected · decision recorded by Mai Nguyen at 12:51</p></div><span>READY FOR GRAPH 2</span></div>
    <div className="g1-review-grid"><div><span>Approved</span><strong>5</strong></div><div><span>Rejected</span><strong>1</strong></div><div><span>Edited</span><strong>1</strong></div></div>
    <div className="g1-rule-decision"><Check size={17}/><div><strong>RULE-002 was refined before approval</strong><p>Added the steward-confirmed refund exception to prevent false positives.</p></div><span className="g1-chip success">AUDITED</span></div>
  </>;
}

export function Graph1Studio({ datasetName = "rides_2025_q2.csv", onExit }: { datasetName?: string; onExit: () => void }) {
  const [selectedId, setSelectedId] = useState("08");
  const [runNodes, setRunNodes] = useState(baseNodes);
  const [replaying, setReplaying] = useState(false);
  const selected = useMemo(() => runNodes.find((node) => node.id === selectedId) ?? runNodes[0], [runNodes, selectedId]);

  useEffect(() => {
    if (!replaying) return;
    const next = runNodes.findIndex((node) => node.status === "running");
    if (next < 0) { setReplaying(false); return; }
    const timer = window.setTimeout(() => {
      setRunNodes((nodes) => nodes.map((node, index) => index === next ? { ...node, status: "completed" } : index === next + 1 ? { ...node, status: "running" } : node));
      if (next + 1 < runNodes.length) setSelectedId(runNodes[next + 1].id);
    }, 720);
    return () => window.clearTimeout(timer);
  }, [replaying, runNodes]);

  const replay = () => {
    setRunNodes(baseNodes.map((node, index) => ({ ...node, status: index === 0 ? "running" : "queued" })));
    setSelectedId("01");
    setReplaying(true);
  };

  const completed = runNodes.filter((node) => node.status === "completed").length;
  const SelectedIcon = IconByKind[selected.kind];

  return <main className="g1-studio" id="main-content">
    <header className="g1-hero">
      <div><div className="g1-breadcrumb"><button type="button" onClick={onExit}>Workspace</button><ChevronRight size={14}/><span>Graph 1</span></div><div className="g1-title-row"><div className="g1-title-icon"><GitBranch size={24}/></div><div><span className="eyebrow">RULE DISCOVERY · GRAPH 1</span><h1>Agent execution studio</h1><p>Theo dõi từng quyết định của agent, từ raw profile đến rule được steward phê duyệt.</p></div></div></div>
      <div className="g1-hero-actions"><span className="g1-preview-badge"><CircleDot size={14}/> SIMULATED PREVIEW</span><button type="button" className="button secondary" onClick={replay} disabled={replaying}>{replaying ? <><span className="spinner"/> Running…</> : <><RotateCcw size={16}/> Replay graph</>}</button><button type="button" className="button primary" onClick={() => setSelectedId("09")}><Play size={16}/> View final decision</button></div>
    </header>

    <section className="g1-runbar" aria-label="Graph run summary"><div><span className="g1-live-dot"/><div><strong>RUN-G1-2025-0624-018</strong><span>{datasetName} · revision 7</span></div></div><div className="g1-run-meta"><span><small>STATUS</small><strong>{replaying ? "RUNNING" : "COMPLETED"}</strong></span><span><small>NODES</small><strong>{completed} / 9</strong></span><span><small>ELAPSED</small><strong>6m 31s</strong></span><span><small>OWNER</small><strong>Data Steward</strong></span></div><div className="g1-progress"><span style={{ width: `${(completed / 9) * 100}%` }}/></div></section>

    <div className="g1-workspace">
      <aside className="g1-node-rail" aria-label="Graph 1 nodes"><div className="g1-rail-heading"><div><span>EXECUTION PATH</span><strong>9 nodes</strong></div><FileJson2 size={18}/></div><ol>{runNodes.map((node, index) => { const NodeIcon = IconByKind[node.kind]; return <li key={node.id} className={node.status}><button type="button" className={selected.id === node.id ? "active" : ""} onClick={() => setSelectedId(node.id)} aria-current={selected.id === node.id ? "step" : undefined}><span className="g1-node-connector"/><span className="g1-node-state">{node.status === "completed" ? <Check size={15}/> : node.status === "running" ? <span className="g1-pulse"/> : <span>{index + 1}</span>}</span><span className="g1-node-copy"><span>{node.id} · {node.technicalName}</span><strong><NodeIcon size={15}/>{node.label}</strong><small>{node.status === "completed" ? `${node.duration} · output ready` : node.status}</small></span><ChevronRight className="g1-node-chevron" size={16}/></button></li>})}</ol></aside>

      <section className="g1-output" aria-live="polite"><header className="g1-output-header"><div className="g1-output-title"><div className="g1-icon-tile"><SelectedIcon size={21}/></div><div><span>NODE {selected.id} OUTPUT</span><h2>{selected.label}</h2><p>{selected.description}</p></div></div><div className="g1-output-status"><span className={`g1-chip ${selected.status === 'completed' ? 'success' : 'warning'}`}>{selected.status === 'completed' ? <CheckCircle2 size={13}/> : <Clock3 size={13}/>} {selected.status.toUpperCase()}</span><span>{selected.duration}</span></div></header><div className="g1-tabs" role="tablist" aria-label="Output views"><button type="button" role="tab" aria-selected="true">Output</button><button type="button" role="tab" aria-selected="false">Evidence <span>4</span></button><button type="button" role="tab" aria-selected="false">Activity <span>3</span></button></div><div className="g1-output-body"><OutputPanel kind={selected.kind}/></div><footer className="g1-output-footer"><div><ShieldCheck size={15}/><span>Output validated against <strong>Graph1State</strong></span></div><button type="button" onClick={() => { const next = Math.min(8, baseNodes.findIndex(n => n.id === selected.id) + 1); setSelectedId(baseNodes[next].id); }}>Next node <ArrowRight size={15}/></button></footer></section>
    </div>
  </main>;
}
