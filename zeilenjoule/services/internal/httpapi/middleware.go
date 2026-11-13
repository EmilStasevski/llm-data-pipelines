package httpapi

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"log/slog"
	"net/http"
	"runtime/debug"
	"strconv"
	"sync"
	"time"

	"golang.org/x/time/rate"

	"zeilenjoule/services/internal/api"
	"zeilenjoule/services/internal/metrics"
)

// Options of the middleware chain.
type Options struct {
	APIKeys   []string
	RateLimit float64 // per key, requests/s
	RateBurst int
	JSONLimit int64         // body bytes for JSON endpoints
	Timeout   time.Duration // deadline for ask/search/extract
	Log       *slog.Logger
}

var public = map[string]bool{"/healthz": true, "/readyz": true, "/v1/openapi.json": true}

// statusWriter records the status code and keeps http.Flusher working (SSE).
type statusWriter struct {
	http.ResponseWriter
	code int
}

func (w *statusWriter) WriteHeader(c int) {
	if w.code == 0 {
		w.code = c
	}
	w.ResponseWriter.WriteHeader(c)
}

func (w *statusWriter) Write(b []byte) (int, error) {
	if w.code == 0 {
		w.code = http.StatusOK
	}
	return w.ResponseWriter.Write(b)
}

func (w *statusWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

func (w *statusWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

// op names the endpoint for metrics without unbounded label values (job ids).
func op(r *http.Request) string {
	switch p := r.URL.Path; {
	case len(p) > len("/v1/jobs/") && p[:len("/v1/jobs/")] == "/v1/jobs/":
		return "get_job"
	case p == "/v1/ask", p == "/v1/search", p == "/v1/extract", p == "/v1/ingest", p == "/healthz", p == "/readyz",
		p == "/v1/openapi.json":
		return p
	default:
		return "other"
	}
}

func keyHash(k string) [32]byte { return sha256.Sum256([]byte(k)) }

// Chain wraps h with recovery, access log + metrics, API key, rate limit, body limit and deadline.
func Chain(h http.Handler, o Options) http.Handler {
	keys := make([][32]byte, len(o.APIKeys))
	for i, k := range o.APIKeys {
		keys[i] = keyHash(k)
	}
	var mu sync.Mutex
	limiters := map[[32]byte]*rate.Limiter{}
	limiter := func(k [32]byte) *rate.Limiter {
		mu.Lock()
		defer mu.Unlock()
		l, ok := limiters[k]
		if !ok {
			l = rate.NewLimiter(rate.Limit(o.RateLimit), o.RateBurst)
			limiters[k] = l
		}
		return l
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t0 := time.Now()
		sw := &statusWriter{ResponseWriter: w}
		defer func() {
			if v := recover(); v != nil {
				o.Log.Error("panic", "path", r.URL.Path, "panic", v, "stack", string(debug.Stack()))
				if sw.code == 0 {
					writeProblem(sw, problem(http.StatusInternalServerError, "internal error", ""))
				}
			}
			code := sw.code
			if code == 0 {
				code = http.StatusOK
			}
			d := time.Since(t0)
			metrics.HTTPDuration.WithLabelValues(op(r), strconv.Itoa(code)).Observe(d.Seconds())
			if !public[r.URL.Path] {
				o.Log.Info("http", "method", r.Method, "path", r.URL.Path, "status", code, "ms", d.Milliseconds())
			}
		}()
		if !public[r.URL.Path] {
			given := keyHash(r.Header.Get("X-API-Key"))
			ok := false
			for _, k := range keys {
				ok = ok || subtle.ConstantTimeCompare(given[:], k[:]) == 1
			}
			if !ok {
				writeProblem(sw, problem(http.StatusUnauthorized, "missing or invalid X-API-Key", ""))
				return
			}
			if !limiter(given).Allow() {
				metrics.RateLimited.Inc()
				sw.Header().Set("Retry-After", "1")
				writeProblem(sw, problem(http.StatusTooManyRequests, "rate limit exceeded", ""))
				return
			}
		}
		ctx := r.Context()
		if r.URL.Path != "/v1/ingest" { // ingest bodies are bounded by MaxUpload in the handler
			r.Body = http.MaxBytesReader(sw, r.Body, o.JSONLimit)
			var cancel context.CancelFunc
			ctx, cancel = context.WithTimeout(ctx, o.Timeout)
			defer cancel()
		}
		h.ServeHTTP(sw, r.WithContext(ctx))
	})
}

// Handler builds the full gateway handler: generated routes + spec + middleware.
func Handler(s *Server, o Options) http.Handler {
	mux := http.NewServeMux()
	strict := api.NewStrictHandlerWithOptions(s, []api.StrictMiddlewareFunc{AcceptMiddleware},
		api.StrictHTTPServerOptions{
			RequestErrorHandlerFunc:  ProblemHandler(o.Log, http.StatusBadRequest),
			ResponseErrorHandlerFunc: ProblemHandler(o.Log, http.StatusInternalServerError),
		})
	h := api.HandlerWithOptions(strict, api.StdHTTPServerOptions{BaseRouter: mux,
		ErrorHandlerFunc: ProblemHandler(o.Log, http.StatusBadRequest)})
	mux.HandleFunc("GET /v1/openapi.json", func(w http.ResponseWriter, _ *http.Request) {
		spec, err := api.GetSwagger()
		if err != nil {
			writeProblem(w, problem(http.StatusInternalServerError, "spec unavailable", ""))
			return
		}
		b, _ := spec.MarshalJSON()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(b)
	})
	return Chain(h, o)
}
