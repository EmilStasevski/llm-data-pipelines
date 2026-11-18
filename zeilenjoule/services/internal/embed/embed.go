// Package embed is a client for Hugging Face Text Embeddings Inference (bge-m3, 1 024 dimensions; the measured
// stack) or any OpenAI-compatible /v1/embeddings server (Ollama, llama.cpp, vLLM …; winner kit, ADR 0028).
//
// TEI rejects more than ≈ 512 concurrent inputs with HTTP 429 when it shares the GPU with vLLM (ADR 0021
// addendum), so all callers share one semaphore: at most inFlight requests of ≤ batch texts each.
package embed

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"time"

	"golang.org/x/sync/errgroup"

	"zeilenjoule/services/internal/llm"
	"zeilenjoule/services/internal/metrics"
)

// Dim is the bge-m3 dense vector size (the default).
const Dim = 1024

// APIs a client can speak.
const (
	APITEI    = "tei"    // POST /embed, server-side truncation + normalisation (measured)
	APIOpenAI = "openai" // POST /v1/embeddings, normalised here
)

// Options select the server API and model; the zero value is the measured TEI + bge-m3 setup.
type Options struct {
	API         string // APITEI (default) or APIOpenAI
	Model       string // model name for APIOpenAI
	Dim         int    // vector size; 0 means Dim
	APIKey      string // sent as "Authorization: Bearer …" when set
	QueryPrefix string // prepended to search queries (e.g. "query: " for e5 models)
	DocPrefix   string // prepended to documents (e.g. "passage: ")
}

// Client embeds texts; every vector is L2-normalised (inner product = cosine, as b06_tei in Track B).
type Client struct {
	base    string
	o       Options
	http    *http.Client
	sem     chan struct{}
	batch   int
	retries int
}

// New creates a TEI client for bge-m3.
func New(base string, inFlight, batch int) *Client { return NewWith(base, inFlight, batch, Options{}) }

// NewWith creates a client for another server API or model.
func NewWith(base string, inFlight, batch int, o Options) *Client {
	if o.API == "" {
		o.API = APITEI
	}
	if o.Dim == 0 {
		o.Dim = Dim
	}
	return &Client{base: base, o: o, http: &http.Client{Timeout: 120 * time.Second},
		sem: make(chan struct{}, inFlight), batch: batch, retries: 5}
}

// Dim is the configured vector size.
func (c *Client) Dim() int { return c.o.Dim }

// Embed returns one vector per text, in order. op labels the metrics (query, ingest, label).
func (c *Client) Embed(ctx context.Context, op string, texts []string) ([][]float32, error) {
	if pre := c.prefix(op); pre != "" {
		p := make([]string, len(texts))
		for i, s := range texts {
			p[i] = pre + s
		}
		texts = p
	}
	out := make([][]float32, len(texts))
	g, ctx := errgroup.WithContext(ctx)
	for s := 0; s < len(texts); s += c.batch {
		e := min(s+c.batch, len(texts))
		g.Go(func() error {
			select {
			case c.sem <- struct{}{}:
			case <-ctx.Done():
				return ctx.Err()
			}
			defer func() { <-c.sem }()
			v, err := c.withRetry(ctx, op, texts[s:e])
			if err != nil {
				return err
			}
			copy(out[s:e], v)
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return nil, err
	}
	metrics.EmbeddedTexts.WithLabelValues(op).Add(float64(len(texts)))
	return out, nil
}

// prefix: op "query" is a search query, everything else (ingest, label) embeds documents.
func (c *Client) prefix(op string) string {
	if op == "query" {
		return c.o.QueryPrefix
	}
	return c.o.DocPrefix
}

type statusError struct{ code int }

// dimError is permanent: a retry returns the same size.
type dimError struct{ got, want int }

func (e dimError) Error() string {
	return fmt.Sprintf("embed: model returns %d dimensions, ZJ_EMBED_DIM is %d", e.got, e.want)
}

func (e statusError) Error() string { return fmt.Sprintf("tei: HTTP %d", e.code) }

func (c *Client) withRetry(ctx context.Context, op string, texts []string) ([][]float32, error) {
	req := map[string]any{"inputs": texts, "truncate": true, "normalize": true}
	if c.o.API == APIOpenAI {
		req = map[string]any{"model": c.o.Model, "input": texts, "encoding_format": "float"}
	}
	body, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	for attempt := 0; ; attempt++ {
		t0 := time.Now()
		v, err := c.once(ctx, body, len(texts))
		outcome := "ok"
		if err != nil {
			outcome = "error"
		}
		metrics.Upstream.WithLabelValues("tei", op, outcome).Observe(time.Since(t0).Seconds())
		var se statusError
		var de dimError
		retry := err != nil && !errors.As(err, &de) &&
			(!errors.As(err, &se) || se.code == http.StatusTooManyRequests || se.code >= 500) && ctx.Err() == nil
		if !retry || attempt >= c.retries {
			return v, err
		}
		select {
		case <-time.After(time.Duration(100<<attempt) * time.Millisecond):
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
}

func (c *Client) once(ctx context.Context, body []byte, n int) ([][]float32, error) {
	path := "/embed"
	if c.o.API == APIOpenAI {
		path = "/v1/embeddings"
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.o.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.o.APIKey)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil, statusError{resp.StatusCode}
	}
	v, err := c.decode(resp.Body)
	if err != nil {
		return nil, err
	}
	if len(v) != n {
		return nil, fmt.Errorf("tei: %d vectors for %d texts", len(v), n)
	}
	for _, x := range v {
		if len(x) != c.o.Dim {
			return nil, dimError{len(x), c.o.Dim}
		}
	}
	return v, nil
}

// decode reads TEI's [[…]] or OpenAI's {"data":[{"index":i,"embedding":[…]}]}; OpenAI vectors are normalised here.
func (c *Client) decode(r io.Reader) ([][]float32, error) {
	if c.o.API != APIOpenAI {
		var v [][]float32
		if err := json.NewDecoder(r).Decode(&v); err != nil {
			return nil, fmt.Errorf("tei: decode: %w", err)
		}
		return v, nil
	}
	var resp struct {
		Data []struct {
			Index     int       `json:"index"`
			Embedding []float32 `json:"embedding"`
		} `json:"data"`
	}
	if err := json.NewDecoder(r).Decode(&resp); err != nil {
		return nil, fmt.Errorf("embed: decode: %w", err)
	}
	v := make([][]float32, len(resp.Data))
	for _, d := range resp.Data {
		if d.Index < 0 || d.Index >= len(v) || v[d.Index] != nil {
			return nil, fmt.Errorf("embed: bad index %d in response", d.Index)
		}
		v[d.Index] = normalize(d.Embedding)
	}
	return v, nil
}

func normalize(x []float32) []float32 {
	var s float64
	for _, f := range x {
		s += float64(f) * float64(f)
	}
	if s == 0 {
		return x
	}
	n := float32(1 / math.Sqrt(s))
	for i := range x {
		x[i] *= n
	}
	return x
}

// Ready checks GET /health, or GET /v1/models on servers without it (Ollama).
func (c *Client) Ready(ctx context.Context) error {
	return llm.ReadyCheck(ctx, c.http, c.base, c.o.APIKey)
}

// Literal formats a vector as a pgvector text literal ("[0.1,0.2,…]") for halfvec parameters.
func Literal(v []float32) string {
	b := make([]byte, 0, len(v)*10+2)
	b = append(b, '[')
	for i, x := range v {
		if i > 0 {
			b = append(b, ',')
		}
		b = fmt.Appendf(b, "%g", x)
	}
	return string(append(b, ']'))
}
