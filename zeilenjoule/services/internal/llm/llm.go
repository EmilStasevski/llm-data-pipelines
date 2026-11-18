// Package llm is a small client for an OpenAI-compatible chat endpoint (vLLM in the measured stack; Ollama,
// llama.cpp server … in the winner kit, ADR 0028) with JSON-schema constrained output.
//
// Settings match the measured Track D/E runs: temperature 0, seed 0, Qwen3.5 thinking off
// (chat_template_kwargs.enable_thinking=false, the default Extra), response_format json_schema strict.
package llm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"zeilenjoule/services/internal/metrics"
	"zeilenjoule/services/internal/prompts"
)

// Client talks to one vLLM server; the semaphore keeps in-flight requests ≤ --max-num-seqs.
type Client struct {
	base, model string
	apiKey      string
	extra       map[string]any
	http        *http.Client
	sem         chan struct{}
	retries     int
}

// DefaultExtra switches Qwen3.5's thinking off on vLLM (the measured setting). Other servers need other fields,
// e.g. Ollama {"reasoning_effort": "none"} (ADR 0028).
func DefaultExtra() map[string]any {
	return map[string]any{"chat_template_kwargs": map[string]any{"enable_thinking": false}}
}

// Options are server-specific settings; the zero value is the measured vLLM setup.
type Options struct {
	APIKey string         // sent as "Authorization: Bearer …" when set
	Extra  map[string]any // top-level request fields added to every request; nil means DefaultExtra
}

// New creates a client for the measured vLLM setup; inFlight bounds concurrent requests across all callers.
func New(base, model string, inFlight int) *Client { return NewWith(base, model, inFlight, Options{}) }

// NewWith creates a client with server-specific options.
func NewWith(base, model string, inFlight int, o Options) *Client {
	extra := o.Extra
	if extra == nil {
		extra = DefaultExtra()
	}
	return &Client{base: base, model: model, apiKey: o.APIKey, extra: extra,
		http: &http.Client{Timeout: 120 * time.Second}, sem: make(chan struct{}, inFlight), retries: 3}
}

// Result is the constrained JSON text and token usage of one completion.
type Result struct {
	Text             string
	PromptTokens     int
	CompletionTokens int
	Finish           string
}

// chatRequest documents the request fields this client sets itself; Extra adds server-specific ones.
type chatRequest struct {
	Model          string            `json:"model"`
	Messages       []prompts.Message `json:"messages"`
	Temperature    float64           `json:"temperature"`
	Seed           int               `json:"seed"`
	MaxTokens      int               `json:"max_tokens"`
	ResponseFormat map[string]any    `json:"response_format,omitempty"`
	Stream         bool              `json:"stream,omitempty"`
	StreamOptions  map[string]any    `json:"stream_options,omitempty"`
}

type chatResponse struct {
	Choices []struct {
		Message      struct{ Content string } `json:"message"`
		FinishReason string                   `json:"finish_reason"`
	} `json:"choices"`
	Usage struct {
		PromptTokens     int `json:"prompt_tokens"`
		CompletionTokens int `json:"completion_tokens"`
	} `json:"usage"`
}

// StatusError is a non-2xx answer from the server.
type StatusError struct {
	Code int
	Body string
}

func (e *StatusError) Error() string { return fmt.Sprintf("llm: HTTP %d: %s", e.Code, e.Body) }

func retryable(err error) bool {
	var se *StatusError
	if errors.As(err, &se) {
		return se.Code == http.StatusTooManyRequests || se.Code >= 500
	}
	return !errors.Is(err, context.Canceled) && !errors.Is(err, context.DeadlineExceeded)
}

// JSON runs one chat completion constrained to schema. op labels the metrics (router, answer, label, extract).
func (c *Client) JSON(ctx context.Context, op string, msgs []prompts.Message, schema json.RawMessage, maxTokens int) (Result, error) {
	return c.run(ctx, op, msgs, schema, maxTokens, nil)
}

// JSONStream is JSON with a streamed completion: onText sees the generated text so far after every chunk (same
// request settings, so the same tokens as JSON). The router uses it to report its route before the rest of the
// JSON is generated (ADR 0027). onText may see a prefix again when a failed attempt is retried.
func (c *Client) JSONStream(ctx context.Context, op string, msgs []prompts.Message, schema json.RawMessage, maxTokens int,
	onText func(string),
) (Result, error) {
	return c.run(ctx, op, msgs, schema, maxTokens, onText)
}

