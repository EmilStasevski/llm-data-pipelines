// Package runtime holds the process plumbing shared by zj-gateway and zj-worker: logger, signal context,
// metrics listener and graceful HTTP shutdown.
package runtime

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Logger writes JSON logs to stderr (collected by docker/k8s); ZJ_LOG_LEVEL=debug|info|warn|error.
func Logger(service string) *slog.Logger {
	var lvl slog.Level
	_ = lvl.UnmarshalText([]byte(os.Getenv("ZJ_LOG_LEVEL")))
	return slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: lvl})).With("service", service)
}

// SignalContext is canceled on SIGINT/SIGTERM.
func SignalContext() (context.Context, context.CancelFunc) {
	return signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
}

// Serve runs srv until ctx ends, then shuts it down gracefully (in-flight requests get grace to finish).
func Serve(ctx context.Context, srv *http.Server, grace time.Duration, log *slog.Logger) error {
	srv.BaseContext = func(net.Listener) context.Context { return context.WithoutCancel(ctx) }
	errc := make(chan error, 1)
	go func() { errc <- srv.ListenAndServe() }()
	log.Info("listening", "addr", srv.Addr)
	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
	}
	sctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), grace)
	defer cancel()
	err := srv.Shutdown(sctx)
	if e := <-errc; !errors.Is(e, http.ErrServerClosed) && err == nil {
		err = e
	}
	return err
}

// MetricsServer exposes /metrics (Prometheus) on its own address, separate from the API port.
func MetricsServer(addr string) *http.Server {
	mux := http.NewServeMux()
	mux.Handle("GET /metrics", promhttp.Handler())
	return &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
}
