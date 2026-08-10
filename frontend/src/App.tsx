import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, isMockMode } from "./api";
import { ApiError, clearApiSession } from "./api/client";
import type { AuditLog, CreateJobResponse, Dataset, DatasetProfile, DqResult, DqRun, Job, ManualRuleInput, RuleProposal, RuleSpec, UserRole } from "./types";

type View = "overview" | "rules" | "runs" | "audit";

const sleep = (duration: number) => new Promise((resolve) => window.setTimeout(resolve, duration));

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function formatRule(rule: RuleSpec) {
  if (rule.type === "not_null") return `NOT NULL · ${rule.column}`;
  if (rule.type === "numeric_range") return `RANGE · ${rule.column} ≥ ${rule.min_value}`;
  if (rule.type === "accepted_values") return `VALUES · ${rule.column} ∈ ${(rule.allowed_values ?? []).join(", ")}`;
  if (rule.type === "cross_field_comparison") return `COMPARE · ${(rule.columns ?? []).join(` ${rule.operator ?? "≤"} `)}`;
  return `DUPLICATE · ${(rule.fingerprint_columns ?? []).join(" + ")}`;
}

function getErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : fallback;
  if (error.status === 401) return "Your session has expired. Please sign in again.";
  if (error.status === 409) return "This operation is already in progress. Refresh the workspace before retrying.";
  if (error.status === 422) return "The request is not valid for the current workflow state.";
  if (error.status === 429) return "The demo quota has been reached. Please try again later.";
  if (error.status >= 500) return "The service is temporarily unavailable. Retry when it is ready.";
  return error.message || fallback;
}

function StatusPill({ label, tone = "neutral" }: { label: string; tone?: "neutral" | "success" | "warning" | "danger" | "info" }) {
  return <span className={`status-pill ${tone}`}><span className="status-dot" />{label}</span>;
}

function ProgressPanel({ job, title }: { job: Job; title: string }) {
  return <div className="progress-panel">
    <div className="progress-heading"><div><span className="eyebrow">ACTIVE JOB</span><h3>{title}</h3></div><strong>{job.progress}%</strong></div>
    <div className="progress-track"><span style={{ width: `${job.progress}%` }} /></div>
    <div className="progress-meta"><span>{job.message}</span><span>{job.status}</span></div>
  </div>;
}

