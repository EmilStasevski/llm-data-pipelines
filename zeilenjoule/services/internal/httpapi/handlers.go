// Package httpapi implements the generated StrictServerInterface (api/openapi.yaml) and the HTTP middleware
// of zj-gateway: API key, per-key rate limit, body limits, request deadline, metrics, structured logs.
package httpapi

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	openapi_types "github.com/oapi-codegen/runtime/types"
	"github.com/riverqueue/river"
	"golang.org/x/sync/errgroup"

	"zeilenjoule/services/internal/api"
	"zeilenjoule/services/internal/ingest"
	"zeilenjoule/services/internal/rag"
)

// Pinger is anything /readyz checks.
type Pinger interface {
	Ready(ctx context.Context) error
}

// Server holds the dependencies of the handlers.
type Server struct {
	Pool      *pgxpool.Pool
	Engine    *rag.Engine
	River     *river.Client[pgx.Tx]
	Upstreams map[string]Pinger // "tei", "llm"
	UploadDir string
	MaxUpload int64
	Log       *slog.Logger
	// Prometheus is the base URL for GET /v1/energy (ZJ_PROMETHEUS_URL); empty: energy not available
	Prometheus string
}

var _ api.StrictServerInterface = (*Server)(nil)

func problem(status int, title, detail string) api.Problem {
	p := api.Problem{Status: status, Title: title}
	if detail != "" {
		p.Detail = &detail
	}
	return p
}

// Healthz implements liveness.
func (s *Server) Healthz(context.Context, api.HealthzRequestObject) (api.HealthzResponseObject, error) {
	return api.Healthz200Response{}, nil
}

// Readyz checks the database and both model servers.
func (s *Server) Readyz(ctx context.Context, _ api.ReadyzRequestObject) (api.ReadyzResponseObject, error) {
	var failed []string
	if err := s.Pool.Ping(ctx); err != nil {
		failed = append(failed, "postgres: "+err.Error())
	}
	for name, p := range s.Upstreams {
		if err := p.Ready(ctx); err != nil {
			failed = append(failed, name+": "+err.Error())
		}
	}
	if len(failed) > 0 {
		return api.Readyz503ApplicationProblemPlusJSONResponse{ProblemApplicationProblemPlusJSONResponse: api.ProblemApplicationProblemPlusJSONResponse(
			problem(http.StatusServiceUnavailable, "not ready", strings.Join(failed, "; ")))}, nil
	}
	return api.Readyz200Response{}, nil
}

// Ingest stores the upload on the shared volume and enqueues the job in the same transaction as its row.
func (s *Server) Ingest(ctx context.Context, req api.IngestRequestObject) (api.IngestResponseObject, error) {
	bad := func(status int, title, detail string) (api.IngestResponseObject, error) {
		p := problem(status, title, detail)
		if status == http.StatusRequestEntityTooLarge {
			return api.Ingest413ApplicationProblemPlusJSONResponse(p), nil
		}
		return api.Ingest400ApplicationProblemPlusJSONResponse{ProblemApplicationProblemPlusJSONResponse: api.ProblemApplicationProblemPlusJSONResponse(p)}, nil
	}
	var rnd [12]byte
	if _, err := rand.Read(rnd[:]); err != nil {
		return nil, err
	}
	path := filepath.Join(s.UploadDir, hex.EncodeToString(rnd[:])+".csv")
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640) //nolint:gosec // random name in our dir
	if err != nil {
		return nil, fmt.Errorf("upload: %w", err)
	}
	n, err := io.Copy(f, io.LimitReader(req.Body, s.MaxUpload+1))
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	switch {
	case err != nil:
		_ = os.Remove(path)
		return bad(http.StatusBadRequest, "upload failed", err.Error())
	case n > s.MaxUpload:
		_ = os.Remove(path)
		return bad(http.StatusRequestEntityTooLarge, "upload too large", fmt.Sprintf("limit %d bytes", s.MaxUpload))
	case n == 0:
		_ = os.Remove(path)
		return bad(http.StatusBadRequest, "empty upload", "")
	}
	var job api.Job
	err = pgx.BeginFunc(ctx, s.Pool, func(tx pgx.Tx) error {
		if err := tx.QueryRow(ctx, "INSERT INTO ingest_jobs (path, bytes) VALUES ($1, $2) RETURNING id, created_at",
			path, n).Scan(&job.Id, &job.CreatedAt); err != nil {
			return err
		}
		_, err := s.River.InsertTx(ctx, tx, ingest.Args{JobID: job.Id, Path: path}, nil)
		return err
	})
	if err != nil {
		_ = os.Remove(path)
		return nil, err
	}
	job.State = api.JobState("queued")
	s.Log.Info("ingest queued", "job", job.Id, "bytes", n)
	return api.Ingest202JSONResponse(job), nil
}

