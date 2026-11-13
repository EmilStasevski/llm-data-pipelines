// Command zj-gateway serves the ZeilenJoule REST API (api/openapi.yaml): ask, search, extract, ingest, jobs.
package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/riverqueue/river"
	"github.com/riverqueue/river/riverdriver/riverpgxv5"
	"golang.org/x/sync/errgroup"

	"zeilenjoule/services/internal/config"
	"zeilenjoule/services/internal/db"
	"zeilenjoule/services/internal/embed"
	"zeilenjoule/services/internal/httpapi"
	"zeilenjoule/services/internal/llm"
	"zeilenjoule/services/internal/prompts"
	"zeilenjoule/services/internal/rag"
	"zeilenjoule/services/internal/runtime"
)

// version is set at build time (-ldflags -X main.version=…).
var version = "dev"

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "zj-gateway:", err)
		os.Exit(1)
	}
}

func run() error {
	log := runtime.Logger("zj-gateway").With("version", version)
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	if cfg.ReaderURL == "" {
		return fmt.Errorf("config: ZJ_READER_URL is required (SELECT-only login for router SQL)")
	}
	if len(cfg.APIKeys) == 0 {
		return fmt.Errorf("config: ZJ_API_KEYS is required")
	}
	p, err := prompts.Load()
	if err != nil {
		return err
	}
	ctx, stop := runtime.SignalContext()
	defer stop()
	pool, err := db.Open(ctx, cfg.DatabaseURL, 16)
	if err != nil {
		return err
	}
	defer pool.Close()
	reader, err := db.Open(ctx, cfg.ReaderURL, 8)
	if err != nil {
		return err
	}
	defer reader.Close()
	rc, err := river.NewClient[pgx.Tx](riverpgxv5.New(pool), &river.Config{}) // insert-only client
	if err != nil {
		return err
	}
	if err := os.MkdirAll(cfg.UploadDir, 0o750); err != nil {
		return err
	}
	lc := llm.New(cfg.LLMURL, cfg.LLMModel, cfg.LLMInFlight)
	ec := embed.New(cfg.TEIURL, cfg.EmbedInFlight, cfg.EmbedBatch)
	srv := &httpapi.Server{
		Pool: pool, River: rc, UploadDir: cfg.UploadDir, MaxUpload: cfg.MaxUpload, Log: log,
		Engine:     &rag.Engine{Owner: pool, Reader: reader, LLM: lc, Emb: ec, P: p, SQLTimeout: cfg.SQLTimeout},
		Upstreams:  map[string]httpapi.Pinger{"tei": ec, "llm": lc},
		Prometheus: cfg.PrometheusURL,
	}
	h := httpapi.Handler(srv, httpapi.Options{APIKeys: cfg.APIKeys, RateLimit: cfg.RateLimit, RateBurst: cfg.RateBurst,
		JSONLimit: 4 << 20, Timeout: cfg.RequestTO, Log: log})
	api := &http.Server{Addr: cfg.Listen, Handler: h, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 120 * time.Second}
	g, gctx := errgroup.WithContext(ctx)
	g.Go(func() error { return runtime.Serve(gctx, api, 30*time.Second, log) })
	g.Go(func() error { return runtime.Serve(gctx, runtime.MetricsServer(cfg.MetricsListen), 5*time.Second, log) })
	err = g.Wait()
	if ctx.Err() != nil && (err == nil || errors.Is(err, context.Canceled)) {
		log.Info("stopped")
		return nil
	}
	return err
}
