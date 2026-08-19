-- Core evidence contract for Rule Proposer output.
-- HITL review state remains in the existing status/reviewer columns.

ALTER TABLE public.proposed_rules
    ADD COLUMN IF NOT EXISTS rule_name varchar(256),
    ADD COLUMN IF NOT EXISTS business_rationale text,
    ADD COLUMN IF NOT EXISTS proposal_basis varchar(32),
    ADD COLUMN IF NOT EXISTS evidence text, -- Local compatibility
    ADD COLUMN IF NOT EXISTS confidence_breakdown text; -- Local compatibility

UPDATE public.proposed_rules
SET rule_name = COALESCE(rule_name, rule_description),
    business_rationale = COALESCE(business_rationale, ai_reasoning),
    proposal_basis = COALESCE(proposal_basis, 'DATA_PROFILE'),
    evidence = COALESCE(evidence, '{}'),
    confidence_breakdown = COALESCE(
        confidence_breakdown,
        '{"overall": 1.0, "evidence_strength": 1.0, "business_support": 1.0, "sample_representativeness": 1.0, "explanation": "Legacy confidence score"}'
    );

ALTER TABLE public.proposed_rules
    ALTER COLUMN rule_name SET NOT NULL,
    ALTER COLUMN business_rationale SET NOT NULL,
    ALTER COLUMN proposal_basis SET NOT NULL,
    ALTER COLUMN evidence SET NOT NULL,
    ALTER COLUMN confidence_breakdown SET NOT NULL;

ALTER TABLE public.rule_proposals
    ADD COLUMN IF NOT EXISTS rule_name varchar(256),
    ADD COLUMN IF NOT EXISTS business_rationale text,
    ADD COLUMN IF NOT EXISTS proposal_basis varchar(32),
    ADD COLUMN IF NOT EXISTS evidence text, -- Local compatibility
    ADD COLUMN IF NOT EXISTS confidence_breakdown text; -- Local compatibility

UPDATE public.rule_proposals
SET rule_name = COALESCE(rule_name, title),
    business_rationale = COALESCE(business_rationale, evidence_summary),
    proposal_basis = COALESCE(proposal_basis, 'DATA_PROFILE'),
    evidence = COALESCE(evidence, '{}'),
    confidence_breakdown = COALESCE(
        confidence_breakdown,
        '{"overall": 1.0, "evidence_strength": 1.0, "business_support": 1.0, "sample_representativeness": 1.0, "explanation": "Legacy confidence score"}'
    );

ALTER TABLE public.rule_proposals
    ALTER COLUMN rule_name SET NOT NULL,
    ALTER COLUMN business_rationale SET NOT NULL,
    ALTER COLUMN proposal_basis SET NOT NULL,
    ALTER COLUMN evidence SET NOT NULL,
    ALTER COLUMN confidence_breakdown SET NOT NULL;
