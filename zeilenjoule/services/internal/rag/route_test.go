package rag

import (
	"context"
	"encoding/json"
	"testing"

	"zeilenjoule/services/internal/llm"
	"zeilenjoule/services/internal/prompts"
)

// streamFake streams text one byte at a time and records how much was generated when the route fired.
type streamFake struct {
	text      string
	generated *int
}

func (f streamFake) JSON(context.Context, string, []prompts.Message, json.RawMessage, int) (llm.Result, error) {
	return llm.Result{Text: f.text}, nil
}

func (f streamFake) JSONStream(_ context.Context, _ string, _ []prompts.Message, _ json.RawMessage, _ int,
	onText func(string),
) (llm.Result, error) {
	for i := 1; i <= len(f.text); i++ {
		*f.generated = i
		onText(f.text[:i])
	}
	return llm.Result{Text: f.text}, nil
}

func TestRouteFiresBeforeRouterFinishes(t *testing.T) {
	p, err := prompts.Load()
	if err != nil {
		t.Fatal(err)
	}
	cases := map[string]string{
		`{"route": "search", "sql": null, "suchtext": "Lager läuft heiß"}`: "search",
		`{ "route":"sql", "sql": "SELECT 1"}`:                              "sql",
		`{"route": "searchx"}`:                                             "invalid",
		`not json`:                                                         "invalid",
	}
	for text, want := range cases {
		var generated, firedAt int
		var got []string
		e := &Engine{P: p, LLM: streamFake{text: text, generated: &generated}}
		o, err := e.Route(context.Background(), "Frage", func(r string) { got, firedAt = append(got, r), generated })
		if err != nil {
			t.Fatal(err)
		}
		if len(got) != 1 || got[0] != want || o.Route != want {
			t.Fatalf("%q: route events %v, routed %q, want %q", text, got, o.Route, want)
		}
		if want != "invalid" && firedAt >= len(text) {
			t.Fatalf("%q: route fired only after the whole JSON (%d/%d bytes)", text, firedAt, len(text))
		}
	}
}
