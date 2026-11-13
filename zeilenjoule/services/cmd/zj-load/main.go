// Command zj-load is contestant e07_stack_load (Track E, task e.load): the latency and energy curve of the Phase 9
// production stack under open-loop load (PLAN §12.3, ADR 0026).
//
//	load            docker compose up (profile gpu) on fresh volumes, as e06_stack_go
//	ingest          POST /v1/ingest with the raw CSV of the scale, poll the job
//	warm            a few searches and questions (caches, CUDA graphs); not part of the curve
//	search_<r>      POST /v1/search at r requests/s for --step
//	ask_<r>         POST /v1/ask (SSE) at r requests/s for --ask-step
//	write           out.json
//
// Open loop: request i is due at t0 + i/r whether or not earlier ones returned, and its latency counts from that due
// time, so a slow server cannot slow the generator down (no coordinated omission). Each rate step is a ZJMARK stage:
// the harness measures its wall time and GPU/CPU energy like every other stage, and J/request = stage energy /
// requests sent (analysis). Errors under overload (timeouts, 5xx) are results; a 2xx answer of the wrong shape or a
// 4xx is a defect and fails verification (harness builtin e.load).
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math"
	"net"
	"net/http"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"zeilenjoule/services/internal/benchstack"
)

type options struct {
	input, output         string
	stack                 benchstack.Options
	searchRates, askRates []float64
	step, askStep         time.Duration
	timeout               time.Duration
	maxInFlight           int
	keep                  bool
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "zj-load:", err)
		os.Exit(1)
	}
}

// ---------------------------------------------------------------- one request

// outcome kinds; ok and the error kinds are mutually exclusive
const (
	kindOK        = "ok"
	kindTimeout   = "timeout"   // client timeout (> gateway timeout)
	kind5xx       = "http_5xx"  // gateway/upstream error, incl. its own 504
	kind429       = "http_429"  // rate limited (disabled in the bench; would show a misconfiguration)
	kind4xx       = "http_4xx"  // client defect → verification fails
	kindTransport = "transport" // connection refused/reset
	kindUpstream  = "upstream"  // SSE "error" event (the ask failed after the stream started)
	kindInvalid   = "invalid"   // 2xx with a wrong shape → verification fails
	kindOverload  = "overload"  // generator: --max-in-flight reached, request not sent
)

type outcome struct {
	kind   string
	latMS  float64 // due → complete answer
	firstM float64 // due → first SSE event (ask only)
}

func classify(err error) string {
	var se *benchstack.StatusError
	var ne net.Error
	switch {
	case errors.As(err, &se) && se.Code == http.StatusTooManyRequests:
		return kind429
	case errors.As(err, &se) && se.Code >= 500:
		return kind5xx
	case errors.As(err, &se):
		return kind4xx
	case errors.Is(err, context.DeadlineExceeded), errors.As(err, &ne) && ne.Timeout():
		return kindTimeout
	default:
		return kindTransport
	}
}

func ms(d time.Duration) float64 { return float64(d.Microseconds()) / 1e3 }

type loader struct {
	c       *benchstack.Client
	qs      []benchstack.Question
	timeout time.Duration
}

func (l *loader) search(ctx context.Context, i int, due time.Time) outcome {
	ctx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()
	body, _ := json.Marshal(map[string]any{"query": l.qs[i%len(l.qs)].Text, "k": 10})
	var r struct {
		Hits []struct {
			MeldungID string `json:"meldung_id"`
		} `json:"hits"`
	}
	err := l.c.Do(ctx, http.MethodPost, "/v1/search", "application/json", bytes.NewReader(body), &r)
	o := outcome{latMS: ms(time.Since(due))}
	switch {
	case err != nil:
		o.kind = classify(err)
	case len(r.Hits) == 0 || len(r.Hits) > 10:
		o.kind = kindInvalid
	default:
		o.kind = kindOK
		for _, h := range r.Hits {
			if h.MeldungID == "" {
				o.kind = kindInvalid
			}
		}
	}
	return o
}

// ask streams one question: event "route" first (router decision), then "answer" and "done", or "error".
func (l *loader) ask(ctx context.Context, i int, due time.Time) outcome {
	ctx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()
	body, _ := json.Marshal(map[string]string{"question": l.qs[i%len(l.qs)].Text})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, l.c.Base+"/v1/ask", bytes.NewReader(body))
	if err != nil {
		return outcome{kind: kindTransport}
	}
	req.Header.Set("X-API-Key", l.c.Key)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "text/event-stream")
	resp, err := l.c.HTTP.Do(req)
	if err != nil {
		return outcome{kind: classify(err), latMS: ms(time.Since(due))}
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode/100 != 2 {
		return outcome{kind: classify(&benchstack.StatusError{Method: "POST", Path: "/v1/ask", Code: resp.StatusCode}), latMS: ms(time.Since(due))}
	}
	return readSSE(resp.Body, due)
}