function LoginScreen({ onLogin, busy, error }: { onLogin: (username: string, password: string) => void; busy: boolean; error: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const submit = (event: FormEvent) => { event.preventDefault(); onLogin(username, password); };
  return <main className="login-shell">
    <div className="login-art"><div className="orb orb-one" /><div className="orb orb-two" /><div className="grid-lines" />
      <div className="brand-lockup"><span className="brand-mark">RP</span><span>RidePulse <em>DQ</em></span></div>
      <div className="login-pitch"><span className="eyebrow">DATA QUALITY INTELLIGENCE</span><h1>Turn data signals into <span>trusted decisions.</span></h1><p>Inspect the registered mobility dataset, review evidence-grounded rules and run only the checks your Steward approves.</p><div className="metric-row"><div><strong>50k</strong><span>registered rows</span></div><div><strong>5</strong><span>typed rule templates</span></div><div><strong>100%</strong><span>audit visibility</span></div></div></div>
      <div className="login-footer">GATE 2 · COURSE PROJECT SIMULATION</div>
    </div>
    <section className="login-card"><div className="mobile-brand"><span className="brand-mark">RP</span> RidePulse <em>DQ</em></div><span className="eyebrow">ROLE-BASED ACCESS</span><h2>Welcome back</h2><p className="muted">Sign in with your demo account to open the workspace.</p><form onSubmit={submit}><label htmlFor="username">Username</label><input id="username" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="user, steward, or admin" autoFocus /><label htmlFor="password">Password</label><input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter password" />{error && <div className="inline-error">{error}</div>}<button className="button primary full" disabled={busy || username.length < 1 || password.length < 1}>{busy ? "Opening workspace…" : "Open workspace →"}</button></form><div className="login-note"><span className="lock-icon">⌁</span><span><strong>Demo accounts</strong><br /><code>user/user</code> read-only · <code>steward/steward</code> review · <code>admin/admin</code> full access.</span></div></section>
  </main>;
}

function App() {
  const [authenticated, setAuthenticated] = useState(() => sessionStorage.getItem("ridepulse.auth") === "true" && Boolean(sessionStorage.getItem("ridepulse.role")));
  const [role, setRole] = useState<UserRole>(() => (sessionStorage.getItem("ridepulse.role") as UserRole | null) ?? "USER");
  const [username, setUsername] = useState(() => sessionStorage.getItem("ridepulse.username") ?? "");
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [view, setView] = useState<View>("overview");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [profile, setProfile] = useState<DatasetProfile | null>(null);
  const [proposals, setProposals] = useState<RuleProposal[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [activeRun, setActiveRun] = useState<DqRun | null>(null);
  const [dqResults, setDqResults] = useState<DqResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [retryAction, setRetryAction] = useState<(() => void) | null>(null);
  const [editingProposal, setEditingProposal] = useState<RuleProposal | null>(null);
  const [manualRuleOpen, setManualRuleOpen] = useState(false);

  const dataset = datasets[0];
  const approvedRules = useMemo(() => proposals.filter((proposal) => proposal.status === "APPROVED"), [proposals]);
  const canOperate = role === "STEWARD" || role === "ADMIN";

  const refreshWorkspace = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [nextDatasets, nextAudit] = await Promise.all([api.listDatasets(), api.listAuditLogs()]);
      setDatasets(nextDatasets); setAuditLogs(nextAudit);
      const nextDataset = nextDatasets[0];
      if (nextDataset?.status === "PROFILE_READY") {
        setProfile(await api.getProfile(nextDataset.id));
        setProposals(await api.listProposals(nextDataset.id));
      }
    } catch (err) { if (err instanceof ApiError && err.status === 401) { clearApiSession(); sessionStorage.removeItem("ridepulse.auth"); sessionStorage.removeItem("ridepulse.role"); sessionStorage.removeItem("ridepulse.username"); setAuthenticated(false); } setError(getErrorMessage(err, "Unable to load workspace.")); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { if (authenticated) void refreshWorkspace(); }, [authenticated, refreshWorkspace]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(""), 3500); return () => window.clearTimeout(timer); }, [toast]);

  async function handleLogin(loginUsername: string, password: string) {
    setLoginBusy(true); setLoginError("");
    try { const session = await api.createSession(loginUsername, password); sessionStorage.setItem("ridepulse.auth", "true"); sessionStorage.setItem("ridepulse.role", session.role); sessionStorage.setItem("ridepulse.username", session.username); setRole(session.role); setUsername(session.username); setAuthenticated(true); }
    catch (err) { setLoginError(getErrorMessage(err, "Unable to start session.")); }
    finally { setLoginBusy(false); }
  }

  async function handleLogout() { await api.deleteSession(); sessionStorage.removeItem("ridepulse.auth"); sessionStorage.removeItem("ridepulse.role"); sessionStorage.removeItem("ridepulse.username"); setAuthenticated(false); }

  async function pollJob(acceptedJob: CreateJobResponse, onComplete: () => Promise<void>) {
    let current = await api.getJob(acceptedJob.job_id);
    setActiveJob(current);
    for (let attempt = 0; attempt < 30 && !["SUCCEEDED", "FAILED", "FAILED_RETRYABLE"].includes(current.status); attempt += 1) {
      await sleep(450); current = await api.getJob(acceptedJob.job_id); setActiveJob(current);
    }
    const finalStatus = current.status as Job["status"];
    if (finalStatus === "SUCCEEDED") { await onComplete(); setActiveJob(null); setRetryAction(null); setToast("Job completed successfully."); }
    else { setRetryAction(() => () => void pollJob(acceptedJob, onComplete)); setError(current.error ?? "The job did not complete. Retry the operation when ready."); }
  }

  async function startAnalysis() {
    if (!dataset) return; setError(""); setRetryAction(null);
    try { const job = await api.startIngestion(dataset.id, crypto.randomUUID()); await pollJob(job, async () => { setDatasets(await api.listDatasets()); setProfile(await api.getProfile(dataset.id)); }); }
    catch (err) { setError(getErrorMessage(err, "Unable to start analysis.")); }
  }

  async function requestProposals() {
    if (!dataset) return; setError(""); setRetryAction(null);
    try { const job = await api.startRuleProposals(dataset.id, crypto.randomUUID()); await pollJob(job, async () => { setProposals(await api.listProposals(dataset.id)); setAuditLogs(await api.listAuditLogs()); setView("rules"); }); }
    catch (err) { setError(getErrorMessage(err, "Unable to request proposals.")); }
  }

  async function reviewProposal(id: string, action: "approve" | "reject") {
    try { await api.reviewProposal(id, { action }); setProposals(await api.listProposals(dataset.id)); setAuditLogs(await api.listAuditLogs()); setToast(action === "approve" ? "Rule approved for execution." : "Proposal rejected and kept out of execution."); }
    catch (err) { setError(getErrorMessage(err, "Unable to update proposal.")); }
  }

  async function saveEdit(input: { title: string; description: string; severity: RuleProposal["severity"]; rule: RuleSpec }) {
    if (!editingProposal) return;
    try { await api.reviewProposal(editingProposal.id, { action: "edit", ...input }); setProposals(await api.listProposals(dataset.id)); setAuditLogs(await api.listAuditLogs()); setEditingProposal(null); setToast("Proposal edited and marked ready for approval."); }
    catch (err) { setError(getErrorMessage(err, "Unable to edit proposal.")); }
  }

  async function createManualRule(input: ManualRuleInput) {
    if (!dataset) return;
    try { await api.createManualRule(dataset.id, input); setProposals(await api.listProposals(dataset.id)); setAuditLogs(await api.listAuditLogs()); setManualRuleOpen(false); setToast("Manual rule created and queued for approval."); }
    catch (err) { setError(getErrorMessage(err, "Unable to create manual rule.")); }
  }

  async function runApprovedRules() {
    try { const queuedRun = await api.startDqRun(approvedRules.map((rule) => rule.id), crypto.randomUUID()); setActiveRun(await api.getDqRun(queuedRun.run_id)); await pollJob({ job_id: queuedRun.job_id, status: queuedRun.status }, async () => { const completed = await api.getDqRun(queuedRun.run_id); setActiveRun(completed); setDqResults(await api.getDqResults(queuedRun.run_id)); setAuditLogs(await api.listAuditLogs()); setView("runs"); }); }
    catch (err) { setError(getErrorMessage(err, "Unable to start DQ run.")); }
  }

  if (!authenticated) return <LoginScreen onLogin={handleLogin} busy={loginBusy} error={loginError} />;
  return <div className="app-shell"><aside className="sidebar"><div className="brand-lockup"><span className="brand-mark">RP</span><span>RidePulse <em>DQ</em></span></div><div className="sidebar-label">WORKSPACE</div><nav>{(["overview", "rules", "runs", "audit"] as View[]).map((item) => <button key={item} className={`nav-item ${view === item ? "active" : ""}`} onClick={() => setView(item)}><span className="nav-icon">{item === "overview" ? "◈" : item === "rules" ? "✦" : item === "runs" ? "↗" : "≡"}</span>{item === "overview" ? "Overview" : item === "rules" ? "Rule proposals" : item === "runs" ? "DQ runs" : "Audit history"}{item === "rules" && proposals.some((proposal) => ["PROPOSED", "EDITED"].includes(proposal.status)) && <span className="nav-count">{proposals.filter((proposal) => ["PROPOSED", "EDITED"].includes(proposal.status)).length}</span>}</button>)}</nav><div className="sidebar-bottom"><div className="security-card"><span className="shield">✦</span><div><strong>Guardrails active</strong><small>Aggregate evidence only</small></div></div><button className="profile-button" onClick={() => void handleLogout()}><span className="avatar">{role === "ADMIN" ? "AD" : role === "STEWARD" ? "DS" : "US"}</span><span><strong>{username || role}</strong><small>{role} · Sign out</small></span><span className="chevron">↗</span></button></div></aside><main className="main-content"><header className="topbar"><div className="breadcrumb"><span>Workspace</span><span>/</span><strong>{view === "overview" ? "Overview" : view === "rules" ? "Rule proposals" : view === "runs" ? "DQ runs" : "Audit history"}</strong></div><div className="topbar-actions"><span className={`mode-badge ${isMockMode ? "mock" : "live"}`}><span />{isMockMode ? "LOCAL MOCK ADAPTER" : "CONNECTED API"}</span><span className="role-badge">{role}</span><button className="icon-button" aria-label="Notifications">♢</button><button className="avatar mini" aria-label="Current user">{role === "ADMIN" ? "AD" : role === "STEWARD" ? "DS" : "US"}</button></div></header><div className="page-container">{!canOperate && <div className="dev-banner"><span>Read-only access</span><span>Your role can inspect evidence and results but cannot change rules or start jobs.</span><code>{role}</code></div>}{isMockMode && <div className="dev-banner"><span>Local development adapter</span><span>Results are deterministic fixtures until the Gate 2 backend is connected.</span><code>VITE_USE_MOCK_API=false</code></div>}{error && <div className="alert error"><strong>Action needs attention</strong><span>{getErrorMessage(error, "Action needs attention")}</span><button onClick={() => setError("")}>×</button></div>}{toast && <div className="alert success"><strong>Done</strong><span>{toast}</span><button onClick={() => setToast("")}>×</button></div>}{activeJob && <ProgressPanel job={activeJob} title={activeJob.type === "INGEST_PROFILE" ? "Building dataset profile" : activeJob.type === "PROPOSE_RULES" ? "Generating rule proposals" : "Running approved checks"} />}{view === "overview" && <OverviewPage dataset={dataset} profile={profile} proposals={proposals} approvedRules={approvedRules.length} loading={loading} busy={Boolean(activeJob)} canOperate={canOperate} onStartAnalysis={() => void startAnalysis()} onRequestProposals={() => void requestProposals()} onNavigate={setView} />}{view === "rules" && <RulesPage proposals={proposals} profileReady={Boolean(profile)} busy={Boolean(activeJob)} canOperate={canOperate} onRequestProposals={() => void requestProposals()} onApprove={(id) => void reviewProposal(id, "approve")} onReject={(id) => void reviewProposal(id, "reject")} onEdit={setEditingProposal} onCreateManual={() => setManualRuleOpen(true)} onRun={() => void runApprovedRules()} />}{view === "runs" && <RunsPage activeRun={activeRun} results={dqResults} approvedCount={approvedRules.length} busy={Boolean(activeJob)} canOperate={canOperate} onRun={() => void runApprovedRules()} />}{view === "audit" && <AuditPage logs={auditLogs} />}</div></main>{editingProposal && <EditDialog proposal={editingProposal} onClose={() => setEditingProposal(null)} onSave={(input) => void saveEdit(input)} />}{manualRuleOpen && <ManualRuleDialog onClose={() => setManualRuleOpen(false)} onSave={(input) => void createManualRule(input)} />}</div>;
}

