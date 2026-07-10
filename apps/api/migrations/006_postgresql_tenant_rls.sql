-- taroai:postgresql-only-start
ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspaces FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspaces_tenant_isolation ON workspaces;
CREATE POLICY workspaces_tenant_isolation
    ON workspaces
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS users_tenant_isolation ON users;
CREATE POLICY users_tenant_isolation
    ON users
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS auth_sessions_tenant_isolation ON auth_sessions;
CREATE POLICY auth_sessions_tenant_isolation
    ON auth_sessions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE roles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS roles_tenant_isolation ON roles;
CREATE POLICY roles_tenant_isolation
    ON roles
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_assignments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS role_assignments_tenant_isolation ON role_assignments;
CREATE POLICY role_assignments_tenant_isolation
    ON role_assignments
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS runs_tenant_isolation ON runs;
CREATE POLICY runs_tenant_isolation
    ON runs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS run_events_tenant_isolation ON run_events;
CREATE POLICY run_events_tenant_isolation
    ON run_events
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE idempotency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS idempotency_records_tenant_isolation ON idempotency_records;
CREATE POLICY idempotency_records_tenant_isolation
    ON idempotency_records
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifacts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS artifacts_tenant_isolation ON artifacts;
CREATE POLICY artifacts_tenant_isolation
    ON artifacts
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE storage_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE storage_objects FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS storage_objects_tenant_isolation ON storage_objects;
CREATE POLICY storage_objects_tenant_isolation
    ON storage_objects
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE knowledge_bases ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_bases FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_bases_tenant_isolation ON knowledge_bases;
CREATE POLICY knowledge_bases_tenant_isolation
    ON knowledge_bases
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_documents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_documents_tenant_isolation ON knowledge_documents;
CREATE POLICY knowledge_documents_tenant_isolation
    ON knowledge_documents
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_chunks_tenant_isolation ON knowledge_chunks;
CREATE POLICY knowledge_chunks_tenant_isolation
    ON knowledge_chunks
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_requests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS approval_requests_tenant_isolation ON approval_requests;
CREATE POLICY approval_requests_tenant_isolation
    ON approval_requests
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE memory_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS memory_records_tenant_isolation ON memory_records;
CREATE POLICY memory_records_tenant_isolation
    ON memory_records
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE short_term_memory_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE short_term_memory_reviews FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS short_term_memory_reviews_tenant_isolation ON short_term_memory_reviews;
CREATE POLICY short_term_memory_reviews_tenant_isolation
    ON short_term_memory_reviews
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE skill_registry_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_registry_entries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS skill_registry_entries_tenant_isolation ON skill_registry_entries;
CREATE POLICY skill_registry_entries_tenant_isolation
    ON skill_registry_entries
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE skill_registry_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_registry_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS skill_registry_versions_tenant_isolation ON skill_registry_versions;
CREATE POLICY skill_registry_versions_tenant_isolation
    ON skill_registry_versions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE skill_installations ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_installations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS skill_installations_tenant_isolation ON skill_installations;
CREATE POLICY skill_installations_tenant_isolation
    ON skill_installations
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE solution_pack_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE solution_pack_entries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS solution_pack_entries_tenant_isolation ON solution_pack_entries;
CREATE POLICY solution_pack_entries_tenant_isolation
    ON solution_pack_entries
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE solution_pack_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE solution_pack_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS solution_pack_versions_tenant_isolation ON solution_pack_versions;
CREATE POLICY solution_pack_versions_tenant_isolation
    ON solution_pack_versions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE solution_pack_installations ENABLE ROW LEVEL SECURITY;
