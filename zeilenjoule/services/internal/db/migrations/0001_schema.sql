-- Production schema of e03_postgres_only (ADR 0021): one table, halfvec HNSW + German full text, SQL view for the
-- router, a NOLOGIN role that may only read the view. Index parameters as in Track C (m 16, ef_construction 200).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE docs (
    meldung_id          text PRIMARY KEY,
    werk                text NOT NULL,
    regierungsbezirk    text,
    anlage              text NOT NULL,
    equipment_nr        text,
    datum               date,
    prioritaet          smallint,
    stillstand_min      integer,
    kosten_eur          double precision,
    komponente          text,
    schadensart         text,
    schadensart_quelle  text CHECK (schadensart_quelle IN ('llm', 'klassifikator')),
    sicherheitsrelevant boolean NOT NULL,
    meldungstext        text NOT NULL,
    doc_text            text NOT NULL,
    emb                 halfvec(1024) NOT NULL,
    tsv                 tsvector GENERATED ALWAYS AS (to_tsvector('german', doc_text)) STORED,
    job_id              bigint,
    ingested_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX docs_hnsw ON docs USING hnsw (emb halfvec_ip_ops) WITH (m = 16, ef_construction = 200);
CREATE INDEX docs_tsv ON docs USING gin (tsv);
CREATE INDEX docs_werk ON docs (werk);
CREATE INDEX docs_regierungsbezirk ON docs (regierungsbezirk);
CREATE INDEX docs_datum ON docs (datum);
CREATE INDEX docs_equipment_nr ON docs (equipment_nr);
CREATE INDEX docs_komponente ON docs (komponente);
CREATE INDEX docs_schadensart ON docs (schadensart);

-- what the router may query (same columns and types as the benchmark view; kosten_eur numeric so round(x, 2) works)
CREATE VIEW meldungen AS
SELECT meldung_id, werk, regierungsbezirk, anlage, equipment_nr, datum, prioritaet::integer AS prioritaet,
       stillstand_min, kosten_eur::numeric AS kosten_eur, komponente, schadensart, sicherheitsrelevant, meldungstext
FROM docs;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zj_reader') THEN
        CREATE ROLE zj_reader NOLOGIN;
    END IF;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM zj_reader;
GRANT USAGE ON SCHEMA public TO zj_reader;
GRANT SELECT ON meldungen TO zj_reader;

CREATE TABLE ingest_jobs (
    id          bigserial PRIMARY KEY,
    state       text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'done', 'failed')),
    path        text NOT NULL,
    bytes       bigint NOT NULL,
    rows        bigint NOT NULL DEFAULT 0,
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

-- schadensart distillation (ADR 0020): softmax weights trained on LLM labels over bge-m3 vectors
CREATE TABLE classifier (
    id          bigserial PRIMARY KEY,
    classes     text[] NOT NULL,
    dim         integer NOT NULL,
    weights     real[] NOT NULL, -- (dim + 1) × classes, row-major, last row = bias
    train_rows  integer NOT NULL,
    holdout_acc double precision,
    created_at  timestamptz NOT NULL DEFAULT now()
);
