package embed

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// fakeOpenAI answers /v1/embeddings with unnormalised 3-dim vectors in reverse index order.
func fakeOpenAI(t *testing.T, dim int, seen *[]string) *httptest.Server {
	t.Helper()
	var mu sync.Mutex // the client sends batches concurrently
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/embeddings" || r.Header.Get("Authorization") != "Bearer k" {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		var req struct {
			Model string   `json:"model"`
			Input []string `json:"input"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Model != "bge-m3" {
			http.Error(w, "bad body", http.StatusBadRequest)
			return
		}
		mu.Lock()
		*seen = append(*seen, req.Input...)
		mu.Unlock()
		type item struct {
			Index     int       `json:"index"`
			Embedding []float32 `json:"embedding"`
		}
		var out []item
		for i := len(req.Input) - 1; i >= 0; i-- {
			v := make([]float32, dim)
			v[0], v[1] = 3, float32(4*(i+1))
			out = append(out, item{i, v})
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"data": out})
	}))
}

func TestOpenAIEmbeddings(t *testing.T) {
	var seen []string
	srv := fakeOpenAI(t, 3, &seen)
	defer srv.Close()
	c := NewWith(srv.URL, 2, 2, Options{API: APIOpenAI, Model: "bge-m3", Dim: 3, APIKey: "k",
		QueryPrefix: "query: ", DocPrefix: "passage: "})
	v, err := c.Embed(context.Background(), "query", []string{"a", "b", "c"})
	if err != nil {
		t.Fatal(err)
	}
	for i, x := range v { // order restored from "index", unit length
		want := float32(4*((i%2)+1)) / float32(math.Hypot(3, float64(4*((i%2)+1))))
		if math.Abs(float64(x[1]-want)) > 1e-6 {
			t.Errorf("vector %d = %v, want y %v", i, x, want)
		}
	}
	if _, err := c.Embed(context.Background(), "ingest", []string{"d"}); err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(seen, "|"); !strings.Contains(got, "query: a") || !strings.Contains(got, "passage: d") {
		t.Errorf("prefixes not applied: %s", got)
	}
}

func TestOpenAIDimensionMismatch(t *testing.T) {
	var seen []string
	srv := fakeOpenAI(t, 3, &seen)
	defer srv.Close()
	c := NewWith(srv.URL, 1, 8, Options{API: APIOpenAI, Model: "bge-m3", APIKey: "k"}) // default 1024
	if _, err := c.Embed(context.Background(), "query", []string{"a"}); err == nil || !strings.Contains(err.Error(), "ZJ_EMBED_DIM") {
		t.Fatalf("want dimension error, got %v", err)
	}
}
