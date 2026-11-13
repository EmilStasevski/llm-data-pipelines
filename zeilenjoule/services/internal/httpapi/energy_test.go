package httpapi

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"zeilenjoule/services/internal/api"
	"zeilenjoule/services/internal/rag"
)

func TestEnergy(t *testing.T) {
	prom := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Query().Get("query") {
		case "zj:joules_per_ask:1m": // no question in the last minute: empty vector
			_, _ = fmt.Fprint(w, `{"status":"success","data":{"resultType":"vector","result":[]}}`)
		case "zj:energy_watts:rate1m":
			_, _ = fmt.Fprint(w, `{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1,"212.5"]}]}}`)
		default:
			http.NotFound(w, r)
		}
	}))
	defer prom.Close()
	got, err := (&Server{Prometheus: prom.URL}).Energy(context.Background(), api.EnergyRequestObject{})
	e, ok := got.(api.Energy200JSONResponse)
	if err != nil || !ok || e.JoulesPerAsk != nil || e.Watts == nil || *e.Watts != 212.5 || e.Source != "prometheus" {
		t.Fatalf("%+v %v", got, err)
	}
	none, _ := (&Server{}).Energy(context.Background(), api.EnergyRequestObject{})
	if n := none.(api.Energy200JSONResponse); n.JoulesPerAsk != nil || !strings.HasPrefix(n.Source, "not configured") {
		t.Fatalf("unconfigured: %+v", n)
	}
	prom.Close()
	down, _ := (&Server{Prometheus: prom.URL}).Energy(context.Background(), api.EnergyRequestObject{})
	if d := down.(api.Energy200JSONResponse); d.Watts != nil || d.Source != "prometheus unreachable" {
		t.Fatalf("down: %+v", d)
	}
}

func TestCitedRows(t *testing.T) {
	d := time.Date(2025, 3, 1, 0, 0, 0, 0, time.UTC)
	out := toResponse(rag.Answer{Route: "search", CitedIDs: []string{"1"},
		Hits: []rag.Hit{{MeldungID: "1", Score: 0.5, Werk: "Köln", Datum: &d, Meldungstext: "Leck"}}})
	if out.CitedRows == nil || len(*out.CitedRows) != 1 || (*out.CitedRows)[0].Datum.String() != "2025-03-01" {
		t.Fatalf("%+v", out.CitedRows)
	}
	if toResponse(rag.Answer{Route: "sql"}).CitedRows != nil {
		t.Fatal("sql answers carry no cited_rows")
	}
}

func TestDemoPage(t *testing.T) {
	w := httptest.NewRecorder()
	serveDemo(w, httptest.NewRequestWithContext(context.Background(), http.MethodGet, "/demo", nil))
	b := w.Body.String()
	if !strings.Contains(w.Header().Get("Content-Type"), "text/html") || !strings.Contains(b, "/v1/ask") ||
		!strings.Contains(b, "/v1/energy") || strings.Contains(b, "<script src=") {
		t.Fatal("demo page: wrong type, missing endpoints or external script")
	}
}
