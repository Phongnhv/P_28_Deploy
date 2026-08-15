# dbt Test Artifact Object Storage

## Architecture

Generated dbt YAML is stored as a run-scoped object at
`dbt-tests/runs/{test_run_id}/generated_dq_tests.yml`. MinIO provides the S3 API
locally and AWS S3 provides it in production. The checked-in `dbt_project` is an
immutable template; each runner copies it into a temporary workspace, downloads
and verifies the matching YAML, runs dbt, and removes the workspace.

## Runtime behavior

- The generator writes `output/test_generator/{test_run_id}/generated_dq_tests.yml`
  as a deterministic local trace, calculates SHA-256, uploads the YAML, and stores
  bucket/key/checksum/size/version metadata in the audit record.
- The runner downloads only the object referenced by graph state and verifies its
  size and checksum before writing it into the temporary dbt project.
- Production upload or download failures fail the run closed. Local, development,
  and test environments may use only the exact run-scoped trace after checksum
  validation.
- Existing generated SQL checks remain the persisted result source; object storage
  changes YAML transport and dbt workspace isolation only.

## Operations

- Local Docker Compose creates `ridepulse-dbt-artifacts` and configures a 30-day
  expiration rule.
- Provision the AWS bucket before deployment with block-public-access enabled,
  default encryption, versioning if audit policy requires it, and a 30-day lifecycle.
- Grant the runtime identity only `s3:GetObject`, `s3:PutObject`, and the minimum
  bucket-list/head permissions for the configured `dbt-tests/` prefix.
- Do not set `OBJECT_STORAGE_ENDPOINT_URL` or MinIO credentials in production.
  Use the AWS SDK credential chain or workload-provided credentials.

## Verification

Test upload/download metadata, checksum rejection, exact-run local fallback,
production fail-closed behavior, temporary workspace cleanup, and concurrent runs
using distinct object keys and workspaces. Run the existing graph, execution-node,
API, and dbt project tests to confirm compatibility.