func (c *Client) run(ctx context.Context, op string, msgs []prompts.Message, schema json.RawMessage, maxTokens int,
	onText func(string),
) (Result, error) {
	cr := chatRequest{
		Model: c.model, Messages: msgs, Temperature: 0, Seed: 0, MaxTokens: maxTokens,
		ResponseFormat: map[string]any{"type": "json_schema",
			"json_schema": map[string]any{"name": op, "schema": schema, "strict": true}},
	}
	if onText != nil {
		cr.Stream, cr.StreamOptions = true, map[string]any{"include_usage": true}
	}
	body, err := c.encode(cr)
	if err != nil {
		return Result{}, err
	}
	select {
	case c.sem <- struct{}{}:
	case <-ctx.Done():
		return Result{}, ctx.Err()
	}
	defer func() { <-c.sem }()
	var res Result
	for attempt := 0; ; attempt++ {
		t0 := time.Now()
		res, err = c.once(ctx, body, onText)
		outcome := "ok"
		if err != nil {
			outcome = "error"
		}
		metrics.Upstream.WithLabelValues("llm", op, outcome).Observe(time.Since(t0).Seconds())
		if err == nil || attempt >= c.retries || !retryable(err) {
			break
		}
		select {
		case <-time.After(time.Duration(200<<attempt) * time.Millisecond):
		case <-ctx.Done():
			return Result{}, ctx.Err()
		}
	}
	if err != nil {
		return Result{}, err
	}
	metrics.LLMTokens.WithLabelValues(op, "prompt").Add(float64(res.PromptTokens))
	metrics.LLMTokens.WithLabelValues(op, "completion").Add(float64(res.CompletionTokens))
	return res, nil
}

// encode merges the server-specific Extra fields into the request; the client's own fields win.
func (c *Client) encode(cr chatRequest) ([]byte, error) {
	own, err := json.Marshal(cr)
	if err != nil {
		return nil, err
	}
	m := map[string]any{}
	for k, v := range c.extra {
		m[k] = v
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(own, &fields); err != nil {
		return nil, err
	}
	for k, v := range fields {
		m[k] = v
	}
	return json.Marshal(m)
}

func (c *Client) once(ctx context.Context, body []byte, onText func(string)) (Result, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		return Result{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return Result{}, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode/100 == 2 && onText != nil {
		return readStream(resp.Body, onText)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return Result{}, err
	}
	if resp.StatusCode/100 != 2 {
		return Result{}, &StatusError{Code: resp.StatusCode, Body: string(truncate(raw, 300))}
	}
	var cr chatResponse
	if err := json.Unmarshal(raw, &cr); err != nil {
		return Result{}, fmt.Errorf("llm: decode: %w", err)
	}
	if len(cr.Choices) == 0 {
		return Result{}, errors.New("llm: no choices")
	}
	return Result{Text: cr.Choices[0].Message.Content, PromptTokens: cr.Usage.PromptTokens,
		CompletionTokens: cr.Usage.CompletionTokens, Finish: cr.Choices[0].FinishReason}, nil
}

type streamChunk struct {
	Choices []struct {
		Delta        struct{ Content string } `json:"delta"`
		FinishReason *string                  `json:"finish_reason"`
	} `json:"choices"`
	Usage *struct {
		PromptTokens     int `json:"prompt_tokens"`
		CompletionTokens int `json:"completion_tokens"`
	} `json:"usage"`
}

// readStream reads an OpenAI-style SSE completion ("data: {chunk}" lines, "data: [DONE]").
func readStream(r io.Reader, onText func(string)) (Result, error) {
	var res Result
	var text strings.Builder
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64<<10), 4<<20)
	for sc.Scan() {
		data, ok := strings.CutPrefix(sc.Text(), "data: ")
		if !ok {
			continue
		}
		if data == "[DONE]" {
			res.Text = text.String()
			return res, nil
		}
		var ch streamChunk
		if err := json.Unmarshal([]byte(data), &ch); err != nil {
			return Result{}, fmt.Errorf("llm: decode chunk: %w", err)
		}
		if ch.Usage != nil {
			res.PromptTokens, res.CompletionTokens = ch.Usage.PromptTokens, ch.Usage.CompletionTokens
		}
		for _, c := range ch.Choices {
			if c.FinishReason != nil {
				res.Finish = *c.FinishReason
			}
			if c.Delta.Content != "" {
				text.WriteString(c.Delta.Content)
				onText(text.String())
			}
		}
	}
	if err := sc.Err(); err != nil {
		return Result{}, err
	}
	return Result{}, errors.New("llm: stream ended without [DONE]")
}

// Ready checks GET /health.
func (c *Client) Ready(ctx context.Context) error { return ReadyCheck(ctx, c.http, c.base, c.apiKey) }

// ReadyCheck is GET /health (vLLM, TEI, llama.cpp); servers without it (Ollama: 404) are checked with
// GET /v1/models, which every OpenAI-compatible server has.
func ReadyCheck(ctx context.Context, h *http.Client, base, apiKey string) error {
	err := getOK(ctx, h, base+"/health", apiKey)
	var nf notFound
	if errors.As(err, &nf) {
		return getOK(ctx, h, base+"/v1/models", apiKey)
	}
	return err
}

type notFound struct{ url string }

func (e notFound) Error() string { return e.url + ": HTTP 404" }

func getOK(ctx context.Context, h *http.Client, url, apiKey string) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	resp, err := h.Do(req)
	if err != nil {
		return err
	}
	_ = resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return notFound{url}
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: HTTP %d", url, resp.StatusCode)
	}
	return nil
}

func truncate(b []byte, n int) []byte {
	if len(b) > n {
		return b[:n]
	}
	return b
}
