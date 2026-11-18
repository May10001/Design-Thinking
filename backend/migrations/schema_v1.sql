PRAGMA foreign_keys = ON;

CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL,
    display_name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT,
    course_name TEXT,
    external_ref TEXT,
    due_kind TEXT NOT NULL CHECK (due_kind IN ('NONE', 'DATE', 'DATETIME')),
    due_date TEXT,
    due_at TEXT,
    timezone TEXT NOT NULL,
    requirements TEXT,
    title_normalized TEXT NOT NULL,
    course_normalized TEXT,
    external_ref_normalized TEXT,
    content_hash TEXT NOT NULL,
    identity_key TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, user_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    CHECK (
        (due_kind = 'NONE' AND due_date IS NULL AND due_at IS NULL)
        OR (due_kind = 'DATE' AND due_date IS NOT NULL AND due_at IS NULL)
        OR (due_kind = 'DATETIME' AND due_date IS NOT NULL AND due_at IS NOT NULL)
    )
);

CREATE TABLE imports (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL,
    input_type TEXT NOT NULL CHECK (input_type IN ('TEXT', 'IMAGE')),
    sanitized_text TEXT NOT NULL,
    source_time TEXT,
    reference_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'READY'
        CHECK (status IN ('READY', 'ANALYZING', 'REVIEW_READY', 'COMPLETED', 'FAILED')),
    warnings_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    last_error_code TEXT,
    last_error_message TEXT,
    analyzed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, user_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE review_batches (
    id TEXT PRIMARY KEY NOT NULL,
    import_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    request_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, idempotency_key),
    FOREIGN KEY (import_id, user_id) REFERENCES imports(id, user_id)
);

CREATE TABLE draft_items (
    id TEXT PRIMARY KEY NOT NULL,
    import_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    extracted_json TEXT NOT NULL,
    proposed_task_json TEXT NOT NULL,
    suggested_action TEXT NOT NULL
        CHECK (suggested_action IN ('CREATE', 'DUPLICATE', 'UPDATE', 'REVIEW')),
    matched_task_id TEXT,
    matched_task_version INTEGER CHECK (matched_task_version >= 1),
    match_details_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'CONFIRMED', 'SKIPPED')),
    final_action TEXT CHECK (final_action IN ('CREATE', 'UPDATE', 'KEEP', 'SKIP')),
    final_task_id TEXT,
    finalized_task_json TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (import_id, ordinal),
    FOREIGN KEY (import_id, user_id) REFERENCES imports(id, user_id),
    FOREIGN KEY (matched_task_id, user_id) REFERENCES tasks(id, user_id),
    FOREIGN KEY (final_task_id, user_id) REFERENCES tasks(id, user_id),
    CHECK (
        (matched_task_id IS NULL AND matched_task_version IS NULL)
        OR (matched_task_id IS NOT NULL AND matched_task_version IS NOT NULL)
    ),
    CHECK (
        (status = 'PENDING' AND final_action IS NULL AND final_task_id IS NULL
            AND finalized_task_json IS NULL AND confirmed_at IS NULL)
        OR (status = 'CONFIRMED' AND final_action IS NOT NULL
            AND final_action IN ('CREATE', 'UPDATE', 'KEEP')
            AND final_task_id IS NOT NULL AND finalized_task_json IS NOT NULL
            AND confirmed_at IS NOT NULL)
        OR (status = 'SKIPPED' AND final_action IS NOT NULL AND final_action = 'SKIP'
            AND final_task_id IS NULL AND finalized_task_json IS NULL
            AND confirmed_at IS NOT NULL)
    )
);

CREATE INDEX idx_tasks_content ON tasks(user_id, content_hash);
CREATE INDEX idx_tasks_identity ON tasks(user_id, identity_key);
CREATE INDEX idx_tasks_course_title ON tasks(user_id, course_normalized, title_normalized);
CREATE INDEX idx_tasks_due ON tasks(user_id, due_date);
CREATE INDEX idx_imports_user_time ON imports(user_id, created_at);
CREATE INDEX idx_drafts_import_status ON draft_items(import_id, status);