function OverviewPage({ dataset, profile, proposals, approvedRules, loading, busy, canOperate, onStartAnalysis, onRequestProposals, onNavigate }: { dataset?: Dataset; profile: DatasetProfile | null; proposals: RuleProposal[]; approvedRules: number; loading: boolean; busy: boolean; canOperate: boolean; onStartAnalysis: () => void; onRequestProposals: () => void; onNavigate: (view: View) => void }) {
  const proposalCount = proposals.filter((proposal) => ["PROPOSED", "EDITED"].includes(proposal.status)).length;
  if (!dataset) return <><div className="page-heading"><div><span className="eyebrow">DATA STEWARD WORKSPACE</span><h1>No registered dataset</h1><p>The backend has not registered a Gate 2 dataset yet.</p></div></div><section className="empty-state"><div className="empty-illustration">▦</div><h2>Dataset catalog is empty</h2><p>Connect the dataset registration API to show the approved NYC Yellow Taxi artifact here.</p></section></>;
  return <><div className="page-heading"><div><span className="eyebrow">DATA STEWARD WORKSPACE</span><h1>Good morning, Steward.</h1><p>One clear view of your dataset’s quality signals and the decisions waiting for review.</p></div><div className="heading-date"><span>LAST SYNC</span><strong>{formatTime(dataset.updated_at)}</strong></div></div><section className="dataset-hero"><div className="dataset-icon">⌁</div><div className="dataset-copy"><div className="title-line"><h2>{dataset.name}</h2><StatusPill label={dataset.status === "PROFILE_READY" ? "PROFILE READY" : "REGISTERED"} tone={dataset.status === "PROFILE_READY" ? "success" : "info"} /></div><p>{dataset.description}</p><div className="dataset-meta"><span>▦ {dataset.row_count.toLocaleString()} rows</span><span>◌ {dataset.source_label}</span><span>◇ {dataset.manifest_version}</span></div></div><div className="dataset-action">{canOperate && (!profile ? <button className="button primary" onClick={onStartAnalysis} disabled={busy}>Start analysis <span>→</span></button> : <button className="button secondary" onClick={onRequestProposals} disabled={busy}>Request rule proposals <span>→</span></button>)}</div></section>{!profile ? <section className="empty-state large"><div className="empty-illustration">◌</div><span className="eyebrow">READY WHEN YOU ARE</span><h2>Start with a trusted profile</h2><p>The Cloud Run job will validate the manifest, run the fixed dbt stage and persist aggregate evidence for review.</p>{canOperate && <button className="button primary" onClick={onStartAnalysis} disabled={loading || busy}>Start analysis →</button>}<div className="empty-steps"><span><b>01</b> Ingest</span><span><b>02</b> dbt build</span><span><b>03</b> Profile</span></div></section> : <><div className="section-heading"><div><span className="eyebrow">QUALITY SNAPSHOT</span><h2>Profile at a glance</h2></div><button className="text-button" onClick={() => onNavigate("audit")}>View audit trail →</button></div><section className="stat-grid"><StatCard label="Completeness" value={`${profile.completeness_score}%`} detail="Across profiled columns" tone="green" /><StatCard label="Validity" value={`${profile.validity_score}%`} detail="Contract checks passing" tone="blue" /><StatCard label="Duplicate rate" value={`${profile.duplicate_rate}%`} detail="Fingerprint collisions" tone="amber" /><StatCard label="Review queue" value={`${proposalCount}`} detail="Proposals awaiting Steward" tone="violet" /></section><section className="two-column"><div className="panel"><div className="panel-heading"><div><span className="eyebrow">PROFILE EVIDENCE</span><h3>Column quality</h3></div><span className="panel-caption">{profile.columns.length} tracked fields</span></div><div className="column-list">{profile.columns.map((column) => <div className="column-row" key={column.name}><div className="column-name"><strong>{column.name}</strong><small>{column.data_type}</small></div><div className="column-bar"><span style={{ width: `${Math.max(4, 100 - column.null_rate * 100)}%` }} /></div><strong className={column.null_rate > 0.01 ? "metric-warn" : ""}>{(100 - column.null_rate * 100).toFixed(1)}%</strong></div>)}</div></div><div className="panel next-panel"><div className="panel-heading"><div><span className="eyebrow">NEXT ACTION</span><h3>Review AI proposals</h3></div><span className="spark">✦</span></div><p>Proposals are grounded in the aggregate evidence above. You remain in control of every executable rule.</p>{proposalCount ? <><div className="next-stat"><strong>{proposalCount}</strong><span>typed proposals are ready</span></div><button className="button secondary full" onClick={() => onNavigate("rules")}>Open review queue →</button></> : canOperate && <button className="button secondary full" onClick={onRequestProposals} disabled={busy}>Generate proposals →</button>}</div></section></>}</>;
}

