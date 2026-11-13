package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// fakeVLLM streams body in 3-character chunks (stream=true) or answers it at once.
func fakeVLLM(t *testing.T, body string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req chatRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
			return
		}
		if !req.Stream {
			_, _ = fmt.Fprintf(w, `{"choices":[{"message":{"content":%q},"finish_reason":"stop"}],"usage":{"prompt_tokens":7,"completion_tokens":5}}`, body)
			return
		}
		if req.StreamOptions["include_usage"] != true {
			t.Error("stream without include_usage")
		}
		w.Header().Set("Content-Type", "text/event-stream")
		for i := 0; i < len(body); i += 3 {
			_, _ = fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":%q},\"finish_reason\":null}]}\n\n", body[i:min(i+3, len(body))])
		}
		_, _ = fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n")
		_, _ = fmt.Fprint(w, "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":7,\"completion_tokens\":5}}\n\ndata: [DONE]\n\n")
	}))
}

func TestJSONStreamSameResultAsJSON(t *testing.T) {
	const body = `{"route": "search", "suchtext": "Lager"}`
	srv := fakeVLLM(t, body)
	defer srv.Close()
	c := New(srv.URL, "m", 2)
	plain, err := c.JSON(context.Background(), "router", nil, json.RawMessage(`{}`), 50)
	if err != nil {
		t.Fatal(err)
	}
	var prefixes []string
	streamed, err := c.JSONStream(context.Background(), "router", nil, json.RawMessage(`{}`), 50,
		func(p string) { prefixes = append(prefixes, p) })
	if err != nil {
		t.Fatal(err)
	}
	if streamed != plain || streamed.Text != body || streamed.Finish != "stop" || streamed.CompletionTokens != 5 {
		t.Fatalf("streamed %+v, plain %+v", streamed, plain)
	}
	for i, p := range prefixes {
		if !strings.HasPrefix(body, p) || (i > 0 && len(p) <= len(prefixes[i-1])) {
			t.Fatalf("prefix %d %q", i, p)
		}
	}
}

func TestStreamWithoutDoneFails(t *testing.T) {
	_, err := readStream(strings.NewReader("data: {\"choices\":[{\"delta\":{\"content\":\"{\"}}]}\n\n"), func(string) {})
	if err == nil {
		t.Fatal("truncated stream accepted")
	}
}
