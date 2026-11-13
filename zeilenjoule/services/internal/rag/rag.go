// Package rag answers questions like the measured e03 pipeline (zj_e2e.pipeline.answer_all): one constrained
// router call (sql ↔ search + self-query filters), router SQL checked by sqlguard and run as the SELECT-only
// role, hybrid search = pgvector HNSW ⊕ German full text fused with RRF (k = 60), and a second LLM call only when
// the answer stands in the message text.
package rag

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"

	"zeilenjoule/services/internal/embed"
	"zeilenjoule/services/internal/llm"
	"zeilenjoule/services/internal/metrics"
	"zeilenjoule/services/internal/prompts"
	"zeilenjoule/services/internal/sqlguard"
)

// Depth is how many candidates each search leg returns before fusion (Track C/E setting).
const Depth = 100

// MaxSQLRows caps a router SQL result.
const MaxSQLRows = 1000

// LLM is the subset of llm.Client the engine needs (tests use a fake).
type LLM interface {
	JSON(ctx context.Context, op string, msgs []prompts.Message, schema json.RawMessage, maxTokens int) (llm.Result, error)
}

// Embedder is the subset of embed.Client the engine needs.
type Embedder interface {
	Embed(ctx context.Context, op string, texts []string) ([][]float32, error)
}

// Engine holds the pools and clients.
type Engine struct {
	Owner      *pgxpool.Pool // owner pool: vector + full-text search
	Reader     *pgxpool.Pool // zj_reader login: router SQL only
	LLM        LLM
	Emb        Embedder
	P          *prompts.Set
	SQLTimeout time.Duration
}

// Filters are exact-match self-query filters (catalog values only) and an inclusive date range.
type Filters struct {
	Werk, Regierungsbezirk, Komponente, Schadensart string
	Von, Bis                                        *time.Time
}

// Hit is one search result.
type Hit struct {
	MeldungID    string
	Score        float64
	Werk         string
	Datum        *time.Time
	Meldungstext string
}

// Routed is the router's JSON answer (schema in prompts.json).
type Routed struct {
	Route            string  `json:"route"`
	SQL              *string `json:"sql"`
	AntwortAus       *string `json:"antwort_aus"`
	Suchtext         *string `json:"suchtext"`
	Werk             *string `json:"werk"`
	Regierungsbezirk *string `json:"regierungsbezirk"`
	DatumVon         *string `json:"datum_von"`
	DatumBis         *string `json:"datum_bis"`
	Komponente       *string `json:"komponente"`
	Schadensart      *string `json:"schadensart"`
}

// Answer is the result of Ask.
type Answer struct {
	Route     string   // sql | search | invalid
	SQL       string   // executed SQL (route sql, guard passed)
	Rejected  string   // guard or database error, when the SQL could not be used
	SQLStatus string   // route sql: executed | rejected (guard) | failed (database)
	Value     any      // table rows ([][]any, in Columns order), text, or nil
	Columns   []string // column names of a table answer
	CitedIDs  []string
	Estimated bool // depends on the estimated column schadensart
}