function StatCard({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: string }) { return <div className={`stat-card ${tone}`}><span className="stat-label">{label}</span><strong>{value}</strong><span className="stat-detail">{detail}</span></div>; }

function RulesPage({ proposals, profileReady, busy, canOperate, onRequestProposals, onApprove, onReject, onEdit, onCreateManual, onRun }: { proposals: RuleProposal[]; profileReady: boolean; busy: boolean; canOperate: boolean; onRequestProposals: () => void; onApprove: (id: string) => void; onReject: (id: string) => void; onEdit: (proposal: RuleProposal) => void; onCreateManual: () => void; onRun: () => void }) {
  const pending = proposals.filter((proposal) => ["PROPOSED", "EDITED"].includes(proposal.status)); const approved = proposals.filter((proposal) => proposal.status === "APPROVED");
  return <><div className="page-heading"><div><span className="eyebrow">HUMAN-IN-THE-LOOP</span><h1>Rule proposals</h1><p>Review agent suggestions or author a typed rule manually.</p></div><div className="heading-actions">{canOperate && <button className="button secondary" onClick={onCreateManual}>+ Add manual rule</button>}<button className="button primary" onClick={onRun} disabled={!approved.length || busy || !canOperate}>Run approved rules <span>→</span></button></div></div>{!profileReady ? <section className="empty-state"><div className="empty-illustration">✦</div><h2>Profile first, proposals second</h2><p>Complete the dataset analysis before asking the guarded Agent for proposals.</p>{canOperate && <button className="button secondary" onClick={onCreateManual}>Add manual rule anyway</button>}</section> : !proposals.length ? <section className="empty-state"><div className="empty-illustration">✦</div><h2>No proposals yet</h2><p>Start with an Agent proposal or create a typed rule manually.</p>{canOperate && <div className="dialog-actions"><button className="button secondary" onClick={onCreateManual}>Add manual rule</button><button className="button primary" onClick={onRequestProposals} disabled={busy}>Generate proposals →</button></div>}</section> : <><div className="review-summary"><div><span className="eyebrow">REVIEW QUEUE</span><strong>{pending.length} awaiting decision</strong></div><div className="review-progress"><span style={{ width: `${proposals.length ? ((proposals.length - pending.length) / proposals.length) * 100 : 0}%` }} /></div><span>{approved.length} approved · {proposals.filter((proposal) => proposal.status === "REJECTED").length} rejected</span></div><div className="proposal-list">{proposals.map((proposal) => <ProposalCard key={proposal.id} proposal={proposal} canOperate={canOperate} onApprove={() => onApprove(proposal.id)} onReject={() => onReject(proposal.id)} onEdit={() => onEdit(proposal)} />)}</div></>}</>;
}

