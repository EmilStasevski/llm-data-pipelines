package httpapi

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"zeilenjoule/services/internal/api"
	"zeilenjoule/services/internal/rag"
)

func testHandler(rate float64, burst int) http.Handler {
	ok := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, has := r.Context().Deadline(); !has && r.URL.Path != "/v1/ingest" {
			http.Error(w, "no deadline", http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	return Chain(ok, Options{APIKeys: []string{"k1", "k2"}, RateLimit: rate, RateBurst: burst, JSONLimit: 1 << 10,
		Timeout: time.Second, Log: slog.New(slog.DiscardHandler)})
}

func do(h http.Handler, path, key string) *httptest.ResponseRecorder {
	r := httptest.NewRequestWithContext(context.Background(), http.MethodPost, path, strings.NewReader("{}"))
	if key != "" {
		r.Header.Set("X-API-Key", key)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func TestAuth(t *testing.T) {
	h := testHandler(100, 100)
	for _, c := range []struct {
		path, key string
		want      int
	}{
		{"/healthz", "", 200}, {"/readyz", "", 200}, {"/v1/openapi.json", "", 200},
		{"/v1/ask", "", 401}, {"/v1/ask", "wrong", 401}, {"/v1/ask", "k1", 200}, {"/v1/search", "k2", 200},
	} {
		if got := do(h, c.path, c.key); got.Code != c.want {
			t.Errorf("%s key=%q: %d, want %d", c.path, c.key, got.Code, c.want)
		}
	}
	w := do(h, "/v1/ask", "")
	var p map[string]any
	if w.Header().Get("Content-Type") != "application/problem+json" || json.Unmarshal(w.Body.Bytes(), &p) != nil || p["status"] != 401.0 {
		t.Errorf("401 is not a problem+json body: %q %s", w.Header().Get("Content-Type"), w.Body)
	}
}

func TestRateLimitPerKey(t *testing.T) {
	h := testHandler(0.001, 2)
	codes := []int{do(h, "/v1/ask", "k1").Code, do(h, "/v1/ask", "k1").Code, do(h, "/v1/ask", "k1").Code}
	if codes[0] != 200 || codes[1] != 200 || codes[2] != 429 {
		t.Fatalf("k1 codes %v, want [200 200 429]", codes)
	}
	if c := do(h, "/v1/ask", "k2").Code; c != 200 {
		t.Fatalf("k2 shares k1's bucket: %d", c)
	}
}

func TestParseExtract(t *testing.T) {
	full := `{"komponente": "Getriebe", "schadensart": "Überhitzung", "ursache": null, "massnahme": null,
		"sicherheitsrelevant": false, "stillstand_minuten": 30, "ersatzteil_materialnr": null}`
	if r := parseExtract(full); !r.Valid || (*r.Fields)["komponente"] != "Getriebe" {
		t.Fatalf("valid row rejected: %+v", r)
	}
	for _, bad := range []string{"", "[]", `{"komponente": "x"}`, strings.Replace(full, "ursache", "grund", 1)} {
		if parseExtract(bad).Valid {
			t.Errorf("accepted %q", bad)
		}
	}
}

func TestOpLabelsBounded(t *testing.T) {
	r := httptest.NewRequestWithContext(context.Background(), http.MethodGet, "/v1/jobs/12345", nil)
	if op(r) != "get_job" {
		t.Fatal(op(r))
	}
	r = httptest.NewRequestWithContext(context.Background(), http.MethodGet, "/random/123", nil)
	if op(r) != "other" {
		t.Fatal(op(r))
	}
}

func TestToResponse(t *testing.T) {
	r := toResponse(rag.Answer{Route: "invalid"})
	if r.Route != api.None || r.SqlStatus != nil || r.CitedIds == nil {
		t.Fatalf("invalid route: %+v", r)
	}
	r = toResponse(rag.Answer{Route: "sql", SQLStatus: "rejected", Rejected: "not a SELECT"})
	if r.Route != api.Sql || r.SqlStatus == nil || *r.SqlStatus != api.AskResponseSqlStatusRejected || r.Sql != nil {
		t.Fatalf("rejected sql: %+v", r)
	}
	r = toResponse(rag.Answer{Route: "sql", SQLStatus: "executed", SQL: "SELECT 1", Value: [][]any{{1}}})
	if r.Sql == nil || *r.Sql != "SELECT 1" || *r.SqlStatus != api.AskResponseSqlStatusExecuted {
		t.Fatalf("executed sql: %+v", r)
	}
}
