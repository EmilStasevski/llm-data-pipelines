package db

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// SearchIndexes are the two expensive indexes of docs, exactly as migration 0001 creates them (the integration
// test compares pg_indexes.indexdef before and after a bulk load). A first import into an empty table drops them
// and builds them once at the end with parallel maintenance workers — the e03 procedure (COPY, then CREATE
// INDEX) instead of growing an HNSW graph row by row (ADR 0024 addendum).
var SearchIndexes = []struct{ Name, DDL string }{
	{"docs_hnsw", "CREATE INDEX IF NOT EXISTS docs_hnsw ON docs USING hnsw (emb halfvec_ip_ops) WITH (m = 16, ef_construction = 200)"},
	{"docs_tsv", "CREATE INDEX IF NOT EXISTS docs_tsv ON docs USING gin (tsv)"},
}

// DropSearchIndexes removes the search indexes (bulk load into an empty table).
func DropSearchIndexes(ctx context.Context, pool *pgxpool.Pool) error {
	for _, ix := range SearchIndexes {
		if _, err := pool.Exec(ctx, "DROP INDEX IF EXISTS "+ix.Name); err != nil {
			return fmt.Errorf("drop %s: %w", ix.Name, err)
		}
	}
	return nil
}

// EnsureSearchIndexes creates missing search indexes; a no-op when they exist. Called after every bulk load
// (also when it failed) and at worker start, so a crash between drop and rebuild heals itself.
func EnsureSearchIndexes(ctx context.Context, pool *pgxpool.Pool) error {
	conn, err := pool.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()
	// server defaults (compose/Helm: maintenance_work_mem 2GB, 4 parallel maintenance workers) apply; no SET here
	for _, ix := range SearchIndexes {
		if _, err := conn.Exec(ctx, ix.DDL); err != nil {
			return fmt.Errorf("create %s: %w", ix.Name, err)
		}
	}
	return nil
}