function ProposalCard({ proposal, canOperate, onApprove, onReject, onEdit }: { proposal: RuleProposal; canOperate: boolean; onApprove: () => void; onReject: () => void; onEdit: () => void }) { const pending = ["PROPOSED", "EDITED"].includes(proposal.status); const editable = pending || proposal.status === "APPROVED"; const canApprove = proposal.status !== "APPROVED"; const canReject = proposal.status !== "REJECTED"; const tone = proposal.status === "REJECTED" ? "danger" : proposal.status === "APPROVED" ? "success" : "warning"; return <article className={`proposal-card ${proposal.status.toLowerCase()}`}><div className="proposal-top"><div className={`rule-type ${proposal.rule.type}`}><span>✦</span>{proposal.rule.type.replaceAll("_", " ")}</div><StatusPill label={proposal.status} tone={tone} /><span className={`severity ${proposal.severity.toLowerCase()}`}>{proposal.severity} severity</span></div><div className="proposal-main"><div className="proposal-content"><h3>{proposal.title}</h3><p>{proposal.description}</p><div className="rule-code"><span>TYPE</span><code>{formatRule(proposal.rule)}</code></div></div><div className="confidence"><span>CONFIDENCE</span><strong>{Math.round(proposal.confidence * 100)}%</strong><div className="confidence-track"><span style={{ width: `${proposal.confidence * 100}%` }} /></div></div></div><div className="evidence-row"><span className="evidence-label">EVIDENCE</span><span>{proposal.evidence_summary}</span>{proposal.evidence_refs.map((ref) => <code key={ref}>{ref}</code>)}</div>{(editable || proposal.status === "REJECTED") && canOperate && <div className="proposal-actions">{canReject && <button className="button ghost" onClick={onReject}>{proposal.status === "APPROVED" ? "Reject approved rule" : "Reject"}</button>}<button className="button secondary" onClick={onEdit}>{pending ? "Edit" : proposal.status === "APPROVED" ? "Edit approved rule" : "Edit rejected rule"}</button>{canApprove && <button className="button primary" onClick={onApprove}>{proposal.status === "REJECTED" ? "Re-approve rule" : "Approve rule"} <span>→</span></button>}</div>}</article>; }