// GetJob returns an ingest job.
func (s *Server) GetJob(ctx context.Context, req api.GetJobRequestObject) (api.GetJobResponseObject, error) {
	var j api.Job
	var state string
	var rows int64
	err := s.Pool.QueryRow(ctx, "SELECT id, state, rows, error, created_at, finished_at FROM ingest_jobs WHERE id = $1",
		req.Id).Scan(&j.Id, &state, &rows, &j.Error, &j.CreatedAt, &j.FinishedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return api.GetJob404ApplicationProblemPlusJSONResponse{ProblemApplicationProblemPlusJSONResponse: api.ProblemApplicationProblemPlusJSONResponse(
			problem(http.StatusNotFound, "job not found", ""))}, nil
	}
	if err != nil {
		return nil, err
	}
	j.State, j.Rows = api.JobState(state), &rows
	return api.GetJob200JSONResponse(j), nil
}

func searchBad(detail string) api.SearchResponseObject {
	return api.Search400ApplicationProblemPlusJSONResponse{ProblemApplicationProblemPlusJSONResponse: api.ProblemApplicationProblemPlusJSONResponse(
		problem(http.StatusBadRequest, "invalid request", detail))}
}

func runes(s string) int { return len([]rune(s)) }

// Search runs the filtered hybrid search.
func (s *Server) Search(ctx context.Context, req api.SearchRequestObject) (api.SearchResponseObject, error) {
	b := req.Body
	if b == nil || strings.TrimSpace(b.Query) == "" || runes(b.Query) > 2000 {
		return searchBad("query: 1–2000 characters"), nil
	}
	k := 20
	if b.K != nil {
		k = *b.K
	}
	if k < 1 || k > 200 {
		return searchBad("k: 1–200"), nil
	}
	var f rag.Filters
	if fl := b.Filters; fl != nil {
		if fl.Regierungsbezirk != nil && !fl.Regierungsbezirk.Valid() {
			return searchBad("filters.regierungsbezirk: unknown value"), nil
		}
		deref := func(p *string) string {
			if p == nil {
				return ""
			}
			return *p
		}
		f = rag.Filters{Werk: deref(fl.Werk), Komponente: deref(fl.Komponente), Schadensart: deref(fl.Schadensart)}
		if fl.Regierungsbezirk != nil {
			f.Regierungsbezirk = string(*fl.Regierungsbezirk)
		}
		if fl.DatumVon != nil {
			f.Von = &fl.DatumVon.Time
		}
		if fl.DatumBis != nil {
			f.Bis = &fl.DatumBis.Time
		}
	}
	hits, err := s.Engine.Search(ctx, b.Query, f, k)
	if err != nil {
		return nil, err
	}
	out := api.SearchResponse{Hits: make([]api.Hit, len(hits))}
	for i, h := range hits {
		out.Hits[i] = hitOf(h)
	}
	return api.Search200JSONResponse(out), nil
}

func hitOf(h rag.Hit) api.Hit {
	out := api.Hit{MeldungId: h.MeldungID, Score: float32(h.Score), Werk: &h.Werk, Meldungstext: &h.Meldungstext}
	if h.Datum != nil {
		out.Datum = &openapi_types.Date{Time: *h.Datum}
	}
	return out
}

type acceptKey struct{}

// AcceptMiddleware passes the Accept header into the handler context (the strict interface hides headers).
func AcceptMiddleware(f api.StrictHandlerFunc, _ string) api.StrictHandlerFunc {
	return func(ctx context.Context, w http.ResponseWriter, r *http.Request, req any) (any, error) {
		return f(context.WithValue(ctx, acceptKey{}, r.Header.Get("Accept")), w, r, req)
	}
}

func toResponse(a rag.Answer) api.AskResponse {
	route := api.AskResponseRoute(a.Route)
	if !route.Valid() {
		route = api.None // unusable router answer: reported as such, never disguised as an empty search
	}
	out := api.AskResponse{Route: route, CitedIds: a.CitedIDs, Estimated: a.Estimated, Answer: a.Value}
	if out.CitedIds == nil {
		out.CitedIds = []string{}
	}
	if a.SQL != "" {
		out.Sql = &a.SQL
	}
	if len(a.Hits) > 0 {
		rows := make([]api.Hit, len(a.Hits))
		for i, h := range a.Hits {
			rows[i] = hitOf(h)
		}
		out.CitedRows = &rows
	}
	if a.Columns != nil {
		out.Columns = &a.Columns
	}
	if st := api.AskResponseSqlStatus(a.SQLStatus); st.Valid() {
		out.SqlStatus = &st
	}
	return out
}

// Ask answers a question as JSON, or as Server-Sent Events (route, plan, answer, done) when the client asks for them.
func (s *Server) Ask(ctx context.Context, req api.AskRequestObject) (api.AskResponseObject, error) {
	if req.Body == nil || strings.TrimSpace(req.Body.Question) == "" || runes(req.Body.Question) > 2000 {
		return api.Ask400ApplicationProblemPlusJSONResponse{ProblemApplicationProblemPlusJSONResponse: api.ProblemApplicationProblemPlusJSONResponse(
			problem(http.StatusBadRequest, "invalid request", "question: 1–2000 characters"))}, nil
	}
	q := req.Body.Question
	if accept, _ := ctx.Value(acceptKey{}).(string); strings.Contains(accept, "text/event-stream") {
		return sseAsk{ctx: ctx, s: s, q: q}, nil
	}
	a, err := s.Engine.Ask(ctx, q, nil)
	if err != nil {
		return nil, err
	}
	if a.Rejected != "" {
		s.Log.Warn("router sql not used", "reason", a.Rejected)
	}
	return api.Ask200JSONResponse(toResponse(a)), nil
}

