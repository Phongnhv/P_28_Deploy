# Golden dataset

A golden case is a written-down expectation about what the agent should produce in
a situation whose correct answer is known **before** the agent runs.

This exists because SDIH, on its own, answers only one question — *did the agent
find the injected defect?* — and the project's real failures have not been failures
of detection. They have been failures of **judgement**: proposing a rule whose
threshold came from the data it was supposed to judge, putting a UNIQUE constraint
on a surrogate key that is unique by construction, or writing a business rationale
full of column names that the system prompt explicitly forbids. None of those show
up as a missed defect. All of them show up here.

## Three tiers, and why they are separate

| Tier | Question it answers | Cost | Can it be a baseline? |
|---|---|---|---|
| **1** `tier1_sdih/` | Which cells are defective, and of what class? | $0 | yes — fingerprinted |
| **2** `tier2_rules/` | Was the *right rule* proposed, sourced from the right place? | $0 | yes — deterministic |
| **3** `tier3_llm/` | Did the generated text obey its own system prompt? | $0 | yes — deterministic |

Tier 3 deserves a note. It is normally where an LLM judge appears, and it does not
here. Two of the prompt's instructions are literal enough to check with a string
operation: *"do not use technical variable names in `business_rationale`"* and
*"cite concrete figures in `ai_reasoning`"*. Checking those with a model would be
slower, cost money, and — worse — produce a baseline that drifts on its own. A
drifting baseline cannot detect drift in anything else.

Whether an explanation is *good* is a genuinely subjective question. That belongs
to a separate LLM-judge evaluator, run at pre-release, and deliberately not part of
the release gate.

## Layout

```text
golden/
├── manifest.yaml              seed, row cap, fingerprint + sha256 per snapshot
├── freeze.py                  writes tier 1 and verifies it
├── schema.py                  the case format and the eight assertion types
├── tier1_sdih/*.labels.json   frozen SDIH ground truth, one file per archetype
├── tier2_rules/*.cases.yaml   rule-level expectations
└── tier3_llm/*.cases.yaml     prompt-compliance expectations
```

## Working with it

```bash
python -m evalgate.golden.freeze            # regenerate tier 1 and the manifest
python -m evalgate.golden.freeze --verify   # confirm nothing drifted, write nothing
```

`--verify` re-derives every label set from its seed and compares fingerprints, so a
snapshot that was hand-edited or left stale is caught rather than trusted. The
snapshot is for review and diffing; the generator remains the source of truth.

## Rules for changing a case

1. **A case must cite a source.** Every case carries a `source:` pointing at the
   document, contract or code that the expectation comes from. A case with no source
   is an opinion, and an opinion must not be able to block a release.

2. **Never edit a label to make a test pass.** That is the one change this whole
   apparatus exists to prevent. If the expectation is wrong, change the expectation
   in a pull request that says why, and accept that stored baselines scored against
   the old labels are no longer comparable.

3. **A case may be written to fail.** `GC-E5-UNIQUE-ON-BUSINESS-KEY` fails today, on
   purpose: it states that duplicate detection must not rely on a surrogate key. A
   golden set describes what should be true, not what currently is.

4. **Changing tier 1 bumps the version.** The `manifest.yaml` fingerprint is what
   makes two runs comparable. If it moves, older baselines must be discarded rather
   than compared against.

## PII

Six of the seven archetypes are synthetic, so no real personal data is involved.
`corpus-synth-clinical` and `corpus-synth-hr` contain columns that *look* like PII
specifically so the PII classifier has something to detect; the values are
generated. `corpus-nyc-taxi-50k` is the public NYC TLC fixture.

Tier 2 and tier 3 cases must never embed a cell value from a real dataset. They
refer to columns, rule types and policy values only.

## Ownership

| Tier | Owner | Why |
|---|---|---|
| 1 | automatic (SDIH + `verifier.py`) | derived, then machine-verified |
| 2 | data engineer | rule shape and parameter provenance are engineering decisions |
| 3 | data steward | whether an explanation is usable is the steward's call |