function RunsPage({ activeRun, results, approvedCount, busy, canOperate, onRun }: { activeRun: DqRun | null; results: DqResult[]; approvedCount: number; busy: boolean; canOperate: boolean; onRun: () => void }) { return <><div className="page-heading"><div><span className="eyebrow">READ-ONLY EXECUTION</span><h1>DQ runs</h1><p>Persisted checks from approved typed rules. Failed results expose bounded IDs only.</p></div><button className="button primary" onClick={onRun} disabled={!approvedCount || busy || !canOperate}>Run approved rules <span>→</span></button></div>{!activeRun ? <section className="empty-state"><div className="empty-illustration">↗</div><h2>No run yet</h2><p>Approve at least one proposal, then execute it through the read-only runner.</p>{canOperate && <button className="button primary" onClick={onRun} disabled={!approvedCount || busy}>Run approved rules →</button>}</section> : <><div className="run-hero"><div><span className="eyebrow">LATEST RUN</span><h2>{activeRun.id}</h2><p>Created {formatTime(activeRun.created_at)} · {activeRun.rule_ids.length} approved rules</p></div><StatusPill label={activeRun.status} tone={activeRun.status === "SUCCEEDED" ? "success" : "info"} /></div>{activeRun.status === "SUCCEEDED" && <div className="stat-grid run-stats"><StatCard label="Checked rows" value={activeRun.total_checked.toLocaleString()} detail="Across approved checks" tone="blue" /><StatCard label="Failed rows" value={activeRun.total_failed.toLocaleString()} detail="Bounded result summary" tone="amber" /><StatCard label="Rules executed" value={`${activeRun.rule_ids.length}`} detail="Approved versions only" tone="green" /><StatCard label="Raw values" value="0" detail="Never returned to browser" tone="violet" /></div>}<div className="panel"><div className="panel-heading"><div><span className="eyebrow">RESULTS</span><h3>Rule outcomes</h3></div><span className="panel-caption">{results.length} checks</span></div>{results.length ? <div className="results-table"><div className="result-header"><span>RULE</span><span>STATUS</span><span>CHECKED</span><span>FAILED</span><span>FAILED IDS</span></div>{results.map((result) => <div className="result-row" key={result.rule_id}><strong>{result.rule_title}</strong><StatusPill label={result.status} tone={result.status === "PASS" ? "success" : "danger"} /><span>{result.checked_count.toLocaleString()}</span><strong className={result.failed_count ? "metric-warn" : ""}>{result.failed_count.toLocaleString()}</strong><code>{result.failed_row_ids.length ? result.failed_row_ids.join(", ") : "—"}</code></div>)}</div> : <div className="table-empty">The runner is preparing bounded results…</div>}</div></>}</>; }