// sseAsk streams the route as soon as the router has generated it, the router plan (SQL or search text), then the
// answer (ADR 0027).
type sseAsk struct {
	ctx context.Context //nolint:containedctx // the strict interface visits the response after the handler returns
	s   *Server
	q   string
}

func (r sseAsk) VisitAskResponse(w http.ResponseWriter) error {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	fl, _ := w.(http.Flusher)
	var mu sync.Mutex
	emit := func(event string, v any) {
		b, _ := json.Marshal(v)
		mu.Lock()
		defer mu.Unlock()
		_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, b)
		if fl != nil {
			fl.Flush()
		}
	}
	a, err := r.s.Engine.Ask(r.ctx, r.q, &rag.Progress{
		Route: func(rt string) {
			route := api.AskResponseRoute(rt)
			if !route.Valid() {
				route = api.None
			}
			emit("route", map[string]any{"route": route})
		},
		Plan: func(o rag.Routed) { emit("plan", map[string]any{"sql": o.SQL, "suchtext": o.Suchtext}) },
	})
	if err != nil {
		emit("error", problem(http.StatusBadGateway, "ask failed", err.Error()))
		return nil
	}
	emit("answer", toResponse(a))
	emit("done", map[string]any{})
	return nil
}

var extractFields = []string{"komponente", "schadensart", "ursache", "massnahme", "sicherheitsrelevant",
	"stillstand_minuten", "ersatzteil_materialnr"}

func extractUser(r api.ExtractRow) string {
	d := func(p *string) string {
		if p == nil {
			return ""
		}
		return *p
	}
	return "Anlage: " + d(r.Anlage) + "\nEquipment: " + d(r.EquipmentNr) + "\nMeldungstext: " + r.Meldungstext +
		"\nStillstand: " + d(r.Stillstand)
}

// Extract runs the Track D 3-shot extraction per row (the measured d04 configuration on the shared vLLM).
func (s *Server) Extract(ctx context.Context, req api.ExtractRequestObject) (api.ExtractResponseObject, error) {
	bad := func(d string) (api.ExtractResponseObject, error) {
		return api.Extract400ApplicationProblemPlusJSONResponse{ProblemApplicationProblemPlusJSONResponse: api.ProblemApplicationProblemPlusJSONResponse(
			problem(http.StatusBadRequest, "invalid request", d))}, nil
	}
	if req.Body == nil || len(req.Body.Rows) == 0 || len(req.Body.Rows) > 500 {
		return bad("rows: 1–500")
	}
	for i, r := range req.Body.Rows {
		if strings.TrimSpace(r.Meldungstext) == "" || runes(r.Meldungstext) > 4000 {
			return bad(fmt.Sprintf("rows[%d].meldungstext: 1–4000 characters", i))
		}
	}
	p := s.Engine.P.Extract
	out := make([]api.ExtractResult, len(req.Body.Rows))
	g, gctx := errgroup.WithContext(ctx)
	for i, r := range req.Body.Rows {
		g.Go(func() error {
			res, err := s.Engine.LLM.JSON(gctx, "extract", p.Messages(extractUser(r)), p.Schema, p.MaxTokens)
			if err != nil {
				return err
			}
			out[i] = parseExtract(res.Text)
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return nil, err
	}
	return api.Extract200JSONResponse(api.ExtractResponse{Results: out}), nil
}

// parseExtract applies the Track D validity rule: a JSON object with exactly the seven fields.
func parseExtract(text string) api.ExtractResult {
	var m map[string]any
	if json.Unmarshal([]byte(strings.TrimSpace(text)), &m) != nil || len(m) != len(extractFields) {
		return api.ExtractResult{Valid: false}
	}
	for _, f := range extractFields {
		if _, ok := m[f]; !ok {
			return api.ExtractResult{Valid: false}
		}
	}
	return api.ExtractResult{Valid: true, Fields: &m}
}

// ProblemHandler writes RFC 9457 problem details for decode and internal errors.
func ProblemHandler(log *slog.Logger, status int) func(w http.ResponseWriter, r *http.Request, err error) {
	return func(w http.ResponseWriter, r *http.Request, err error) {
		title := "invalid request"
		detail := err.Error()
		if status >= 500 {
			title, detail = "internal error", ""
			if errors.Is(err, context.DeadlineExceeded) {
				status, title = http.StatusGatewayTimeout, "deadline exceeded"
			}
			log.Error("request failed", "path", r.URL.Path, "err", err)
		}
		writeProblem(w, problem(status, title, detail))
	}
}

func writeProblem(w http.ResponseWriter, p api.Problem) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(p.Status)
	_ = json.NewEncoder(w).Encode(p)
}