func str(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

// Route runs the router call.
func (e *Engine) Route(ctx context.Context, question string) (Routed, error) {
	r := e.P.Router
	res, err := e.LLM.JSON(ctx, "router", r.Messages(question), r.Schema, r.MaxTokens)
	if err != nil {
		return Routed{}, err
	}
	var o Routed
	if err := json.Unmarshal([]byte(res.Text), &o); err != nil {
		return Routed{Route: "invalid"}, nil //nolint:nilerr // an unparsable route is a wrong answer, not an outage
	}
	return o, nil
}

// Ask answers one question. Progress events (route chosen) go to onRoute when non-nil.
func (e *Engine) Ask(ctx context.Context, question string, onRoute func(Routed)) (Answer, error) {
	o, err := e.Route(ctx, question)
	if err != nil {
		return Answer{}, err
	}
	if onRoute != nil {
		onRoute(o)
	}
	switch {
	case o.Route == "sql" && str(o.SQL) != "":
		metrics.Routes.WithLabelValues("sql").Inc()
		return e.askSQL(ctx, question, o)
	case o.Route == "search":
		metrics.Routes.WithLabelValues("search").Inc()
		f := e.filters(o)
		q := str(o.Suchtext)
		if q == "" {
			q = question
		}
		hits, err := e.Search(ctx, q, f, e.P.Search.K)
		if err != nil {
			return Answer{}, err
		}
		ids := make([]string, len(hits))
		for i, h := range hits {
			ids[i] = h.MeldungID
		}
		return Answer{Route: "search", CitedIDs: ids, Estimated: f.Schadensart != ""}, nil
	default:
		metrics.Routes.WithLabelValues("invalid").Inc()
		return Answer{Route: "invalid", CitedIDs: []string{}}, nil
	}
}

// filters keeps only catalog values (self-query, as in the benchmark) and valid ISO dates.
func (e *Engine) filters(o Routed) Filters {
	pick := func(key string, v *string) string {
		if s := str(v); s != "" && e.P.InCatalog(key, s) {
			return s
		}
		return ""
	}
	date := func(v *string) *time.Time {
		t, err := time.Parse(time.DateOnly, str(v))
		if err != nil {
			return nil
		}
		return &t
	}
	return Filters{Werk: pick("werk", o.Werk), Regierungsbezirk: pick("regierungsbezirk", o.Regierungsbezirk),
		Komponente: pick("komponente", o.Komponente), Schadensart: pick("schadensart", o.Schadensart),
		Von: date(o.DatumVon), Bis: date(o.DatumBis)}
}

func (e *Engine) askSQL(ctx context.Context, question string, o Routed) (Answer, error) {
	sql := strings.TrimSpace(strings.TrimRight(strings.TrimSpace(str(o.SQL)), ";"))
	a := Answer{Route: "sql", CitedIDs: []string{},
		Estimated: strings.Contains(strings.ToLower(sql), "schadensart")}
	if err := sqlguard.Check(sql); err != nil {
		metrics.SQL.WithLabelValues("rejected").Inc()
		a.Rejected, a.SQLStatus = err.Error(), "rejected"
		return a, nil
	}
	a.SQL = sql
	cols, rows, err := e.RunSQL(ctx, sql)
	if err != nil {
		if ctx.Err() != nil {
			return Answer{}, ctx.Err()
		}
		metrics.SQL.WithLabelValues("failed").Inc()
		a.Rejected, a.SQLStatus = err.Error(), "failed"
		return a, nil
	}
	metrics.SQL.WithLabelValues("executed").Inc()
	a.SQLStatus = "executed"
	mid := index(cols, "meldung_id")
	if str(o.AntwortAus) != "text" || (mid < 0 && index(cols, "meldungstext") < 0) {
		a.Value, a.Columns = rows, cols
		if rows == nil {
			a.Value = [][]any{}
		}
		return a, nil
	}
	top := rows[:min(len(rows), e.P.Answer.MaxRows)]
	var ctxLines []string
	for j, row := range top {
		id := fmt.Sprint(j + 1)
		if mid >= 0 {
			id = fmt.Sprint(row[mid])
			a.CitedIDs = append(a.CitedIDs, id)
		}
		var parts []string
		for c, v := range row {
			if c != mid {
				parts = append(parts, fmt.Sprint(v))
			}
		}
		ctxLines = append(ctxLines, "["+id+"] "+strings.Join(parts, " | "))
	}
	if len(top) == 0 {
		return a, nil
	}
	ans := e.P.Answer
	msgs := []prompts.Message{{Role: "system", Content: ans.System},
		{Role: "user", Content: "Frage: " + question + "\n\nMeldungen:\n" + strings.Join(ctxLines, "\n")}}
	res, err := e.LLM.JSON(ctx, "answer", msgs, ans.Schema, ans.MaxTokens)
	if err != nil {
		return Answer{}, err
	}
	var out struct {
		Antwort *string  `json:"antwort"`
		Zitiert []string `json:"zitiert"`
	}
	if json.Unmarshal([]byte(res.Text), &out) == nil {
		if out.Antwort != nil {
			a.Value = *out.Antwort
		}
		var cited []string
		for _, z := range out.Zitiert {
			if index(a.CitedIDs, z) >= 0 {
				cited = append(cited, z)
			}
		}
		if len(cited) > 0 { // no id selected by the SQL → nothing to cite but the SQL rows
			a.CitedIDs = cited
		}
	}
	return a, nil
}

// RunSQL executes guarded SQL as zj_reader in a read-only transaction with a statement timeout.
func (e *Engine) RunSQL(ctx context.Context, sql string) ([]string, [][]any, error) {
	t0 := time.Now()
	var cols []string
	var out [][]any
	err := pgx.BeginTxFunc(ctx, e.Reader, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, fmt.Sprintf("SET LOCAL statement_timeout = %d", e.SQLTimeout.Milliseconds())); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, sql, pgx.QueryExecModeSimpleProtocol)
		if err != nil {
			return err
		}
		defer rows.Close()
		for _, fd := range rows.FieldDescriptions() {
			cols = append(cols, fd.Name)
		}
		for rows.Next() && len(out) < MaxSQLRows {
			vals, err := rows.Values()
			if err != nil {
				return err
			}
			for i, v := range vals {
				vals[i] = plain(v)
			}
			out = append(out, vals)
		}
		return rows.Err()
	})
	outcome := "ok"
	if err != nil {
		outcome = "error"
	}
	metrics.Upstream.WithLabelValues("postgres", "router_sql", outcome).Observe(time.Since(t0).Seconds())
	return cols, out, err
}

// plain converts pgx values to JSON-friendly ones (numeric → float, date → YYYY-MM-DD).
func plain(v any) any {
	switch x := v.(type) {
	case pgtype.Numeric:
		if !x.Valid {
			return nil
		}
		f, err := x.Float64Value()
		if err != nil || !f.Valid {
			return nil
		}
		return f.Float64
	case *big.Int:
		f, _ := new(big.Float).SetInt(x).Float64()
		return f
	case time.Time:
		if x.Hour() == 0 && x.Minute() == 0 && x.Second() == 0 && x.Nanosecond() == 0 {
			return x.Format(time.DateOnly)
		}
		return x.Format(time.RFC3339)
	case [16]byte:
		return fmt.Sprintf("%x", x)
	default:
		return v
	}
}

