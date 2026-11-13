// Command zj-worker runs the ingest jobs from the River queue in PostgreSQL.
//
//	zj-worker migrate   apply River + schema migrations and exit (compose one-shot / k8s Job)
//	zj-worker           run the job worker (one ingest at a time: the GPU is the bottleneck)
package main

import (
	"fmt"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/riverqueue/river"
	"github.com/riverqueue/river/riverdriver/riverpgxv5"

	"zeilenjoule/services/internal/config"
	"zeilenjoule/services/internal/db"
	"zeilenjoule/services/internal/embed"
	"zeilenjoule/services/internal/ingest"
	"zeilenjoule/services/internal/llm"
	"zeilenjoule/services/internal/prompts"
	"zeilenjoule/services/internal/runtime"
)

// version is set at build time (-ldflags -X main.version=…).
var version = "dev"

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "zj-worker:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	log := runtime.Logger("zj-worker").With("version", version)
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	ctx, stop := runtime.SignalContext()
	defer stop()
	pool, err := db.Open(ctx, cfg.DatabaseURL, 8)
	if err != nil {
		return err
	}
	defer pool.Close()
	if len(args) == 1 && args[0] == "migrate" {
		applied, err := db.Migrate(ctx, pool)
		log.Info("migrations", "applied", applied, "err", err)
		return err
	}
	if len(args) > 0 {
		return fmt.Errorf("usage: zj-worker [migrate]")
	}
	p, err := prompts.Load()
	if err != nil {
		return err
	}
	// heal a bulk load that died between dropping and rebuilding the search indexes (no-op normally)
	if err := db.EnsureSearchIndexes(ctx, pool); err != nil {
		return err
	}
	workers := river.NewWorkers()
	river.AddWorker(workers, &ingest.Worker{
		Pool: pool, P: p, Log: log,
		Emb:       embed.New(cfg.TEIURL, cfg.EmbedInFlight, cfg.EmbedBatch),
		LLM:       llm.New(cfg.LLMURL, cfg.LLMModel, cfg.LLMInFlight),
		ParserBin: cfg.ParserBin, Lexicon: cfg.Lexicon, ParserThreads: cfg.ParserThreads,
	})
	rc, err := river.NewClient[pgx.Tx](riverpgxv5.New(pool), &river.Config{
		Queues:  map[string]river.QueueConfig{ingest.Queue: {MaxWorkers: 1}},
		Workers: workers,
		Logger:  log,
	})
	if err != nil {
		return err
	}
	if err := rc.Start(ctx); err != nil {
		return err
	}
	ms := runtime.MetricsServer(cfg.MetricsListen)
	go func() { _ = runtime.Serve(ctx, ms, 5*time.Second, log) }()
	<-ctx.Done()
	log.Info("stopping: waiting for the running job (up to 60 s), then canceling it")
	sctx, cancel := contextWithTimeout(60 * time.Second)
	defer cancel()
	if err := rc.Stop(sctx); err != nil {
		cctx, ccancel := contextWithTimeout(10 * time.Second)
		defer ccancel()
		return rc.StopAndCancel(cctx)
	}
	return nil
}