function AuditPage({ logs }: { logs: AuditLog[] }) { return <><div className="page-heading"><div><span className="eyebrow">APPEND-ONLY HISTORY</span><h1>Audit history</h1><p>Every state transition and execution remains observable for the Steward.</p></div><StatusPill label="AUDIT ENABLED" tone="success" /></div><div className="panel"><div className="panel-heading"><div><span className="eyebrow">EVENT STREAM</span><h3>Recent activity</h3></div><span className="panel-caption">{logs.length} events</span></div>{logs.length ? <div className="audit-list">{logs.map((log) => <div className="audit-row" key={log.id}><div className="audit-icon">✓</div><div><strong>{log.summary}</strong><span>{log.action} · {log.entity_type} · {log.actor}</span></div><time>{formatTime(log.created_at)}</time></div>)}</div> : <div className="table-empty">No audit events yet.</div>}</div></>; }

function RuleSpecEditor({ rule, onChange }: { rule: RuleSpec; onChange: (rule: RuleSpec) => void }) {
  const update = (patch: Partial<RuleSpec>) => onChange({ ...rule, ...patch });
  const csv = (values: string[] | undefined) => (values ?? []).join(", ");
  const parseCsv = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
  return <div className="rule-editor"><span className="eyebrow">TYPED RULE PARAMETERS</span><div className="rule-type-readonly"><strong>{rule.type.replaceAll("_", " ")}</strong><code>{formatRule(rule)}</code></div>{rule.type === "not_null" && <label>Column<input value={rule.column ?? ""} onChange={(event) => update({ column: event.target.value })} /></label>}{rule.type === "numeric_range" && <><label>Column<input value={rule.column ?? ""} onChange={(event) => update({ column: event.target.value })} /></label><div className="dialog-fields"><label>Minimum<input type="number" value={rule.min_value ?? ""} onChange={(event) => update({ min_value: event.target.value === "" ? undefined : Number(event.target.value) })} /></label><label>Maximum<input type="number" value={rule.max_value ?? ""} onChange={(event) => update({ max_value: event.target.value === "" ? undefined : Number(event.target.value) })} /></label></div></>}{rule.type === "accepted_values" && <><label>Column<input value={rule.column ?? ""} onChange={(event) => update({ column: event.target.value })} /></label><label>Allowed values<input value={csv(rule.allowed_values)} onChange={(event) => update({ allowed_values: parseCsv(event.target.value) })} /></label></>}{rule.type === "cross_field_comparison" && <><label>Columns<input value={csv(rule.columns)} onChange={(event) => update({ columns: parseCsv(event.target.value) })} /></label><label>Operator<select value={rule.operator ?? "≤"} onChange={(event) => update({ operator: event.target.value })}><option value="≤">≤</option><option value="<">&lt;</option><option value=">=">≥</option><option value=">">&gt;</option></select></label></>}{rule.type === "duplicate_fingerprint" && <label>Fingerprint columns<input value={csv(rule.fingerprint_columns)} onChange={(event) => update({ fingerprint_columns: parseCsv(event.target.value) })} /></label>}</div>;
}

