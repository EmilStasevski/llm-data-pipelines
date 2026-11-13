// Package metrics defines the Prometheus series of zj-gateway and zj-worker (dashboards: deploy/monitoring).
//
// Energy per request is derived in Grafana from zj-energy-exporter (GPU/CPU joule counters) divided by the
// request counters below, so no service needs to read NVML/RAPL itself.
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	// HTTPDuration is the request latency per operation and status class.
	HTTPDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: "zj", Name: "http_request_duration_seconds", Help: "HTTP request latency.",
		Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 20, 40, 80},
	}, []string{"op", "code"})
	// Upstream measures calls to TEI / vLLM / Postgres.
	Upstream = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: "zj", Name: "upstream_duration_seconds", Help: "Latency of upstream calls.",
		Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 20, 40},
	}, []string{"upstream", "op", "outcome"})
	// LLMTokens counts prompt and completion tokens per operation (router, answer, label, extract).
	LLMTokens = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "zj", Name: "llm_tokens_total", Help: "LLM tokens by operation and kind.",
	}, []string{"op", "kind"})
	// EmbeddedTexts counts texts sent to TEI.
	EmbeddedTexts = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "zj", Name: "embedded_texts_total", Help: "Texts embedded by TEI.",
	}, []string{"op"})
	// Routes counts router decisions.
	Routes = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "zj", Name: "ask_routes_total", Help: "Router decisions (sql, search, invalid).",
	}, []string{"route"})
	// SQL counts router SQL outcomes: executed, rejected (guard), failed (database error/timeout).
	SQL = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "zj", Name: "router_sql_total", Help: "Router SQL by outcome.",
	}, []string{"outcome"})
	// IngestRows counts rows written to the docs table, by schadensart source.
	IngestRows = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "zj", Name: "ingest_rows_total", Help: "Rows ingested.",
	}, []string{"schadensart_source"})
	// IngestStage measures the ingest stages (parse, embed, label, write).
	IngestStage = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: "zj", Name: "ingest_stage_seconds", Help: "Ingest stage duration per batch or job.",
		Buckets: prometheus.ExponentialBuckets(0.01, 2.5, 14),
	}, []string{"stage"})
	// RateLimited counts requests rejected by the per-key token bucket.
	RateLimited = promauto.NewCounter(prometheus.CounterOpts{
		Namespace: "zj", Name: "rate_limited_total", Help: "Requests rejected with 429.",
	})
)
