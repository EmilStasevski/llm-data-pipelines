// Command zj-e2e is contestant e06_stack_go (Track E, task e.rag): the Phase 9 production stack measured end to
// end through its public API, the same raw CSV and the same 300 questions as e01–e05 (ADR 0025).
//
//	load    docker compose up (profile gpu) on fresh volumes: Postgres, migration, gateway, worker, TEI, vLLM
//	ingest  POST /v1/ingest with the raw CSV, poll the job (parse → label → embed → write → index)
//	answer  300 × POST /v1/ask with --concurrency clients
//	write   out.jsonl (scorer format) + ingest.json
//
// The stack is torn down (volumes included) before the ZJRESULT line. Run by the harness (`zj run`); the
// compose project "zeilenjoule-bench" never touches the volumes of a normal `task up` stack.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"zeilenjoule/services/internal/benchstack"
)

type options struct {
	input, output string
	stack         benchstack.Options
	concurrency   int
	keep          bool
	questions     int
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "zj-e2e:", err)
		os.Exit(1)
	}
}

// ---------------------------------------------------------------- API client

type client struct{ *benchstack.Client }

type askResponse struct {
	Route     string   `json:"route"`
	SQL       *string  `json:"sql"`
	SQLStatus *string  `json:"sql_status"`
	Answer    any      `json:"answer"`
	Columns   []string `json:"columns"`
	CitedIDs  []string `json:"cited_ids"`
	Estimated bool     `json:"estimated"`
}

// record is one line of out.jsonl, the Track E scorer format (zj_eval.track_e).
type record struct {
	QID       string   `json:"qid"`
	Route     *string  `json:"route"`
	Answer    any      `json:"answer"`
	CitedIDs  []string `json:"cited_ids"`
	SQL       *string  `json:"sql,omitempty"`
	SQLStatus *string  `json:"sql_status,omitempty"`
	Estimated bool     `json:"estimated"`
	LatencyMS float64  `json:"latency_ms"`
	Error     string   `json:"error,omitempty"`
}

func (c *client) askAll(ctx context.Context, qs []benchstack.Question, n int) []record {
	out := make([]record, len(qs))
	work := make(chan int)
	var wg sync.WaitGroup
	for range n {
		wg.Go(func() {
			for i := range work {
				out[i] = c.ask(ctx, qs[i])
			}
		})
	}
	for i := range qs {
		work <- i
	}
	close(work)
	wg.Wait()
	return out
}

func (c *client) ask(ctx context.Context, q benchstack.Question) record {
	body, _ := json.Marshal(map[string]string{"question": q.Text})
	t0 := time.Now()
	var a askResponse
	err := c.Do(ctx, http.MethodPost, "/v1/ask", "application/json", bytes.NewReader(body), &a)
	rec := record{QID: q.QID, LatencyMS: float64(time.Since(t0).Microseconds()) / 1e3, CitedIDs: []string{}}
	if err != nil {
		rec.Error = err.Error()
		return rec // no route: scored as wrong, like an unparsable router answer in e01–e05
	}
	if a.Route == "sql" || a.Route == "search" {
		rec.Route = &a.Route
	}
	rec.Answer, rec.SQL, rec.SQLStatus, rec.Estimated = a.Answer, a.SQL, a.SQLStatus, a.Estimated
	if a.CitedIDs != nil {
		rec.CitedIDs = a.CitedIDs
	}
	return rec
}

// ---------------------------------------------------------------- main

