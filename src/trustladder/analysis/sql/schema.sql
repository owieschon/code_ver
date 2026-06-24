-- Relational schema the run-records and ledger are loaded into (in-memory SQLite).
-- One row per run, plus a child table per seeded defect and per policy violation,
-- and a small severity-rank lookup so "SEV2+" can be expressed as a join.

CREATE TABLE runs (
    run_id     TEXT PRIMARY KEY,
    task_id    TEXT,
    arm        TEXT,                  -- L0 / L1 / SHAM / L3
    stratum    TEXT,                  -- TYPICAL / NOVEL
    batch      TEXT,                  -- 'primary' is the experimental battery
    completion INTEGER                -- M0: did the run complete the task?
);

CREATE TABLE escapes (
    run_id    TEXT,
    defect_id TEXT,
    sev       TEXT,                   -- SEV1..SEV4
    escaped   INTEGER                 -- did this seeded defect survive to ship?
);

CREATE TABLE violations (
    run_id           TEXT,
    class            TEXT,            -- SCOPE / VERB / GUARD-SURFACE / PROVENANCE
    survived_to_done INTEGER
);

CREATE TABLE sev_rank (
    sev  TEXT PRIMARY KEY,
    rank INTEGER
);
