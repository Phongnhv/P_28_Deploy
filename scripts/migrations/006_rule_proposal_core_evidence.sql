-- Core evidence contract for Rule Proposer output.
-- HITL review state remains in the existing status/reviewer columns.

ALTER TABLE public.proposed_rules
    ADD COLUMN IF NOT EXISTS rule_name varchar(256),
    ADD COLUMN IF NOT EXISTS business_rationale text,
    ADD COLUMN IF NOT EXISTS proposal_basis varchar(32),
    ADD COLUMN IF NOT EXISTS evidence jsonb,
    ADD COLUMN IF NOT EXISTS parameter_provenance jsonb,
    ADD COLUMN IF NOT EXISTS assumptions jsonb,
    ADD COLUMN IF NOT EXISTS confidence_breakdown jsonb;

UPDATE public.proposed_rules
SET rule_name = COALESCE(rule_name, rule_description),
    business_rationale = COALESCE(business_rationale, ai_reasoning),
    proposal_basis = COALESCE(proposal_basis, 'DATA_PROFILE'),
    evidence = COALESCE(evidence, '{}'::jsonb),
    parameter_provenance = COALESCE(parameter_provenance, '[]'::jsonb),
    assumptions = COALESCE(assumptions, '[]'::jsonb),
    confidence_breakdown = COALESCE(
        confidence_breakdown,
        jsonb_build_object(
            'overall', confidence_score,
            'evidence_strength', confidence_score,
            'business_support', confidence_score,
            'sample_representativeness', confidence_score,
            'explanation', 'Legacy confidence score'
        )
    );

ALTER TABLE public.proposed_rules
    ALTER COLUMN rule_name SET NOT NULL,
    ALTER COLUMN business_rationale SET NOT NULL,
    ALTER COLUMN proposal_basis SET NOT NULL,
    ALTER COLUMN evidence SET NOT NULL,
    ALTER COLUMN parameter_provenance SET NOT NULL,
    ALTER COLUMN assumptions SET NOT NULL,
    ALTER COLUMN confidence_breakdown SET NOT NULL;

ALTER TABLE public.rule_proposals
    ADD COLUMN IF NOT EXISTS rule_name varchar(256),
    ADD COLUMN IF NOT EXISTS business_rationale text,
    ADD COLUMN IF NOT EXISTS proposal_basis varchar(32),
    ADD COLUMN IF NOT EXISTS evidence jsonb,
    ADD COLUMN IF NOT EXISTS parameter_provenance jsonb,
    ADD COLUMN IF NOT EXISTS assumptions jsonb,
    ADD COLUMN IF NOT EXISTS confidence_breakdown jsonb;

UPDATE public.rule_proposals
SET rule_name = COALESCE(rule_name, title),
    business_rationale = COALESCE(business_rationale, evidence_summary),
    proposal_basis = COALESCE(proposal_basis, 'DATA_PROFILE'),
    evidence = COALESCE(evidence, '{}'::jsonb),
    parameter_provenance = COALESCE(parameter_provenance, '[]'::jsonb),
    assumptions = COALESCE(assumptions, '[]'::jsonb),
    confidence_breakdown = COALESCE(
        confidence_breakdown,
        jsonb_build_object(
            'overall', confidence,
            'evidence_strength', confidence,
            'business_support', confidence,
            'sample_representativeness', confidence,
            'explanation', 'Legacy confidence score'
        )
    );

ALTER TABLE public.rule_proposals
    ALTER COLUMN rule_name SET NOT NULL,
    ALTER COLUMN business_rationale SET NOT NULL,
    ALTER COLUMN proposal_basis SET NOT NULL,
    ALTER COLUMN evidence SET NOT NULL,
    ALTER COLUMN parameter_provenance SET NOT NULL,
    ALTER COLUMN assumptions SET NOT NULL,
    ALTER COLUMN confidence_breakdown SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_proposed_rules_evidence_gin
    ON public.proposed_rules USING gin (evidence);