func run() error {
	var o options
	flag.StringVar(&o.input, "input", "", "raw CSV (cp1252), data/synth/stoermeldungen/<scale>/stoermeldungen.cp1252.csv")
	flag.StringVar(&o.output, "output", "", "out.jsonl")
	flag.IntVar(&o.stack.Threads, "threads", 0, "parser threads (0 = all cores)")
	flag.IntVar(&o.concurrency, "concurrency", 64, "parallel /v1/ask clients (e03: 64)")
	flag.IntVar(&o.stack.Port, "port", 8080, "gateway port on 127.0.0.1")
	flag.StringVar(&o.stack.Compose, "compose", "deploy/compose/compose.yaml", "compose file")
	flag.BoolVar(&o.keep, "keep", false, "debug: leave the stack running")
	flag.IntVar(&o.questions, "questions", 0, "debug: first N questions only")
	versions := flag.Bool("versions", false, "print the version fingerprint (Go, image IDs) as JSON and exit")
	flag.Parse()
	if *versions {
		return benchstack.PrintVersions(o.stack, "e06_stack_go")
	}
	if o.input == "" || o.output == "" {
		return errors.New("usage: zj-e2e --input CSV --output out.jsonl [--threads N] [--concurrency N]")
	}
	if o.stack.Threads <= 0 {
		o.stack.Threads = runtime.NumCPU()
	}
	qs, err := benchstack.QuestionsFor(o.input)
	if err != nil {
		return err
	}
	if o.questions > 0 {
		qs = qs[:min(o.questions, len(qs))]
	}
	ctx := context.Background()
	s := benchstack.New(o.stack)
	_ = s.Down(ctx) // leftovers of an aborted run (bench project only)
	defer func() {
		if !o.keep {
			if err := s.Down(context.WithoutCancel(ctx)); err != nil {
				fmt.Fprintln(os.Stderr, "teardown:", err)
			}
		}
	}()
	c := &client{&benchstack.Client{Base: o.stack.Base(), Key: s.Key, HTTP: &http.Client{Timeout: 10 * time.Minute}}}

	if err := benchstack.Stage("load", func() error { return s.Up(ctx) }); err != nil {
		return err
	}
	var j benchstack.Job
	if err := benchstack.Stage("ingest", func() (err error) { j, err = c.Ingest(ctx, o.input); return err }); err != nil {
		return err
	}
	var out []record
	t0 := time.Now()
	_ = benchstack.Stage("answer", func() error { out = c.askAll(ctx, qs, o.concurrency); return nil })
	answerS := time.Since(t0).Seconds()

	info := map[string]any{"pipeline": "e06_stack_go", "ingest_job": j, "sql_unsafe_executed": 0}
	var stats map[string]any
	err = benchstack.Stage("write", func() error {
		var buf bytes.Buffer
		enc := json.NewEncoder(&buf)
		enc.SetEscapeHTML(false)
		for _, r := range out {
			if err := enc.Encode(r); err != nil {
				return err
			}
		}
		if err := os.WriteFile(o.output, buf.Bytes(), 0o644); err != nil { //nolint:gosec // result file, read by the scorer
			return err
		}
		stats = summarize(out, answerS)
		for _, k := range []string{"sql_executed", "sql_rejected", "sql_failed", "http_errors", "route_none"} {
			info[k] = stats[k]
		}
		// classifier of the first import (holdout accuracy) and label provenance, straight from the database
		label := map[string]any{"label_sample": nil, "label_classifier_holdout_acc": nil}
		if v := strings.Split(s.Psql(ctx, "SELECT train_rows, round(holdout_acc::numeric, 4) FROM classifier ORDER BY id DESC LIMIT 1"), "|"); len(v) == 2 {
			n, _ := strconv.Atoi(v[0])
			acc, _ := strconv.ParseFloat(v[1], 64)
			label = map[string]any{"label_sample": n, "label_classifier_holdout_acc": acc}
		}
		label["schadensart_quelle"] = s.Psql(ctx, "SELECT string_agg(schadensart_quelle || '=' || n, ',') FROM (SELECT schadensart_quelle, count(*) n FROM docs GROUP BY 1 ORDER BY 1) t")
		info["label"] = label
		b, _ := json.MarshalIndent(info, "", " ")
		return os.WriteFile(filepath.Join(filepath.Dir(o.output), "ingest.json"), b, 0o644) //nolint:gosec // as above
	})
	if err != nil {
		return err
	}
	images := s.ImageDigests(ctx)
	if !o.keep {
		if err := s.Down(ctx); err != nil {
			return err
		}
		o.keep = true // already down; skip the deferred teardown
	}
	extra := map[string]any{
		"engine":      "cuda (server)",
		"pipeline":    "e06_stack_go",
		"concurrency": o.concurrency,
		"services":    []string{"PostgreSQL", "TEI", "vLLM", "zj-gateway", "zj-worker"},
		"images":      images,
		"latency_ms":  stats["latency_ms"],
		"questions":   len(out),
		"answer_s":    stats["answer_s"],
		"sql":         map[string]any{"sql_executed": info["sql_executed"], "sql_rejected": info["sql_rejected"], "sql_failed": info["sql_failed"], "sql_unsafe_executed": 0},
		"http_errors": info["http_errors"],
		"route_none":  info["route_none"],
		"label":       info["label"],
	}
	res, _ := json.Marshal(map[string]any{"rows": j.Rows, "notes": "e06_stack_go: production stack (compose) through its HTTP API", "extra": extra})
	fmt.Println("ZJRESULT " + string(res))
	return nil
}

func summarize(out []record, answerS float64) map[string]any {
	st := map[string]any{"sql_executed": 0, "sql_rejected": 0, "sql_failed": 0, "http_errors": 0, "route_none": 0}
	lat := make([]float64, 0, len(out))
	inc := func(k string) { st[k] = st[k].(int) + 1 }
	for _, r := range out {
		lat = append(lat, r.LatencyMS)
		switch {
		case r.Error != "":
			inc("http_errors")
		case r.Route == nil:
			inc("route_none")
		}
		if r.SQLStatus != nil {
			inc("sql_" + *r.SQLStatus)
		}
	}
	sort.Float64s(lat)
	if len(lat) > 0 {
		st["latency_ms"] = map[string]float64{"p50": lat[len(lat)/2], "p95": lat[int(float64(len(lat))*0.95)],
			"p99": lat[int(float64(len(lat))*0.99)], "max": lat[len(lat)-1]}
	}
	st["answer_s"] = answerS
	return st
}
