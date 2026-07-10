CREATE TABLE IF NOT EXISTS customer_feedback_records (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    submitted_by_user_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rating INTEGER,
    comment TEXT,
    run_id TEXT,
    artifact_id TEXT,
    skill_id TEXT,
    solution_pack_id TEXT,
    onboarding_step_id TEXT,
    missing_skill_name TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_feedback_evaluation_candidates (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_run_id TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    proposed_eval_name TEXT NOT NULL,
    status TEXT NOT NULL,
    human_reviewed_by_user_id TEXT NOT NULL,
    production_change_applied BOOLEAN NOT NULL DEFAULT false,
    reviewed_by_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    evaluation_case_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_solution_pack_feedback_candidates (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    solution_pack_id TEXT NOT NULL,
    requested_skill_name TEXT NOT NULL,
    proposed_change_summary TEXT NOT NULL,
    status TEXT NOT NULL,
    human_reviewed_by_user_id TEXT NOT NULL,
    production_change_applied BOOLEAN NOT NULL DEFAULT false,
    reviewed_by_user_id TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    publication_draft_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_feedback_evaluation_cases (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_candidate_id TEXT NOT NULL,
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_run_id TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    proposed_eval_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    production_change_applied BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_solution_pack_publication_drafts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_candidate_id TEXT NOT NULL,
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    solution_pack_id TEXT NOT NULL,
    requested_skill_name TEXT NOT NULL,
    proposed_change_summary TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    production_change_applied BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_customer_feedback_records_tenant
    ON customer_feedback_records (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_feedback_eval_candidates_tenant
    ON customer_feedback_evaluation_candidates (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_solution_pack_candidates_tenant
    ON customer_solution_pack_feedback_candidates (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_feedback_eval_cases_tenant
    ON customer_feedback_evaluation_cases (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_customer_solution_pack_drafts_tenant
    ON customer_solution_pack_publication_drafts (tenant_id, created_at);

-- taroai:postgresql-only-start
ALTER TABLE customer_feedback_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_feedback_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_feedback_records_tenant_isolation
    ON customer_feedback_records;
CREATE POLICY customer_feedback_records_tenant_isolation
    ON customer_feedback_records
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_feedback_evaluation_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_feedback_evaluation_candidates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_feedback_evaluation_candidates_tenant_isolation
    ON customer_feedback_evaluation_candidates;
CREATE POLICY customer_feedback_evaluation_candidates_tenant_isolation
    ON customer_feedback_evaluation_candidates
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_solution_pack_feedback_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_solution_pack_feedback_candidates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_solution_pack_feedback_candidates_tenant_isolation
    ON customer_solution_pack_feedback_candidates;
CREATE POLICY customer_solution_pack_feedback_candidates_tenant_isolation
    ON customer_solution_pack_feedback_candidates
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_feedback_evaluation_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_feedback_evaluation_cases FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_feedback_evaluation_cases_tenant_isolation
    ON customer_feedback_evaluation_cases;
CREATE POLICY customer_feedback_evaluation_cases_tenant_isolation
    ON customer_feedback_evaluation_cases
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_solution_pack_publication_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_solution_pack_publication_drafts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_solution_pack_publication_drafts_tenant_isolation
    ON customer_solution_pack_publication_drafts;
CREATE POLICY customer_solution_pack_publication_drafts_tenant_isolation
    ON customer_solution_pack_publication_drafts
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end