func readSSE(r interface{ Read([]byte) (int, error) }, due time.Time) outcome {
	o := outcome{kind: kindInvalid}
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64<<10), 8<<20)
	var event string
	seenRoute, seenAnswer := false, false
	for sc.Scan() {
		line := sc.Text()
		switch {
		case strings.HasPrefix(line, "event: "):
			event = strings.TrimPrefix(line, "event: ")
			if o.firstM == 0 {
				o.firstM = ms(time.Since(due))
			}
		case strings.HasPrefix(line, "data: "):
			data := []byte(strings.TrimPrefix(line, "data: "))
			switch event {
			case "route":
				var v struct {
					Route string `json:"route"`
				}
				seenRoute = json.Unmarshal(data, &v) == nil && validRoute(v.Route)
			case "answer":
				var v struct {
					Route  string `json:"route"`
					Answer any    `json:"answer"`
				}
				seenAnswer = json.Unmarshal(data, &v) == nil && validRoute(v.Route)
			case "error":
				o.kind, o.latMS = kindUpstream, ms(time.Since(due))
				return o
			case "done":
				o.latMS = ms(time.Since(due))
				if seenRoute && seenAnswer {
					o.kind = kindOK
				}
				return o
			}
		}
	}
	o.latMS = ms(time.Since(due))
	if err := sc.Err(); err != nil {
		o.kind = classify(err)
	}
	return o // stream ended without "done": invalid unless the read itself failed
}

func validRoute(r string) bool { return r == "sql" || r == "search" || r == "none" }

// ---------------------------------------------------------------- one step

type pct struct {
	P50 float64 `json:"p50"`
	P95 float64 `json:"p95"`
	P99 float64 `json:"p99"`
	Max float64 `json:"max"`
}

func percentiles(xs []float64) *pct {
	if len(xs) == 0 {
		return nil
	}
	sort.Float64s(xs)
	at := func(q float64) float64 { return xs[min(len(xs)-1, int(math.Ceil(q*float64(len(xs))))-1)] }
	return &pct{P50: at(0.50), P95: at(0.95), P99: at(0.99), Max: xs[len(xs)-1]}
}

type stepResult struct {
	Stage     string         `json:"stage"`
	Endpoint  string         `json:"endpoint"`
	RateRPS   float64        `json:"rate_rps"`
	PlannedS  float64        `json:"planned_s"`
	Planned   int            `json:"planned"`
	Sent      int            `json:"sent"`
	OK        int            `json:"ok"`
	Errors    map[string]int `json:"errors"`
	StageS    float64        `json:"stage_s"`    // incl. draining the last requests
	OKPerS    float64        `json:"ok_per_s"`   // ok / stage_s (goodput incl. draining the backlog)
	LatencyMS *pct           `json:"latency_ms"` // ok requests only, from the due time
	FirstMS   *pct           `json:"first_event_ms,omitempty"`
}

func stageName(endpoint string, rate float64) string {
	return endpoint + "_" + strings.ReplaceAll(strconv.FormatFloat(rate, 'f', -1, 64), ".", "p")
}

func (l *loader) step(ctx context.Context, endpoint string, rate float64, dur time.Duration, maxInFlight int) stepResult {
	fire := l.search
	if endpoint == "ask" {
		fire = l.ask
	}
	n := int(math.Round(rate * dur.Seconds()))
	res := make([]outcome, n)
	sem := make(chan struct{}, maxInFlight)
	var wg sync.WaitGroup
	t0 := time.Now()
	for i := range n {
		due := t0.Add(time.Duration(float64(i) / rate * float64(time.Second)))
		if d := time.Until(due); d > 0 {
			time.Sleep(d)
		}
		select {
		case sem <- struct{}{}:
		default:
			res[i] = outcome{kind: kindOverload}
			continue
		}
		wg.Go(func() {
			defer func() { <-sem }()
			res[i] = fire(ctx, i, due)
		})
	}
	wg.Wait()
	sr := stepResult{Stage: stageName(endpoint, rate), Endpoint: endpoint, RateRPS: rate, PlannedS: dur.Seconds(),
		Planned: n, Errors: map[string]int{}, StageS: time.Since(t0).Seconds()}
	var lat, first []float64
	for _, o := range res {
		if o.kind != kindOverload {
			sr.Sent++
		}
		if o.kind != kindOK {
			sr.Errors[o.kind]++
			continue
		}
		sr.OK++
		lat = append(lat, o.latMS)
		if endpoint == "ask" {
			first = append(first, o.firstM)
		}
	}
	sr.OKPerS = float64(sr.OK) / sr.StageS
	sr.LatencyMS, sr.FirstMS = percentiles(lat), percentiles(first)
	return sr
}

// ---------------------------------------------------------------- main

func parseRates(s string) ([]float64, error) {
	var out []float64
	for f := range strings.SplitSeq(s, ",") {
		if f = strings.TrimSpace(f); f == "" {
			continue
		}
		v, err := strconv.ParseFloat(f, 64)
		if err != nil || v <= 0 || v > 10000 {
			return nil, fmt.Errorf("rate %q: positive number ≤ 10000 expected", f)
		}
		out = append(out, v)
	}
	sort.Float64s(out)
	return out, nil
}