func index(xs []string, s string) int {
	for i, x := range xs {
		if x == s {
			return i
		}
	}
	return -1
}

// where builds the filter clause with numbered parameters starting at $first.
func where(f Filters, first int) (string, []any) {
	conds, args := []string{"TRUE"}, []any{}
	add := func(cond string, v any) {
		args = append(args, v)
		conds = append(conds, fmt.Sprintf(cond, first+len(args)-1))
	}
	for _, kv := range [][2]string{{"werk", f.Werk}, {"regierungsbezirk", f.Regierungsbezirk},
		{"komponente", f.Komponente}, {"schadensart", f.Schadensart}} {
		if kv[1] != "" {
			add(kv[0]+" = $%d", kv[1])
		}
	}
	if f.Von != nil {
		add("datum >= $%d", *f.Von)
	}
	if f.Bis != nil {
		add("datum <= $%d", *f.Bis)
	}
	return strings.Join(conds, " AND "), args
}

// RRF fuses ranked id lists: score = Σ 1/(k + rank); ties broken by id.
func RRF(k int, legs ...[]string) ([]string, map[string]float64) {
	score := map[string]float64{}
	for _, leg := range legs {
		for r, id := range leg {
			score[id] += 1 / float64(k+r+1)
		}
	}
	ids := make([]string, 0, len(score))
	for id := range score {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(a, b int) bool {
		if score[ids[a]] != score[ids[b]] {
			return score[ids[a]] > score[ids[b]]
		}
		return ids[a] < ids[b]
	})
	return ids, score
}

const tsQuery = "replace(plainto_tsquery('german', $1)::text, '&', '|')::tsquery"

// Search runs the filtered hybrid search and returns the top k hits with their display columns.
func (e *Engine) Search(ctx context.Context, text string, f Filters, k int) ([]Hit, error) {
	if strings.TrimSpace(text) == "" {
		return nil, errors.New("rag: empty query")
	}
	vecs, err := e.Emb.Embed(ctx, "query", []string{text})
	if err != nil {
		return nil, err
	}
	t0 := time.Now()
	var dense, lex []string
	err = pgx.BeginTxFunc(ctx, e.Owner, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, fmt.Sprintf("SET LOCAL hnsw.ef_search = %d", Depth)); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "SET LOCAL hnsw.iterative_scan = strict_order"); err != nil {
			return err
		}
		// pgx caches prepared statements; after five runs Postgres may switch to a generic plan that estimates a
		// few hundred lexical matches (an OR tsquery often matches a third of the table) and ranks them in one
		// bitmap heap scan, ~3× slower than the custom plan. Same SQL, same order: identical results (ADR 0026).
		if _, err := tx.Exec(ctx, "SET LOCAL plan_cache_mode = force_custom_plan"); err != nil {
			return err
		}
		w, args := where(f, 2)
		dense, err = ids(ctx, tx, fmt.Sprintf("SELECT meldung_id FROM docs WHERE %s ORDER BY emb <#> $1::halfvec LIMIT %d",
			w, Depth), append([]any{embed.Literal(vecs[0])}, args...)...)
		if err != nil {
			return err
		}
		lex, err = ids(ctx, tx, fmt.Sprintf("SELECT meldung_id FROM docs, (SELECT %s AS q) t WHERE %s AND tsv @@ t.q "+
			"ORDER BY ts_rank(tsv, t.q) DESC, meldung_id LIMIT %d", tsQuery, w, Depth), append([]any{text}, args...)...)
		return err
	})
	if err != nil {
		metrics.Upstream.WithLabelValues("postgres", "search", "error").Observe(time.Since(t0).Seconds())
		return nil, err
	}
	fused, score := RRF(e.P.Search.RRFK, dense, lex)
	fused = fused[:min(k, len(fused))]
	hits := make([]Hit, len(fused))
	pos := map[string]int{}
	for i, id := range fused {
		hits[i] = Hit{MeldungID: id, Score: score[id]}
		pos[id] = i
	}
	rows, err := e.Owner.Query(ctx, "SELECT meldung_id, werk, datum, meldungstext FROM docs WHERE meldung_id = ANY($1)", fused)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id, werk, text string
		var d pgtype.Date
		if err := rows.Scan(&id, &werk, &d, &text); err != nil {
			return nil, err
		}
		h := &hits[pos[id]]
		h.Werk, h.Meldungstext = werk, text
		if d.Valid {
			t := d.Time
			h.Datum = &t
		}
	}
	metrics.Upstream.WithLabelValues("postgres", "search", "ok").Observe(time.Since(t0).Seconds())
	return hits, rows.Err()
}

func ids(ctx context.Context, tx pgx.Tx, sql string, args ...any) ([]string, error) {
	rows, err := tx.Query(ctx, sql, args...)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowTo[string])
}
