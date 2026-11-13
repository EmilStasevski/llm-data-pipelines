// Package embed is a client for Hugging Face Text Embeddings Inference (bge-m3, 1 024 dimensions).
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
	"net/http"
	"time"

	"golang.org/x/sync/errgroup"

	"zeilenjoule/services/internal/metrics"
)

// Dim is the bge-m3 dense vector size.
const Dim = 1024

// Client embeds texts with server-side truncation and L2 normalisation (as b06_tei in Track B).
type Client struct {
	base    string
	http    *http.Client
	sem     chan struct{}
	batch   int
	retries int
}

// New creates a client.
func New(base string, inFlight, batch int) *Client {
	return &Client{base: base, http: &http.Client{Timeout: 120 * time.Second},
		sem: make(chan struct{}, inFlight), batch: batch, retries: 5}
}

// Embed returns one vector per text, in order. op labels the metrics (query, ingest, label).
func (c *Client) Embed(ctx context.Context, op string, texts []string) ([][]float32, error) {
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

type statusError struct{ code int }

func (e statusError) Error() string { return fmt.Sprintf("tei: HTTP %d", e.code) }

func (c *Client) withRetry(ctx context.Context, op string, texts []string) ([][]float32, error) {
	body, err := json.Marshal(map[string]any{"inputs": texts, "truncate": true, "normalize": true})
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
		retry := err != nil && (!errors.As(err, &se) || se.code == http.StatusTooManyRequests || se.code >= 500) &&
			ctx.Err() == nil
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
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/embed", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil, statusError{resp.StatusCode}
	}
	var v [][]float32
	if err := json.NewDecoder(resp.Body).Decode(&v); err != nil {
		return nil, fmt.Errorf("tei: decode: %w", err)
	}
	if len(v) != n {
		return nil, fmt.Errorf("tei: %d vectors for %d texts", len(v), n)
	}
	for _, x := range v {
		if len(x) != Dim {
			return nil, fmt.Errorf("tei: dimension %d, want %d", len(x), Dim)
		}
	}
	return v, nil
}

// Ready checks GET /health.
func (c *Client) Ready(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/health", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return statusError{resp.StatusCode}
	}
	return nil
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
