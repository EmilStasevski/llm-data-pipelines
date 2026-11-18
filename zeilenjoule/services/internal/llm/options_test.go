package llm

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// The default request keeps the measured vLLM field; Extra replaces it and never overrides the client's own fields.
func TestExtraFieldsAndAPIKey(t *testing.T) {
	for _, tc := range []struct {
		name  string
		opt   Options
		check func(map[string]any) bool
	}{
		{"default", Options{}, func(m map[string]any) bool {
			k, ok := m["chat_template_kwargs"].(map[string]any)
			return ok && k["enable_thinking"] == false
		}},
		{"ollama", Options{APIKey: "s3cret", Extra: map[string]any{"reasoning_effort": "none", "temperature": 0.7}},
			func(m map[string]any) bool {
				_, tpl := m["chat_template_kwargs"]
				return m["reasoning_effort"] == "none" && m["temperature"] == 0.0 && !tpl
			}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var m map[string]any
				if err := json.NewDecoder(r.Body).Decode(&m); err != nil {
					t.Error(err)
				}
				if !tc.check(m) {
					t.Errorf("request fields: %v", m)
				}
				if want := map[bool]string{true: "Bearer s3cret", false: ""}[tc.opt.APIKey != ""]; r.Header.Get("Authorization") != want {
					t.Errorf("Authorization %q, want %q", r.Header.Get("Authorization"), want)
				}
				_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}`))
			}))
			defer srv.Close()
			if _, err := NewWith(srv.URL, "m", 1, tc.opt).JSON(context.Background(), "t", nil, json.RawMessage(`{}`), 10); err != nil {
				t.Fatal(err)
			}
		})
	}
}

// Ollama has no /health: readiness falls back to /v1/models.
func TestReadyFallsBackToModels(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/models" {
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()
	if err := New(srv.URL, "m", 1).Ready(context.Background()); err != nil {
		t.Fatal(err)
	}
	down := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer down.Close()
	if err := New(down.URL, "m", 1).Ready(context.Background()); err == nil {
		t.Fatal("503 counted as ready")
	}
}
