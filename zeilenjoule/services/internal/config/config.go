// Package config reads the service configuration from the environment (12-factor; .env.example lists every key).
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is shared by zj-gateway and zj-worker; each uses the fields it needs.
type Config struct {
	Listen        string        // gateway HTTP address
	MetricsListen string        // worker /metrics address
	DatabaseURL   string        // owner role: migrations, ingest, search
	ReaderURL     string        // SELECT-only role for router SQL (ADR 0021)
	TEIURL        string        // text-embeddings-inference base URL
	LLMURL        string        // vLLM OpenAI-compatible base URL
	LLMModel      string        // served model name
	LLMInFlight   int           // ≤ vLLM --max-num-seqs
	EmbedInFlight int           // TEI requests in flight (ADR 0021 addendum: 4 × 128)
	EmbedBatch    int           // texts per TEI request
	APIKeys       []string      // accepted X-API-Key values
	RateLimit     float64       // requests/s per API key
	RateBurst     int           // token bucket size
	UploadDir     string        // shared volume gateway → worker
	MaxUpload     int64         // bytes per CSV upload
	RequestTO     time.Duration // per-request deadline (ask/search/extract)
	SQLTimeout    time.Duration // statement_timeout for router SQL
	ParserBin     string        // Rust parser (a16) binary
	Lexicon       string        // spec/track_a_lexicon.json inside the image
	ParserThreads int
}

// Load reads ZJ_* variables; required ones without a value are an error.
func Load() (Config, error) {
	var errs []string
	str := func(k, def string) string {
		if v, ok := os.LookupEnv(k); ok && v != "" {
			return v
		}
		return def
	}
	num := func(k string, def int) int {
		v := str(k, "")
		if v == "" {
			return def
		}
		n, err := strconv.Atoi(v)
		if err != nil || n <= 0 {
			errs = append(errs, fmt.Sprintf("%s=%q: positive integer expected", k, v))
		}
		return n
	}
	dur := func(k string, def time.Duration) time.Duration {
		v := str(k, "")
		if v == "" {
			return def
		}
		d, err := time.ParseDuration(v)
		if err != nil || d <= 0 {
			errs = append(errs, fmt.Sprintf("%s=%q: positive duration expected", k, v))
		}
		return d
	}
	c := Config{
		Listen:        str("ZJ_LISTEN", ":8080"),
		MetricsListen: str("ZJ_METRICS_LISTEN", ":9090"),
		DatabaseURL:   str("ZJ_DATABASE_URL", ""),
		ReaderURL:     str("ZJ_READER_URL", ""),
		TEIURL:        strings.TrimRight(str("ZJ_TEI_URL", "http://127.0.0.1:8081"), "/"),
		LLMURL:        strings.TrimRight(str("ZJ_LLM_URL", "http://127.0.0.1:8000"), "/"),
		LLMModel:      str("ZJ_LLM_MODEL", "model"),
		LLMInFlight:   num("ZJ_LLM_IN_FLIGHT", 32),
		EmbedInFlight: num("ZJ_EMBED_IN_FLIGHT", 4),
		EmbedBatch:    num("ZJ_EMBED_BATCH", 128),
		RateBurst:     num("ZJ_RATE_BURST", 20),
		UploadDir:     str("ZJ_UPLOAD_DIR", "/var/lib/zj/uploads"),
		MaxUpload:     int64(num("ZJ_MAX_UPLOAD_MB", 1024)) << 20,
		RequestTO:     dur("ZJ_REQUEST_TIMEOUT", 120*time.Second),
		SQLTimeout:    dur("ZJ_SQL_TIMEOUT", 10*time.Second),
		ParserBin:     str("ZJ_PARSER_BIN", "/usr/local/bin/zj-parse"),
		Lexicon:       str("ZJ_LEXICON", "/etc/zj/track_a_lexicon.json"),
		ParserThreads: num("ZJ_PARSER_THREADS", 8),
	}
	rate, err := strconv.ParseFloat(str("ZJ_RATE_LIMIT", "10"), 64)
	if err != nil || rate <= 0 {
		errs = append(errs, "ZJ_RATE_LIMIT: positive number expected")
	}
	c.RateLimit = rate
	for _, k := range strings.Split(str("ZJ_API_KEYS", ""), ",") {
		if k = strings.TrimSpace(k); k != "" {
			c.APIKeys = append(c.APIKeys, k)
		}
	}
	if c.DatabaseURL == "" {
		errs = append(errs, "ZJ_DATABASE_URL is required")
	}
	if len(errs) > 0 {
		return c, fmt.Errorf("config: %s", strings.Join(errs, "; "))
	}
	return c, nil
}
