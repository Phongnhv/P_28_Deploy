"""Produce one provenance-bound bundle through the authenticated FastAPI path.

No manifest is written unless every product stage finishes.  The finalized
manifest printed by this command is the sole input to non-local EvalGate runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pandas as pd
from fastapi.testclient import TestClient

from evalgate.core.artifact_provenance import sha256_file
from evalgate.corpus.generator import generate
from evalgate.schemas.artifact_manifest import ArtifactManifestV2, ArtifactRecord, ModelIdentity

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1"


def _git(command: str) -> str:
    return subprocess.run(["git", *command.split()], cwd=PROJECT_ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def _hash_paths(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


@contextmanager
def _released_engine() -> Iterator[None]:
    """Close the product's SQLAlchemy engine before the run directory is removed.

    The runtime database lives inside a TemporaryDirectory. On Windows an open
    SQLite handle makes its cleanup raise PermissionError, and that error replaces
    whatever the run actually failed on -- which is how a plain 409 from the
    product arrived here as an unreadable rmtree traceback. Entered after
    ``runtime`` so its exit runs first, and on the failure path as well.
    """
    try:
        yield
    finally:
        import src.services.rule_store as rule_store

        engine = getattr(rule_store, "_engine", None)
        if engine is not None:
            engine.dispose()
        rule_store._engine = None


class BundleWriter:
    def __init__(self, root: Path, run_id: str, dataset_id: str) -> None:
        self.root, self.run_id, self.dataset_id = root, run_id, dataset_id
        self.records: list[ArtifactRecord] = []

    def add_file(self, name: str, artifact_type: str, path: Path, *, producer: str,
                 media_type: str) -> None:
        resolved = path.resolve()
        resolved.relative_to(self.root.resolve())
        relative = resolved.relative_to(self.root.resolve()).as_posix()
        if any(item.name == name or item.relative_path == relative for item in self.records):
            raise RuntimeError(f"duplicate artifact record: {name} / {relative}")
        self.records.append(ArtifactRecord(
            name=name, type=artifact_type, relative_path=relative, sha256=sha256_file(resolved),
            media_type=media_type, producer=producer, run_id=self.run_id,
            dataset_id=self.dataset_id, created_at=datetime.now(UTC),
        ))

    def json(self, name: str, artifact_type: str, relative: str, payload: Any,
             *, producer: str = "evalgate.product_run") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self.add_file(name, artifact_type, path, producer=producer, media_type="application/json")
        return path


def _redact(body: Any) -> Any:
    if isinstance(body, dict):
        return {key: ("***redacted***" if key.lower() in {"password", "csrf_token", "session_id"}
                      else _redact(value)) for key, value in body.items()}
    if isinstance(body, list):
        return [_redact(item) for item in body[:100]]
    return body


class ServedApi:
    def __init__(self, client: TestClient) -> None:
        self.client, self.csrf = client, ""
        self.transcript: list[dict[str, Any]] = []

    def request(self, method: str, path: str, *, expected: tuple[int, ...] = (200,), **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if method.upper() not in {"GET", "HEAD", "OPTIONS"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        response = self.client.request(method, path, headers=headers, **kwargs)
        try:
            body = response.json()
        except Exception:
            body = {"media_type": response.headers.get("content-type", ""), "bytes": len(response.content)}
        self.transcript.append({"sequence": len(self.transcript) + 1, "method": method.upper(),
                                "path": path, "status_code": response.status_code,
                                "security": {
                                    "session_cookie_present": bool(self.client.cookies),
                                    "csrf_header_present": "X-CSRF-Token" in headers,
                                },
                                "response": _redact(body)})
        if response.status_code not in expected:
            raise RuntimeError(f"{method} {path} returned {response.status_code}: {body}")
        return response

    def login(self) -> None:
        body = self.request("POST", f"{API}/session",
                            json={"username": "steward", "password": "steward"}).json()
        if body.get("role") != "STEWARD" or not self.client.cookies:
            raise RuntimeError("login did not establish a Steward session")
        self.csrf = body["csrf_token"]

    def wait_job(self, job_id: str) -> dict[str, Any]:
        for _ in range(120):
            body = self.request("GET", f"{API}/jobs/{job_id}").json()
            if body["status"] == "SUCCEEDED":
                return body
            if body["status"] == "FAILED":
                raise RuntimeError(f"product job failed: {body}")
        raise RuntimeError(f"product job did not complete: {job_id}")


def _proposal_evidence(proposals: list[dict[str, Any]], dataset_id: str,
                       run_id: str) -> dict[str, Any]:
    types = {"not_null": "NOT_NULL", "numeric_range": "RANGE",
             "accepted_values": "ACCEPTED_VALUES", "cross_field": "CROSS_FIELD",
             "unique": "UNIQUE", "row_count": "ROW_COUNT"}
    rows = []
    for proposal in proposals:
        rule = proposal.get("rule") or {}
        parameters = {"min": rule.get("min_value"), "max": rule.get("max_value"),
                      "accepted_values": rule.get("allowed_values"), "columns": rule.get("columns"),
                      "operator": rule.get("operator")}
        rows.append({**proposal, "run_id": run_id, "dataset_id": dataset_id,
                     "rule_type": types.get(rule.get("type"), str(rule.get("type", "")).upper()),
                     "column": rule.get("column"),
                     "parameters": {key: value for key, value in parameters.items() if value is not None}})
    return {"run_id": run_id, "dataset_id": dataset_id, "proposed_rules": rows}


def _served_run(bundle: Path, run_id: str, frame: pd.DataFrame) -> tuple[str, BundleWriter]:
    input_path = bundle / "input" / "dataset.csv"
    frame.to_csv(input_path, index=False)
    runtime = TemporaryDirectory(prefix="evalgate-product-")
    runtime_root = Path(runtime.name)
    env = {
        "APP_ENV": "test", "AGENT_MODE": "graph", "DQ_EXECUTION_BACKEND": "local",
        "DATABASE_URL": f"sqlite:///{(runtime_root / 'runtime.db').resolve().as_posix()}",
        "OUTPUT_DIR": str((runtime_root / "product-output").resolve()),
        "RESULTS_DIR": str((runtime_root / "product-output").resolve()),
        "UPLOAD_DIR": str((runtime_root / "uploads").resolve()),
        "OBJECT_STORAGE_ENABLED": "false",
        "EVALGATE_DETERMINISTIC_LLM": "1",
        "EVALGATE_LLM_TRACE_PATH": str((bundle / "traces" / "llm-invocations.jsonl").resolve()),
        "DISABLE_TRACING": "1", "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false",
        "LANGCHAIN_API_KEY": "", "LANGSMITH_API_KEY": "",
    }
    with runtime, _environment(env), _released_engine():
        from src.config import get_settings
        get_settings.cache_clear()
        import src.services.rule_store as rule_store
        rule_store._engine = None
        from src.main import app

        with TestClient(app) as client:
            api = ServedApi(client)
            api.login()
            invalid = api.request("POST", f"{API}/datasets/import", expected=(415,),
                                  files={"file": ("payload.exe", b"x", "application/octet-stream")})
            empty = api.request("POST", f"{API}/datasets/import", expected=(422,),
                                files={"file": ("empty.csv", b"", "text/csv")})
            uploaded = api.request("POST", f"{API}/datasets/import",
                                   files={"file": ("frozen-input.csv", input_path.read_bytes(), "text/csv")}).json()
            dataset_id = uploaded["dataset"]["id"]
            api.wait_job(uploaded["job"]["job_id"])
            profile = api.request("GET", f"{API}/datasets/{dataset_id}/profile").json()

            workflow = api.request("POST", f"{API}/datasets/{dataset_id}/workflows?fresh=true").json()
            workflow_id = workflow["id"]
            job = api.request("POST", f"{API}/workflows/{workflow_id}/steps/UNDERSTAND_DATA",
                              headers={"Idempotency-Key": f"{run_id}-understand"}).json()
            api.wait_job(job["job_id"])
            artifacts = api.request("GET", f"{API}/workflows/{workflow_id}/artifacts").json()
            semantic = next(item for item in artifacts if item["type"] == "SEMANTIC_CONTRACT")
            # The generic /workflow-artifacts/{id}/review path only marks the artifact
            # approved and then calls navigate_forward, which requires PROPOSE_RULES to
            # already be READY -- and nothing but confirm_semantic_contract sets that
            # flag. The served path a steward actually walks is this endpoint, which
            # carries the version check that makes the confirmation race-safe.
            confirmed = api.request(
                "POST",
                f"{API}/workflows/{workflow_id}/semantic-contract/confirm",
                json={
                    "artifact_id": semantic["id"],
                    "expected_version": semantic["version"],
                    "contract": semantic["payload"],
                    "review_note": "EvalGate deterministic semantic review",
                },
            ).json()
            semantic = confirmed["artifact"]

            job = api.request("POST", f"{API}/workflows/{workflow_id}/steps/PROPOSE_RULES",
                              headers={"Idempotency-Key": f"{run_id}-propose"}).json()
            api.wait_job(job["job_id"])
            proposals = api.request("GET", f"{API}/rule-proposals?dataset_id={dataset_id}&workflow_run_id={workflow_id}").json()
            if not proposals:
                raise RuntimeError("LangGraph proposal stage returned no reviewable proposals")
            decisions = []
            for proposal in proposals:
                reviewed = api.request("PATCH", f"{API}/rule-proposals/{proposal['id']}",
                                       json={"action": "approve"}).json()
                decisions.append({"proposal_id": proposal["id"], "action": "approve",
                                  "status": reviewed["status"], "actor_source": "authenticated-session"})
            artifacts = api.request("GET", f"{API}/workflows/{workflow_id}/artifacts").json()
            rule_set = next(item for item in artifacts if item["type"] == "RULE_SET" and not item["temporary"])
            reviewed_artifact = api.request("POST", f"{API}/workflow-artifacts/{rule_set['id']}/review",
                                            json={"action": "approve", "comment": "deterministic HITL"}).json()
            for step in ("PUBLISH_RULESET", "RUN_CHECKS"):
                job = api.request("POST", f"{API}/workflows/{workflow_id}/steps/{step}",
                                  headers={"Idempotency-Key": f"{run_id}-{step.lower()}"}).json()
                api.wait_job(job["job_id"])
            execution_artifacts = api.request("GET", f"{API}/workflows/{workflow_id}/artifacts").json()
            current_dq = next(item for item in execution_artifacts
                              if item["type"] == "DQ_RUN" and not item["temporary"])
            dq_run_id = current_dq["payload"]["run_id"]
            dq_run_api = api.request("GET", f"{API}/dq-runs/{dq_run_id}").json()
            dq_results_api = api.request("GET", f"{API}/dq-runs/{dq_run_id}/results").json()

            job = api.request("POST", f"{API}/workflows/{workflow_id}/steps/ANALYZE_REPORT",
                              headers={"Idempotency-Key": f"{run_id}-analyze-report"}).json()
            api.wait_job(job["job_id"])
            final_workflow = api.request("GET", f"{API}/workflows/{workflow_id}").json()
            final_artifacts = api.request("GET", f"{API}/workflows/{workflow_id}/artifacts").json()
            anomaly_artifact = next(item for item in final_artifacts if item["type"] == "ANOMALY_REPORT" and not item["temporary"])
            ruleset_artifact = next(item for item in final_artifacts if item["type"] == "PUBLISHED_RULESET" and not item["temporary"])
            from src.services.node_event_stream import broker
            with broker._lock:
                node_events = list(broker._buffer.get(workflow_id, ()))

        trace_path = bundle / "traces" / "llm-invocations.jsonl"
        invocations = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line]
        # The proposer now asks for CandidateTableRuleDraft and binds the server-owned
        # candidate fields back afterwards. Either name proves the same thing: a
        # structured model call produced the rules, rather than a deterministic
        # fallback producing them without one.
        proposal_schemas = {"TableRuleProposal", "CandidateTableRuleDraft"}
        if not any(item.get("schema") in proposal_schemas for item in invocations):
            raise RuntimeError("no structured LLM invocation proves the LangGraph proposal path")
        if final_workflow.get("current_step") != "ANALYZE_REPORT":
            raise RuntimeError(f"workflow did not reach its terminal state: {final_workflow}")

        writer = BundleWriter(bundle, run_id, dataset_id)
        writer.add_file("input-dataset", "input-dataset", input_path,
                        producer="served upload fixture", media_type="text/csv")
        writer.json("api-transcript", "api-transcript", "api/transcript.json",
                    {"run_id": run_id, "dataset_id": dataset_id, "requests": api.transcript})
        writer.json("dataset-profile", "dataset-profile", "profile/profile.json", profile,
                    producer="FastAPI ingestion profile")
        writer.json("semantic-contract", "semantic-contract", "semantic/contract.json", semantic,
                    producer="LangGraph dataset-understanding")
        proposal_document = _proposal_evidence(proposals, dataset_id, run_id)
        writer.json("proposals", "proposals", "proposals/proposals.json",
                    proposal_document, producer="LangGraph proposal workflow")
        writer.json("review-decisions", "review-decisions", "review/decisions.json",
                    {"run_id": run_id, "dataset_id": dataset_id, "decisions": decisions,
                     "artifact": reviewed_artifact}, producer="authenticated Steward API")
        writer.json("ruleset", "ruleset", "ruleset/ruleset.json", ruleset_artifact,
                    producer="workflow publish step")
        proposal_by_rule_version = {
            f"rv_{proposal['id']}": proposal for proposal in proposal_document["proposed_rules"]
        }
        writer.json("execution-results", "execution-results", "execution/results.json", {
            "run_id": run_id, "dataset_id": dataset_id, "workflow_run_id": workflow_id,
            "status": dq_run_api.get("status"), "total_rows": profile["row_count"],
            "test_results": [{**row,
                              "column": (proposal_by_rule_version.get(row["rule_id"]) or {}).get("column"),
                              "rule_type": (proposal_by_rule_version.get(row["rule_id"]) or {}).get("rule_type"),
                              "violation_count": row.get("failed_count", 0),
                              "total_rows": row.get("checked_count", profile["row_count"]),
                              "sample_failures": [], "sample_refs": row.get("failed_row_ids", [])}
                             for row in dq_results_api],
        }, producer="typed rule execution workflow")
        writer.json("anomaly-report", "anomaly-report", "anomaly/report.json", anomaly_artifact,
                    producer="LangGraph anomaly workflow")
        writer.add_file("llm-invocations", "llm-invocations", trace_path,
                        producer="deterministic structured LLM", media_type="application/x-ndjson")
        node_trace_path = bundle / "traces" / "node-events.jsonl"
        node_trace_path.write_text("".join(json.dumps(event, default=str) + "\n" for event in node_events),
                                   encoding="utf-8")
        writer.add_file("node-traces", "traces", node_trace_path,
                        producer="LangGraph node broker", media_type="application/x-ndjson")
        writer.json("upload-probe", "upload-probe", "api/upload-probe.json", {
            "run_id": run_id, "dataset_id": dataset_id, "executed_cases": 2,
            "accepted_malicious": int(invalid.status_code < 400) + int(empty.status_code < 400),
            "cases": [{"name": "unsupported-extension", "status": invalid.status_code},
                      {"name": "empty-upload", "status": empty.status_code}],
        })
        writer.json("run-outcome", "run-outcome", "execution/run-outcome.json", {
            "run_id": run_id, "dataset_id": dataset_id,
            "runs": [{"run_id": run_id, "workflow": "served-rule-proposer",
                      "started_at": datetime.now(UTC),
                      "stages": [item["key"] for item in final_workflow["steps"] if item["status"] == "COMPLETED"],
                      "reached_terminal": True, "output_count": len(proposals),
                      "schema_rejections": 0, "schema_accepted": len(proposals), "errors": []}],
        })
        get_settings.cache_clear()
        if rule_store._engine is not None:
            rule_store._engine.dispose()
        rule_store._engine = None
        return dataset_id, writer


def create_bundle(out_dir: Path, *, profile: str = "ci", suite: str = "frozen-v1") -> Path:
    git_sha = _git("rev-parse HEAD")
    workspace_dirty = bool(_git("status --porcelain"))
    if profile != "local" and workspace_dirty:
        raise RuntimeError("non-local product runs require a clean Git revision")
    run_id = f"product-{uuid.uuid4().hex}"
    bundle = (out_dir / run_id).resolve()
    if bundle.exists():
        raise RuntimeError(f"refusing to reuse artifact bundle: {bundle}")
    for name in ("input", "api", "profile", "semantic", "proposals", "review", "ruleset",
                 "execution", "anomaly", "traces"):
        (bundle / name).mkdir(parents=True, exist_ok=False)
    frame = generate("corpus-nyc-taxi-50k", rows=5_000)
    # Product startup currently writes lifecycle messages to stdout. Keep stdout a
    # machine-readable one-line manifest contract for CI callers.
    with redirect_stdout(sys.stderr):
        dataset_id, writer = _served_run(bundle, run_id, frame)
    input_record = next(record for record in writer.records if record.type == "input-dataset")
    manifest = ArtifactManifestV2(
        schema_version="2.0", finalized=True,
        run_id=run_id, git_sha=git_sha, workspace_dirty=workspace_dirty,
        created_at=datetime.now(UTC), dataset_id=dataset_id,
        dataset_fingerprint=input_record.sha256,
        schema_fingerprint=_json_hash([{"name": name, "dtype": str(dtype)} for name, dtype in frame.dtypes.items()]),
        model=ModelIdentity(provider="evalgate", name="structured-fake-v1", mode="deterministic-test"),
        prompt_hash=_hash_paths(list((PROJECT_ROOT / "src" / "agents" / "nodes").glob("*.py"))),
        policy_hash=_hash_paths(list((PROJECT_ROOT / "evalgate" / "policies").glob("*.yaml"))),
        config_hash=_hash_paths([PROJECT_ROOT / "evalgate" / "config" / "profiles.yaml",
                                PROJECT_ROOT / "evalgate" / "core" / "evaluator_registry.py"]),
        workflow="served-fastapi-rule-workflow", product_version="1.0.0",
        artifacts=tuple(writer.records),
    )
    temporary, target = bundle / ".manifest.json.tmp", bundle / "manifest.json"
    temporary.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["local", "ci", "nightly", "pre_release"], default="ci")
    parser.add_argument("--suite", choices=["frozen-v1"], default="frozen-v1")
    parser.add_argument("--out", default="output/evalgate-runs")
    args = parser.parse_args()
    print(create_bundle(Path(args.out), profile=args.profile, suite=args.suite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