func run() error {
	var o options
	var search, ask string
	flag.StringVar(&o.input, "input", "", "raw CSV (cp1252), data/synth/stoermeldungen/<scale>/stoermeldungen.cp1252.csv")
	flag.StringVar(&o.output, "output", "", "out.json")
	flag.IntVar(&o.stack.Threads, "threads", 0, "parser threads (0 = all cores)")
	flag.IntVar(&o.stack.Port, "port", 8080, "gateway port on 127.0.0.1")
	flag.StringVar(&o.stack.Compose, "compose", "deploy/compose/compose.yaml", "compose file")
	flag.StringVar(&search, "search-rates", "5,10,20,40,80,160", "POST /v1/search rates (requests/s), one step each")
	flag.StringVar(&ask, "ask-rates", "0.5,1,2,4,8", "POST /v1/ask rates (requests/s), one step each")
	flag.DurationVar(&o.step, "step", 60*time.Second, "duration of one search step")
	flag.DurationVar(&o.askStep, "ask-step", 90*time.Second, "duration of one ask step")
	flag.DurationVar(&o.timeout, "timeout", 130*time.Second, "client timeout per request (gateway: 120 s)")
	flag.IntVar(&o.maxInFlight, "max-in-flight", 2048, "generator limit; beyond it requests count as overload")
	flag.BoolVar(&o.keep, "keep", false, "debug: leave the stack running")
	versions := flag.Bool("versions", false, "print the version fingerprint (Go, image IDs) as JSON and exit")
	flag.Parse()
	if *versions {
		return benchstack.PrintVersions(o.stack, "e07_stack_load")
	}
	if o.input == "" || o.output == "" {
		return errors.New("usage: zj-load --input CSV --output out.json [--search-rates …] [--ask-rates …]")
	}
	var err error
	if o.searchRates, err = parseRates(search); err != nil {
		return err
	}
	if o.askRates, err = parseRates(ask); err != nil {
		return err
	}
	if o.stack.Threads <= 0 {
		o.stack.Threads = runtime.NumCPU()
	}
	qs, err := benchstack.QuestionsFor(o.input)
	if err != nil {
		return err
	}
	if len(qs) == 0 {
		return errors.New("no questions")
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
	tr := &http.Transport{MaxIdleConns: 4096, MaxIdleConnsPerHost: 4096, IdleConnTimeout: 90 * time.Second}
	l := &loader{c: &benchstack.Client{Base: o.stack.Base(), Key: s.Key, HTTP: &http.Client{Transport: tr}}, qs: qs,
		timeout: o.timeout}
	ingestClient := &benchstack.Client{Base: l.c.Base, Key: l.c.Key, HTTP: &http.Client{Timeout: 10 * time.Minute}}

	if err := benchstack.Stage("load", func() error { return s.Up(ctx) }); err != nil {
		return err
	}
	var j benchstack.Job
	if err := benchstack.Stage("ingest", func() (err error) { j, err = ingestClient.Ingest(ctx, o.input); return err }); err != nil {
		return err
	}
	_ = benchstack.Stage("warm", func() error {
		for i := range 20 {
			_ = l.search(ctx, i, time.Now())
		}
		var wg sync.WaitGroup
		for i := range 8 {
			wg.Go(func() { _ = l.ask(ctx, i, time.Now()) })
		}
		wg.Wait()
		return nil
	})
	var steps []stepResult
	runSteps := func(endpoint string, rates []float64, dur time.Duration) {
		for _, r := range rates {
			var sr stepResult
			_ = benchstack.Stage(stageName(endpoint, r), func() error {
				sr = l.step(ctx, endpoint, r, dur, o.maxInFlight)
				return nil
			})
			fmt.Fprintf(os.Stderr, "zj-load: %s sent=%d ok=%d errors=%v p95=%v\n", sr.Stage, sr.Sent, sr.OK, sr.Errors, sr.LatencyMS)
			steps = append(steps, sr)
		}
	}
	runSteps("search", o.searchRates, o.step)
	runSteps("ask", o.askRates, o.askStep)

	out := map[string]any{"pipeline": "e07_stack_load", "rows": j.Rows, "timeout_s": o.timeout.Seconds(),
		"max_in_flight": o.maxInFlight, "steps": steps}
	if err := benchstack.Stage("write", func() error {
		b, _ := json.MarshalIndent(out, "", " ")
		return os.WriteFile(o.output, b, 0o644) //nolint:gosec // result file, read by the verifier
	}); err != nil {
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
		"engine":     "cuda (server)",
		"pipeline":   "e07_stack_load",
		"services":   []string{"PostgreSQL", "TEI", "vLLM", "zj-gateway", "zj-worker"},
		"images":     images,
		"load_steps": steps,
		"timeout_s":  o.timeout.Seconds(),
	}
	res, _ := json.Marshal(map[string]any{"rows": j.Rows, "notes": "e07_stack_load: open-loop load curve of the production stack", "extra": extra})
	fmt.Println("ZJRESULT " + string(res))
	return nil
}
