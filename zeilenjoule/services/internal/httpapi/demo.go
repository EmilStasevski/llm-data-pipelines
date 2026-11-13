package httpapi

import (
	_ "embed"
	"net/http"
)

// demoHTML is the demo page (PLAN §13): question box, streamed answer with cited rows, J/question from
// GET /v1/energy. Plain HTML + JS without external scripts; the page holds no data, the API key is typed in.
//
//go:embed demo.html
var demoHTML []byte

func serveDemo(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_, _ = w.Write(demoHTML)
}
