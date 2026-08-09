-- The idempotency ledger and results store (plan §3). One DuckDB file,
-- serving as both the working ledger and the published raw-results
-- artifact. Every table with a natural key is upsert-safe, so replaying a
-- WAL file twice — or replaying WALs from three separate Azure
-- apply/destroy sessions — is a no-op past the first ingest.

CREATE TABLE IF NOT EXISTS work_items (
    work_item_id TEXT PRIMARY KEY,   -- deterministic hash of (kind, engine, sf, scenario, query_id, rep)
    kind TEXT NOT NULL,              -- 'infra' | 'load' | 'query' | 'cost_reconcile'
    engine TEXT NOT NULL,            -- 'duckhaven' | 'snowflake' | 'databricks'
    scale_factor INTEGER NOT NULL,
    scenario TEXT,                   -- 'sequential' | 'cold_start' | 'concurrent' | 'write' | 'dml'
    query_id TEXT,                   -- 'q01'..'q22', a table name, or NULL for infra/reconcile
    rep INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed | skipped
    attempt INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    run_id TEXT,
    methodology_hash TEXT
);

-- One result row per work item: a query_result IS the outcome of running
-- that work item, so the pairing is 1:1 and UNIQUE(work_item_id) is what
-- makes re-ingesting the same WAL line an upsert instead of a duplicate.
CREATE TABLE IF NOT EXISTS query_results (
    work_item_id TEXT NOT NULL REFERENCES work_items (work_item_id),
    engine_query_id TEXT,        -- native id: DuckHaven Query.id / Snowflake QUERY_ID / DBSQL statement id
    server_duration_ms DOUBLE,   -- DuckHaven: Query.duration_ms; others: engine-reported exec time
    queued_ms DOUBLE,            -- started_at -> running_at (DuckHaven-only concept; NULL elsewhere)
    execution_ms DOUBLE,         -- running_at -> finished_at
    client_wall_ms DOUBLE,       -- full round-trip as the harness measured it
    row_count BIGINT,
    bytes_scanned BIGINT,
    peak_memory_bytes BIGINT,    -- DuckHaven: GET /queries/{id}/profile; others: best engine equivalent
    spill_bytes BIGINT,
    compute_ref TEXT,            -- agent_id / warehouse name/id
    error TEXT,
    raw_response_json JSON,
    UNIQUE (work_item_id)
);

CREATE TABLE IF NOT EXISTS load_results (
    work_item_id TEXT NOT NULL REFERENCES work_items (work_item_id),
    table_name TEXT,
    rows_loaded BIGINT,
    bytes BIGINT,
    load_duration_ms DOUBLE,
    method TEXT,                 -- 'staging_files' | 'copy_into' | 'ctas'
    UNIQUE (work_item_id)
);

-- Provisioning ledger: DuckHaven agents, warehouse resize/suspend. Genuinely
-- append-only (several events can share a resource_ref over its lifetime),
-- so the natural key is the specific event, not the resource.
CREATE TABLE IF NOT EXISTS infra_events (
    engine TEXT NOT NULL,
    scale_factor INTEGER,
    resource_ref TEXT NOT NULL,
    action TEXT NOT NULL,        -- 'provision' | 'terminate' | 'resume' | 'suspend'
    requested_size TEXT,
    hourly_rate DOUBLE,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    wall_hours DOUBLE,
    UNIQUE (resource_ref, action, started_at)
);

-- Authoritative billing, upserted by cost/reconcile.py — designed to be
-- re-run hours or days later as Snowflake/Databricks billing latency
-- catches up, without duplicating rows for the same window.
CREATE TABLE IF NOT EXISTS cost_facts (
    engine TEXT NOT NULL,
    scale_factor INTEGER NOT NULL,
    scenario TEXT NOT NULL,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP,
    cost_amount DOUBLE,
    currency TEXT,
    source TEXT NOT NULL,        -- 'ACCOUNT_USAGE' | 'system.billing.usage' | 'duckhaven_hourly'
    pulled_at TIMESTAMP,
    raw_row JSON,
    UNIQUE (engine, scale_factor, scenario, window_start, source)
);

-- One row per apply->destroy cycle of the Azure environment.
CREATE TABLE IF NOT EXISTS terraform_sessions (
    session_id TEXT PRIMARY KEY,
    applied_at TIMESTAMP,
    destroyed_at TIMESTAMP,
    resource_group TEXT,
    image_tag TEXT,
    purpose TEXT
);

-- Append-only proof of pre-registration: METHODOLOGY.md is frozen and
-- hashed before any real-money run, and no edits to query text, scenario
-- definitions, or rep counts are allowed after — only dated errata.
CREATE TABLE IF NOT EXISTS methodology_registrations (
    methodology_hash TEXT PRIMARY KEY,
    registered_at TIMESTAMP,
    doc_path TEXT
);
