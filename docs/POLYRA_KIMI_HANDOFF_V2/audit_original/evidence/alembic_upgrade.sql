BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE users (
    id UUID NOT NULL, 
    telegram_user_id BIGINT NOT NULL, 
    username VARCHAR(64), 
    first_name VARCHAR(128) NOT NULL, 
    last_name VARCHAR(128), 
    language_code VARCHAR(16), 
    status VARCHAR(16) NOT NULL, 
    is_owner BOOLEAN NOT NULL, 
    first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_users PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_telegram_user_id ON users (telegram_user_id);

CREATE TABLE access_grants (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE, 
    requests_per_day INTEGER, 
    token_limit BIGINT, 
    max_concurrent_generations INTEGER NOT NULL, 
    can_use_web_search BOOLEAN NOT NULL, 
    can_use_memory BOOLEAN NOT NULL, 
    note TEXT, 
    created_by BIGINT, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_access_grants PRIMARY KEY (id), 
    CONSTRAINT fk_access_grants_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT uq_access_grants_user_id UNIQUE (user_id)
);

CREATE TABLE user_model_permissions (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    model_id VARCHAR(64) NOT NULL, 
    allowed BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_user_model_permissions PRIMARY KEY (id), 
    CONSTRAINT fk_user_model_permissions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT uq_user_model_permissions_user_id UNIQUE (user_id, model_id)
);

CREATE INDEX ix_user_model_permissions_user_id ON user_model_permissions (user_id);

CREATE TABLE user_settings (
    user_id UUID NOT NULL, 
    default_model_id VARCHAR(64), 
    default_thinking VARCHAR(16), 
    web_mode VARCHAR(8) NOT NULL, 
    memory_enabled BOOLEAN NOT NULL, 
    locale VARCHAR(8), 
    extra JSONB DEFAULT '{}'::jsonb NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_user_settings PRIMARY KEY (user_id), 
    CONSTRAINT fk_user_settings_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

-- Running upgrade 0001 -> 0002

CREATE TABLE chats (
    id UUID NOT NULL, 
    owner_user_id UUID NOT NULL, 
    title VARCHAR(256), 
    model_id VARCHAR(64), 
    thinking_setting VARCHAR(16), 
    web_mode VARCHAR(8), 
    memory_enabled BOOLEAN, 
    system_prompt_override TEXT, 
    archived_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_chats PRIMARY KEY (id), 
    CONSTRAINT fk_chats_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_chats_owner_user_id ON chats (owner_user_id);

CREATE TABLE messages (
    id UUID NOT NULL, 
    chat_id UUID NOT NULL, 
    role VARCHAR(16) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    provider VARCHAR(32), 
    model_id VARCHAR(64), 
    generation_run_id UUID, 
    input_tokens INTEGER, 
    output_tokens INTEGER, 
    reasoning_tokens INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_messages PRIMARY KEY (id), 
    CONSTRAINT fk_messages_chat_id_chats FOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE
);

CREATE INDEX ix_messages_chat_id ON messages (chat_id);

CREATE TABLE message_parts (
    id UUID NOT NULL, 
    message_id UUID NOT NULL, 
    type VARCHAR(16) NOT NULL, 
    position INTEGER NOT NULL, 
    text TEXT, 
    telegram_file_id VARCHAR(256), 
    mime_type VARCHAR(64), 
    metadata_json JSONB DEFAULT '{}'::jsonb NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_message_parts PRIMARY KEY (id), 
    CONSTRAINT fk_message_parts_message_id_messages FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE
);

CREATE INDEX ix_message_parts_message_id ON message_parts (message_id);

CREATE TABLE generation_runs (
    id UUID NOT NULL, 
    chat_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    model_id VARCHAR(64) NOT NULL, 
    thinking_setting VARCHAR(16), 
    status VARCHAR(16) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    first_token_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    input_tokens INTEGER, 
    output_tokens INTEGER, 
    reasoning_tokens INTEGER, 
    tool_calls_count INTEGER NOT NULL, 
    gemini_project_id UUID, 
    error_category VARCHAR(32), 
    error_code VARCHAR(64), 
    draft_id BIGINT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_generation_runs PRIMARY KEY (id), 
    CONSTRAINT fk_generation_runs_chat_id_chats FOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE, 
    CONSTRAINT fk_generation_runs_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_generation_runs_chat_id ON generation_runs (chat_id);

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';

-- Running upgrade 0002 -> 0003

CREATE TABLE gemini_projects (
    id UUID NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    encrypted_api_key TEXT NOT NULL, 
    key_hint VARCHAR(16) NOT NULL, 
    rotation_order INTEGER NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    health_status VARCHAR(16) NOT NULL, 
    cooldown_until TIMESTAMP WITH TIME ZONE, 
    last_success_at TIMESTAMP WITH TIME ZONE, 
    last_error_at TIMESTAMP WITH TIME ZONE, 
    last_error_code VARCHAR(64), 
    last_error_message VARCHAR(256), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_gemini_projects PRIMARY KEY (id), 
    CONSTRAINT uq_gemini_projects_name UNIQUE (name)
);

CREATE TABLE quota_policies (
    id UUID NOT NULL, 
    model_id VARCHAR(64) NOT NULL, 
    rpm INTEGER, 
    tpm INTEGER, 
    rpd INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_quota_policies PRIMARY KEY (id), 
    CONSTRAINT uq_quota_policies_model_id UNIQUE (model_id)
);

CREATE TABLE quota_minute_usage (
    id UUID NOT NULL, 
    project_id UUID NOT NULL, 
    model_id VARCHAR(64) NOT NULL, 
    minute_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
    requests_count INTEGER NOT NULL, 
    tokens_in BIGINT NOT NULL, 
    CONSTRAINT pk_quota_minute_usage PRIMARY KEY (id), 
    CONSTRAINT fk_quota_minute_usage_project_id_gemini_projects FOREIGN KEY(project_id) REFERENCES gemini_projects (id) ON DELETE CASCADE, 
    CONSTRAINT uq_quota_minute_usage_project_id UNIQUE (project_id, model_id, minute_ts)
);

CREATE INDEX ix_quota_minute_usage_minute_ts ON quota_minute_usage (minute_ts);

CREATE TABLE quota_daily_usage (
    id UUID NOT NULL, 
    project_id UUID NOT NULL, 
    model_id VARCHAR(64) NOT NULL, 
    day DATE NOT NULL, 
    requests_count INTEGER NOT NULL, 
    tokens_in BIGINT NOT NULL, 
    CONSTRAINT pk_quota_daily_usage PRIMARY KEY (id), 
    CONSTRAINT fk_quota_daily_usage_project_id_gemini_projects FOREIGN KEY(project_id) REFERENCES gemini_projects (id) ON DELETE CASCADE, 
    CONSTRAINT uq_quota_daily_usage_project_id UNIQUE (project_id, model_id, day)
);

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';

-- Running upgrade 0003 -> 0004

CREATE TABLE provider_credentials (
    id UUID NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    encrypted_api_key TEXT NOT NULL, 
    key_hint VARCHAR(16) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_provider_credentials PRIMARY KEY (id), 
    CONSTRAINT uq_provider_credentials_provider UNIQUE (provider)
);

INSERT INTO quota_policies (id, model_id, rpm, tpm, rpd, created_at, updated_at) VALUES ('030e2068-93e7-4b5c-9db6-814ba4d39738', 'gemini-3.8-flash', 4, 249999, 19, now(), now()), ('4e3ef36d-f511-47fe-a7f0-02495e4a4abe', 'gemini-3.7-flash', 4, 249999, 19, now(), now()), ('f78833dc-e9a2-416a-896b-f3d440969189', 'gemini-3.6-flash', 4, 249999, 19, now(), now()) ON CONFLICT ON CONSTRAINT uq_quota_policies_model_id DO NOTHING;

UPDATE alembic_version SET version_num='0004' WHERE alembic_version.version_num = '0003';

-- Running upgrade 0004 -> 0005

CREATE TABLE chat_summaries (
    id UUID NOT NULL, 
    chat_id UUID NOT NULL, 
    summary JSONB DEFAULT '{}'::jsonb NOT NULL, 
    covered_until_message_id UUID, 
    covered_messages_count INTEGER NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_chat_summaries PRIMARY KEY (id), 
    CONSTRAINT fk_chat_summaries_chat_id_chats FOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE, 
    CONSTRAINT uq_chat_summaries_chat_id UNIQUE (chat_id)
);

UPDATE alembic_version SET version_num='0005' WHERE alembic_version.version_num = '0004';

-- Running upgrade 0005 -> 0006

CREATE TABLE memories (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    text TEXT NOT NULL, 
    normalized_text TEXT NOT NULL, 
    category VARCHAR(32) NOT NULL, 
    importance INTEGER NOT NULL, 
    last_used_at TIMESTAMP WITH TIME ZONE, 
    source_chat_id UUID, 
    source_message_id UUID, 
    embedding JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_memories PRIMARY KEY (id), 
    CONSTRAINT fk_memories_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_memories_user_id ON memories (user_id);

CREATE INDEX ix_memories_normalized_text ON memories (normalized_text);

UPDATE alembic_version SET version_num='0006' WHERE alembic_version.version_num = '0005';

-- Running upgrade 0006 -> 0007

CREATE TABLE search_backend_configs (
    id UUID NOT NULL, 
    backend_id VARCHAR(32) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    priority INTEGER NOT NULL, 
    encrypted_api_key TEXT, 
    key_hint VARCHAR(16), 
    health_status VARCHAR(16) NOT NULL, 
    last_error VARCHAR(256), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_search_backend_configs PRIMARY KEY (id), 
    CONSTRAINT uq_search_backend_configs_backend_id UNIQUE (backend_id)
);

CREATE TABLE tool_calls (
    id UUID NOT NULL, 
    generation_run_id UUID NOT NULL, 
    chat_id UUID NOT NULL, 
    tool_name VARCHAR(64) NOT NULL, 
    arguments_json TEXT DEFAULT '' NOT NULL, 
    status VARCHAR(16) DEFAULT 'ok' NOT NULL, 
    result_preview VARCHAR(500) DEFAULT '' NOT NULL, 
    duration_ms INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_tool_calls PRIMARY KEY (id), 
    CONSTRAINT fk_tool_calls_generation_run_id_generation_runs FOREIGN KEY(generation_run_id) REFERENCES generation_runs (id) ON DELETE CASCADE, 
    CONSTRAINT fk_tool_calls_chat_id_chats FOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE
);

CREATE INDEX ix_tool_calls_generation_run_id ON tool_calls (generation_run_id);

UPDATE alembic_version SET version_num='0007' WHERE alembic_version.version_num = '0006';

-- Running upgrade 0007 -> 0008

CREATE TABLE system_settings (
    key VARCHAR(64) NOT NULL, 
    value JSONB NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_system_settings PRIMARY KEY (key)
);

CREATE TABLE audit_log (
    id UUID NOT NULL, 
    actor_telegram_id BIGINT NOT NULL, 
    action VARCHAR(48) NOT NULL, 
    target_type VARCHAR(32), 
    target_id VARCHAR(64), 
    metadata_json JSONB DEFAULT '{}'::jsonb NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_audit_log PRIMARY KEY (id)
);

CREATE INDEX ix_audit_log_actor_telegram_id ON audit_log (actor_telegram_id);

UPDATE alembic_version SET version_num='0008' WHERE alembic_version.version_num = '0007';

COMMIT;

