CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    provider_id TEXT,
    model_id TEXT,
    reasoning_effort TEXT,
    sandbox_session_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_threads_workspace_updated
    ON chat_threads (tenant_id, workspace_id, updated_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    thread_id TEXT NOT NULL REFERENCES chat_threads(id),
    sequence BIGINT NOT NULL,
    created_by_user_id TEXT REFERENCES users(id),
    role TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL,
    dispatch_status TEXT NOT NULL DEFAULT 'ready',
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
    resource_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_sequence
    ON chat_messages (tenant_id, thread_id, sequence);

CREATE INDEX IF NOT EXISTS idx_chat_messages_dispatch_queue
    ON chat_messages (tenant_id, thread_id, dispatch_status, sequence);

CREATE TABLE IF NOT EXISTS agent_cycles (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    thread_id TEXT REFERENCES chat_threads(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    iteration INTEGER NOT NULL,
    plan_revision INTEGER NOT NULL DEFAULT 1,
    decision JSONB,
    verifier_result JSONB,
    budget_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (run_id, iteration)
);

CREATE INDEX IF NOT EXISTS idx_agent_cycles_run_iteration
    ON agent_cycles (tenant_id, run_id, iteration);

CREATE TABLE IF NOT EXISTS agent_actions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    thread_id TEXT REFERENCES chat_threads(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    cycle_id TEXT NOT NULL REFERENCES agent_cycles(id),
    action_key TEXT NOT NULL,
    decision JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    observation JSONB,
    failure_class TEXT,
    lease_owner_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (run_id, action_key)
);

CREATE INDEX IF NOT EXISTS idx_agent_actions_run_cycle
    ON agent_actions (tenant_id, run_id, cycle_id);

CREATE INDEX IF NOT EXISTS idx_agent_actions_lease_recovery
    ON agent_actions (tenant_id, status, lease_expires_at, id);

CREATE TABLE IF NOT EXISTS agent_checkpoints (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    thread_id TEXT REFERENCES chat_threads(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    cycle_id TEXT REFERENCES agent_cycles(id),
    sequence BIGINT NOT NULL,
    last_committed_action_id TEXT REFERENCES agent_actions(id),
    state_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    sandbox_checkpoint_ref TEXT,
    checksum TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_run_sequence
    ON agent_checkpoints (tenant_id, run_id, sequence);

ALTER TABLE runs ADD COLUMN thread_id TEXT REFERENCES chat_threads(id);
ALTER TABLE runs ADD COLUMN trigger_message_id TEXT REFERENCES chat_messages(id);
ALTER TABLE runs ADD COLUMN provider_id TEXT;
ALTER TABLE runs ADD COLUMN model_id TEXT;
ALTER TABLE runs ADD COLUMN reasoning_effort TEXT;
ALTER TABLE runs ADD COLUMN resource_refs JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE run_events ADD COLUMN thread_id TEXT REFERENCES chat_threads(id);
ALTER TABLE run_events ADD COLUMN thread_sequence BIGINT;
ALTER TABLE runtime_states ADD COLUMN state_payload JSONB NOT NULL DEFAULT '{}'::jsonb;

-- taroai:postgresql-only-start
CREATE INDEX IF NOT EXISTS idx_runs_thread_created
    ON runs (tenant_id, thread_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_thread_sequence
    ON run_events (thread_id, thread_sequence)
    WHERE thread_id IS NOT NULL AND thread_sequence IS NOT NULL;

ALTER TABLE chat_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_threads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_threads_tenant_isolation ON chat_threads;
CREATE POLICY chat_threads_tenant_isolation
    ON chat_threads
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_messages_tenant_isolation ON chat_messages;
CREATE POLICY chat_messages_tenant_isolation
    ON chat_messages
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_cycles ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_cycles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_cycles_tenant_isolation ON agent_cycles;
CREATE POLICY agent_cycles_tenant_isolation
    ON agent_cycles
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_actions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_actions_tenant_isolation ON agent_actions;
CREATE POLICY agent_actions_tenant_isolation
    ON agent_actions
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));

ALTER TABLE agent_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_checkpoints FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_checkpoints_tenant_isolation ON agent_checkpoints;
CREATE POLICY agent_checkpoints_tenant_isolation
    ON agent_checkpoints
    USING (tenant_id = current_setting('taroai.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('taroai.tenant_id', true));
-- taroai:postgresql-only-end