function ManualRuleDialog({ onClose, onSave }: { onClose: () => void; onSave: (input: ManualRuleInput) => void }) {
  const [title, setTitle] = useState(""); const [description, setDescription] = useState(""); const [severity, setSeverity] = useState<RuleProposal["severity"]>("MEDIUM");
  const [type, setType] = useState<RuleSpec["type"]>("not_null"); const [rule, setRule] = useState<RuleSpec>({ type: "not_null" });
  const changeType = (next: RuleSpec["type"]) => { setType(next); setRule({ type: next }); };
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="dialog" role="dialog" aria-modal="true"><div className="dialog-heading"><div><span className="eyebrow">DATA STEWARD AUTHORING</span><h2>Add manual rule</h2></div><button className="icon-button" onClick={onClose} aria-label="Close dialog">×</button></div><p className="muted">Create a typed rule without waiting for the Agent. It enters the review queue and must be approved before execution.</p><label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. Pickup location must be known" /></label><label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} placeholder="Explain the quality expectation" /></label><label>Severity<select value={severity} onChange={(event) => setSeverity(event.target.value as RuleProposal["severity"])}><option>LOW</option><option>MEDIUM</option><option>HIGH</option></select></label><label>Rule type<select value={type} onChange={(event) => changeType(event.target.value as RuleSpec["type"])}><option value="not_null">Not null</option><option value="numeric_range">Numeric range</option><option value="accepted_values">Accepted values</option><option value="cross_field_comparison">Cross-field comparison</option><option value="duplicate_fingerprint">Duplicate fingerprint</option></select></label><RuleSpecEditor rule={rule} onChange={setRule} /><div className="dialog-actions"><button className="button ghost" onClick={onClose}>Cancel</button><button className="button primary" disabled={!title.trim() || !description.trim()} onClick={() => onSave({ title, description, severity, rule })}>Create rule</button></div></section></div>;
}

function EditDialog({ proposal, onClose, onSave }: { proposal: RuleProposal; onClose: () => void; onSave: (input: { title: string; description: string; severity: RuleProposal["severity"]; rule: RuleSpec }) => void }) { const [title, setTitle] = useState(proposal.title); const [description, setDescription] = useState(proposal.description); const [severity, setSeverity] = useState(proposal.severity); const [rule, setRule] = useState<RuleSpec>({ ...proposal.rule, columns: proposal.rule.columns ? [...proposal.rule.columns] : undefined, allowed_values: proposal.rule.allowed_values ? [...proposal.rule.allowed_values] : undefined, fingerprint_columns: proposal.rule.fingerprint_columns ? [...proposal.rule.fingerprint_columns] : undefined }); return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="dialog" role="dialog" aria-modal="true"><div className="dialog-heading"><div><span className="eyebrow">HITL REVIEW</span><h2>Edit proposal</h2></div><button className="icon-button" onClick={onClose} aria-label="Close dialog">×</button></div><p className="muted">Edit the typed specification and metadata. The server remains responsible for validation and compilation.</p><label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={4} /></label><label>Severity<select value={severity} onChange={(event) => setSeverity(event.target.value as RuleProposal["severity"])}><option>LOW</option><option>MEDIUM</option><option>HIGH</option></select></label><RuleSpecEditor rule={rule} onChange={setRule} /><div className="dialog-actions"><button className="button ghost" onClick={onClose}>Cancel</button><button className="button primary" onClick={() => onSave({ title, description, severity, rule })}>Save edit</button></div></section></div>; }

export default App;
