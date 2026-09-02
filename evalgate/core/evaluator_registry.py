"""Declarative evaluator catalogue used to validate every execution profile."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluatorSpec:
    name: str
    version: str
    gate: str
    module: str | None
    profiles: tuple[str, ...]
    attribute: str = "evaluate"
    cost_class: str = "offline"
    required_dependencies: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    runner_kind: str = "module"
    critical: bool = False


ALL = ("local", "ci", "nightly", "pre_release")
CI_PLUS = ("ci", "nightly", "pre_release")
LIVE = ("nightly", "pre_release")
RELEASE_ONLY = ("pre_release",)

SPECS = (
    EvaluatorSpec("artifact_provenance_v1", "1.0.0", "preflight", None, (), runner_kind="orchestrator"),
    EvaluatorSpec("regression_engine_v1", "1.0.0", "governance", None, (), runner_kind="orchestrator"),
    EvaluatorSpec("workspace_integrity_v1", "1.0.0", "preflight", None, ALL, runner_kind="workspace"),
    EvaluatorSpec("capability_regression_v1", "1.0.0", "governance", None, ALL, runner_kind="capability"),
    EvaluatorSpec("contract_conformance_v1", "1.0.0", "governance", "evalgate.gates.gate6_governance.contract_conformance", ALL),
    EvaluatorSpec("hitl_integrity_v1", "1.0.0", "governance", "evalgate.gates.gate6_governance.hitl_integrity", CI_PLUS),
    EvaluatorSpec("governed_enum_conformance_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.governed_enum_conformance", CI_PLUS, required_artifacts=("proposals",), critical=True),
    EvaluatorSpec("golden_conformance_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.golden_conformance", CI_PLUS, required_artifacts=("proposals",), critical=True),
    EvaluatorSpec("vacuity_probe_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.vacuity_probe", ALL, required_artifacts=("proposals", "input-dataset"), critical=True),
    EvaluatorSpec("run_outcome_integrity_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.run_outcome_integrity", CI_PLUS, required_artifacts=("run-outcome",), critical=True),
    EvaluatorSpec("served_path_fidelity_v1", "1.0.0", "governance", "evalgate.gates.gate6_governance.served_path_fidelity", ALL),
    EvaluatorSpec("ingest_fidelity_v1", "1.0.0", "input_data", "evalgate.gates.gate4_input_data.ingest_fidelity", CI_PLUS),
    EvaluatorSpec("replay_detection_v1", "1.0.0", "ai_quality", None, CI_PLUS, runner_kind="replay", required_artifacts=("execution-results",), critical=True),
    EvaluatorSpec("authz_probe_v1", "1.0.0", "ai_security", "evalgate.gates.gate2_security.authz_probe", ALL),
    EvaluatorSpec("egress_probe_v1", "1.0.0", "ai_security", "evalgate.gates.gate2_security.egress_probe", CI_PLUS, required_artifacts=("api-transcript", "execution-results"), critical=True),
    EvaluatorSpec("secret_scan_v1", "1.0.0", "ai_security", "evalgate.gates.gate2_security.secret_scan", ALL),
    EvaluatorSpec("default_credential_probe_v1", "1.0.0", "ai_security", "evalgate.gates.gate2_security.default_credential_probe", ALL),
    EvaluatorSpec("asgi_behaviour_probe_v1", "1.0.0", "ai_security", "evalgate.gates.gate2_security.asgi_behaviour_probe", CI_PLUS),
    EvaluatorSpec("policy_resolution_v1", "1.0.0", "governance", "evalgate.gates.gate6_governance.policy_resolution", ALL),
    EvaluatorSpec("config_static_check_v1", "1.0.0", "reliability", "evalgate.gates.gate5_reliability.config_static_check", ALL),
    EvaluatorSpec("multi_dataset_readiness_v1", "1.0.0", "input_data", "evalgate.gates.readiness.multi_dataset_readiness", CI_PLUS),
    EvaluatorSpec("anomaly_logic_probe_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.anomaly_logic_probe", CI_PLUS),
    EvaluatorSpec("sql_compilation_probe_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.sql_compilation_probe", CI_PLUS),
    EvaluatorSpec("profile_accuracy_probe_v1", "1.0.0", "input_data", "evalgate.gates.gate4_input_data.profile_accuracy_probe", CI_PLUS),
    EvaluatorSpec("report_grounding_probe_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.report_grounding_probe", CI_PLUS),
    EvaluatorSpec("upload_probe_v1", "1.0.0", "ai_security", "evalgate.gates.gate2_security.upload_behaviour_probe", CI_PLUS, required_artifacts=("upload-probe",), critical=True),
    EvaluatorSpec("live_sdih_detection_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.live_agent_e2e", LIVE, cost_class="paid", required_artifacts=("live-agent",), critical=True),
    EvaluatorSpec("promptfoo_injection_v1", "1.0.0", "ai_security", "evalgate.gates.gate2_security.prompt_injection_probe", LIVE, cost_class="paid", required_dependencies=("promptfoo",), required_artifacts=("promptfoo-result",), critical=True),
    EvaluatorSpec("geval_domain_v1", "1.0.0", "ai_quality", "evalgate.gates.gate1_ai_quality.live_agent_e2e", LIVE, attribute="evaluate_geval", cost_class="paid", required_dependencies=("deepeval",), required_artifacts=("geval-result",), critical=True),
    EvaluatorSpec("trace_coverage_v1", "1.0.0", "observability", "evalgate.gates.gate3_observability.trace_coverage", CI_PLUS, required_artifacts=("traces",), critical=True),
    EvaluatorSpec("k6_load_v1", "1.0.0", "reliability", "evalgate.gates.gate5_reliability.load_slo", RELEASE_ONLY, cost_class="live-target", required_dependencies=("k6",), required_artifacts=("k6-result",), critical=True),
    EvaluatorSpec("steward_behavior_v1", "1.0.0", "business", "evalgate.gates.gate7_business.steward_outcome", LIVE),
)

REGISTRY = {spec.name: spec for spec in SPECS}


def validate_profile(names: list[str]) -> list[str]:
    return [name for name in names if name not in REGISTRY]