ALTER TABLE solution_pack_installations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS solution_pack_installations_tenant_isolation ON solution_pack_installations;
CREATE POLICY solution_pack_installations_tenant_isolation
    ON solution_pack_installations
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_feedback_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_feedback_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_feedback_records_tenant_isolation ON customer_feedback_records;
CREATE POLICY customer_feedback_records_tenant_isolation
    ON customer_feedback_records
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_feedback_evaluation_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_feedback_evaluation_candidates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_feedback_evaluation_candidates_tenant_isolation ON customer_feedback_evaluation_candidates;
CREATE POLICY customer_feedback_evaluation_candidates_tenant_isolation
    ON customer_feedback_evaluation_candidates
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_solution_pack_feedback_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_solution_pack_feedback_candidates FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_solution_pack_feedback_candidates_tenant_isolation ON customer_solution_pack_feedback_candidates;
CREATE POLICY customer_solution_pack_feedback_candidates_tenant_isolation
    ON customer_solution_pack_feedback_candidates
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_feedback_evaluation_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_feedback_evaluation_cases FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_feedback_evaluation_cases_tenant_isolation ON customer_feedback_evaluation_cases;
CREATE POLICY customer_feedback_evaluation_cases_tenant_isolation
    ON customer_feedback_evaluation_cases
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE customer_solution_pack_publication_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_solution_pack_publication_drafts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_solution_pack_publication_drafts_tenant_isolation ON customer_solution_pack_publication_drafts;
CREATE POLICY customer_solution_pack_publication_drafts_tenant_isolation
    ON customer_solution_pack_publication_drafts
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE sso_provider_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sso_provider_configs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sso_provider_configs_tenant_isolation ON sso_provider_configs;
CREATE POLICY sso_provider_configs_tenant_isolation
    ON sso_provider_configs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE scim_provider_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE scim_provider_configs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scim_provider_configs_tenant_isolation ON scim_provider_configs;
CREATE POLICY scim_provider_configs_tenant_isolation
    ON scim_provider_configs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE scim_group_role_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE scim_group_role_mappings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scim_group_role_mappings_tenant_isolation ON scim_group_role_mappings;
CREATE POLICY scim_group_role_mappings_tenant_isolation
    ON scim_group_role_mappings
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE scim_user_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE scim_user_links FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scim_user_links_tenant_isolation ON scim_user_links;
CREATE POLICY scim_user_links_tenant_isolation
    ON scim_user_links
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE scim_import_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE scim_import_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scim_import_records_tenant_isolation ON scim_import_records;
CREATE POLICY scim_import_records_tenant_isolation
    ON scim_import_records
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE trigger_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE trigger_definitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS trigger_definitions_tenant_isolation ON trigger_definitions;
CREATE POLICY trigger_definitions_tenant_isolation
    ON trigger_definitions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_events_tenant_isolation ON audit_events;
CREATE POLICY audit_events_tenant_isolation
    ON audit_events
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE billing_meter_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_meter_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS billing_meter_events_tenant_isolation ON billing_meter_events;
CREATE POLICY billing_meter_events_tenant_isolation
    ON billing_meter_events
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE runtime_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_states FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS runtime_states_tenant_isolation ON runtime_states;
CREATE POLICY runtime_states_tenant_isolation
    ON runtime_states
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE lifecycle_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_policies FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS lifecycle_policies_tenant_isolation ON lifecycle_policies;
CREATE POLICY lifecycle_policies_tenant_isolation
    ON lifecycle_policies
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE legal_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_holds FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_holds_tenant_isolation ON legal_holds;
CREATE POLICY legal_holds_tenant_isolation
    ON legal_holds
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE restore_drill_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE restore_drill_schedules FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS restore_drill_schedules_tenant_isolation ON restore_drill_schedules;
CREATE POLICY restore_drill_schedules_tenant_isolation
    ON restore_drill_schedules
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE restore_drill_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE restore_drill_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS restore_drill_runs_tenant_isolation ON restore_drill_runs;
CREATE POLICY restore_drill_runs_tenant_isolation
    ON restore_drill_runs
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE model_policy_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_policy_scopes FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_policy_scopes_tenant_isolation ON model_policy_scopes;
CREATE POLICY model_policy_scopes_tenant_isolation
    ON model_policy_scopes
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE model_policy_change_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_policy_change_requests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS model_policy_change_requests_tenant_isolation ON model_policy_change_requests;
CREATE POLICY model_policy_change_requests_tenant_isolation
    ON model_policy_change_requests
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE billing_pricing_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_pricing_rules FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS billing_pricing_rules_tenant_isolation ON billing_pricing_rules;
CREATE POLICY billing_pricing_rules_tenant_isolation
    ON billing_pricing_rules
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE billing_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_invoices FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS billing_invoices_tenant_isolation ON billing_invoices;
CREATE POLICY billing_invoices_tenant_isolation
    ON billing_invoices
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE share_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE share_grants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS share_grants_tenant_isolation ON share_grants;
CREATE POLICY share_grants_tenant_isolation
    ON share_grants
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE connector_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_definitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS connector_definitions_tenant_isolation ON connector_definitions;
CREATE POLICY connector_definitions_tenant_isolation
    ON connector_definitions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE tenant_offboarding_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_offboarding_plans FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_offboarding_plans_tenant_isolation ON tenant_offboarding_plans;
CREATE POLICY tenant_offboarding_plans_tenant_isolation
    ON tenant_offboarding_plans
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end
