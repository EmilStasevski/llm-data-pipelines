package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"zeilenjoule/services/internal/benchstack"
)

func TestStageNameMatchesZJMARK(t *testing.T) {
	for rate, want := range map[float64]string{20: "search_20", 0.5: "search_0p5", 160: "search_160"} {
		if got := stageName("search", rate); got != want {
			t.Errorf("stageName(%v) = %q, want %q", rate, got, want)
		}
	}
}

func TestParseRates(t *testing.T) {
	got, err := parseRates("4, 0.5,1")
	if err != nil || fmt.Sprint(got) != "[0.5 1 4]" {
		t.Fatalf("parseRates = %v, %v", got, err)
	}
	for _, bad := range []string{"0", "-1", "x", "20000"} {
		if _, err := parseRates(bad); err == nil {
			t.Errorf("parseRates(%q) accepted", bad)
		}
	}
}

func TestPercentiles(t *testing.T) {
	xs := make([]float64, 100)
	for i := range xs {
		xs[i] = float64(100 - i) // 100 … 1, unsorted
	}
	p := percentiles(xs)
	if p.P50 != 50 || p.P95 != 95 || p.P99 != 99 || p.Max != 100 {
		t.Fatalf("percentiles = %+v", p)
	}
	if percentiles(nil) != nil {
		t.Fatal("empty input must give nil")
	}
}

func TestReadSSE(t *testing.T) {
	due := time.Now()
	cases := map[string]string{
		"event: route\ndata: {\"route\":\"sql\"}\n\nevent: answer\ndata: {\"route\":\"sql\",\"answer\":3}\n\nevent: done\ndata: {}\n\n": kindOK,
		"event: route\ndata: {\"route\":\"none\"}\n\nevent: answer\ndata: {\"route\":\"none\"}\n\nevent: done\ndata: {}\n\n":            kindOK,
		"event: route\ndata: {\"route\":\"sql\"}\n\nevent: error\ndata: {\"status\":502}\n\n":                                           kindUpstream,
		"event: route\ndata: {\"route\":\"sql\"}\n\n":                                                                                   kindInvalid, // no answer
		"event: route\ndata: {\"route\":\"magic\"}\n\nevent: answer\ndata: {\"route\":\"magic\"}\n\nevent: done\ndata: {}\n\n":          kindInvalid,
	}
	for in, want := range cases {
		o := readSSE(strings.NewReader(in), due)
		if o.kind != want {
			t.Errorf("readSSE(%q) = %s, want %s", in, o.kind, want)
		}
		if o.firstM <= 0 || o.latMS < o.firstM {
			t.Errorf("readSSE(%q): first %v, total %v", in, o.firstM, o.latMS)
		}
	}
}

// fakeGateway answers /v1/search after `delay`, and /v1/ask as an SSE stream; key "k" only.
func fakeGateway(delay time.Duration, hits int, inFlight *atomic.Int64) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-API-Key") != "k" {
			http.Error(w, "no key", http.StatusUnauthorized)
			return
		}
		inFlight.Add(1)
		defer inFlight.Add(-1)
		time.Sleep(delay)
		switch r.URL.Path {
		case "/v1/search":
			w.Header().Set("Content-Type", "application/json")
			parts := make([]string, hits)
			for i := range parts {
				parts[i] = fmt.Sprintf(`{"meldung_id":"%d","score":0.1}`, i+1)
			}
			_, _ = fmt.Fprintf(w, `{"hits":[%s]}`, strings.Join(parts, ","))
		case "/v1/ask":
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = fmt.Fprint(w, "event: route\ndata: {\"route\":\"search\"}\n\n")
			w.(http.Flusher).Flush()
			time.Sleep(delay)
			_, _ = fmt.Fprint(w, "event: answer\ndata: {\"route\":\"search\",\"answer\":\"x\"}\n\nevent: done\ndata: {}\n\n")
		default:
			http.NotFound(w, r)
		}
	}))
}

func newLoader(base, key string) *loader {
	return &loader{c: &benchstack.Client{Base: base, Key: key, HTTP: &http.Client{}},
		qs: []benchstack.Question{{QID: "e1", Text: "Frage"}}, timeout: 5 * time.Second}
}

func TestStepOpenLoop(t *testing.T) {
	var inFlight atomic.Int64
	srv := fakeGateway(200*time.Millisecond, 10, &inFlight)
	defer srv.Close()
	l := newLoader(srv.URL, "k")
	// 50 requests/s for 1 s with a 200 ms server: a closed loop with one client would send 5; open loop sends 50
	sr := l.step(context.Background(), "search", 50, time.Second, 1000)
	if sr.Planned != 50 || sr.Sent != 50 || sr.OK != 50 || len(sr.Errors) != 0 {
		t.Fatalf("step = %+v", sr)
	}
	if sr.LatencyMS.P50 < 200 || sr.LatencyMS.P50 > 1500 {
		t.Fatalf("p50 %v ms, want ≈ 200 ms", sr.LatencyMS.P50)
	}
	if sr.Stage != "search_50" || sr.OKPerS <= 25 || sr.OKPerS > 50 {
		t.Fatalf("stage %q ok/s %v", sr.Stage, sr.OKPerS)
	}
	ask := l.step(context.Background(), "ask", 10, time.Second, 1000)
	if ask.OK != 10 || ask.FirstMS == nil || ask.FirstMS.P50 >= ask.LatencyMS.P50 {
		t.Fatalf("ask step = %+v first=%+v lat=%+v", ask, ask.FirstMS, ask.LatencyMS)
	}
}

func TestStepClassifiesErrorsAndOverload(t *testing.T) {
	var inFlight atomic.Int64
	srv := fakeGateway(300*time.Millisecond, 0, &inFlight) // 0 hits = wrong shape
	defer srv.Close()
	sr := newLoader(srv.URL, "k").step(context.Background(), "search", 20, 500*time.Millisecond, 3)
	if sr.Planned != 10 || sr.OK != 0 {
		t.Fatalf("step = %+v", sr)
	}
	if sr.Errors[kindInvalid] == 0 || sr.Errors[kindOverload] == 0 || sr.Sent+sr.Errors[kindOverload] != sr.Planned {
		t.Fatalf("errors = %v sent = %d", sr.Errors, sr.Sent)
	}
	bad := newLoader(srv.URL, "wrong").step(context.Background(), "search", 10, 200*time.Millisecond, 10)
	if bad.Errors[kind4xx] != bad.Planned {
		t.Fatalf("401 must count as http_4xx: %v", bad.Errors)
	}
	l := newLoader(srv.URL, "k")
	l.timeout = 50 * time.Millisecond
	if to := l.step(context.Background(), "search", 10, 200*time.Millisecond, 10); to.Errors[kindTimeout] != to.Planned {
		t.Fatalf("timeouts: %v", to.Errors)
	}
}
