# dbt YAML Agentic Loop

The execution graph treats `generated_dq_tests.yml` as its quality gate:

`test_generator -> validate_dbt_project -> (llm_dbt_repair -> validate_dbt_project)* -> test_runner`

Validation performs safe YAML parsing and, when the executable is available,
`dbt parse`. Three failed repair attempts terminate the run before `dbt test` and
mark the test run `FAILED`. The repair node may only return YAML and enforces the
model/column scope from `approved_rules`.

SQL `EXPLAIN` validation remains available as legacy code but is no longer part of
the execution graph. Deterministically generated SQL remains the compatibility
metrics source until dbt `run_results.json` is mapped into the existing result model.
