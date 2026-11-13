// Package verify decides whether a run's output is correct (rule 4: a fast but wrong run is
// disqualified). Verification is fail-closed: unknown verifiers never pass.
package verify

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// Input is what a verifier gets after a run finished with exit 0.
type Input struct {
	OutputPath   string
	ExpectedRows int64 // rows of the scale
	ResultRows   int64 // rows the contestant reported in ZJRESULT
}

// Func returns (passed, detail).
type Func func(Input) (bool, string)

var builtins = map[string]Func{
	"x.smoke": smoke,
	"x.calib": calib,
	"e.load":  load,
}

// Run executes the verifier named by spec ("builtin:<name>").
func Run(spec string, in Input) (bool, string) {
	name, ok := strings.CutPrefix(spec, "builtin:")
	if !ok {
		return false, fmt.Sprintf("unsupported verifier %q (fail-closed)", spec)
	}
	fn, ok := builtins[name]
	if !ok {
		return false, fmt.Sprintf("no builtin verifier %q (fail-closed)", name)
	}
	return fn(in)
}

// smoke: output has exactly ExpectedRows lines "0".."n-1" and ZJRESULT rows agrees.
func smoke(in Input) (bool, string) {
	if in.ResultRows != in.ExpectedRows {
		return false, fmt.Sprintf("ZJRESULT rows=%d, expected %d", in.ResultRows, in.ExpectedRows)
	}
	f, err := os.Open(in.OutputPath)
	if err != nil {
		return false, "output: " + err.Error()
	}
	defer func() { _ = f.Close() }()
	sc := bufio.NewScanner(f)
	var n int64
	for sc.Scan() {
		if want := strconv.FormatInt(n, 10); sc.Text() != want {
			return false, fmt.Sprintf("line %d: got %q, want %q", n+1, sc.Text(), want)
		}
		n++
	}
	if err := sc.Err(); err != nil {
		return false, "output: " + err.Error()
	}
	if n != in.ExpectedRows {
		return false, fmt.Sprintf("output has %d lines, expected %d", n, in.ExpectedRows)
	}
	return true, fmt.Sprintf("%d lines match", n)
}

// calib: the calibration wrapper writes "exit=<code>" of the wrapped load generator.
func calib(in Input) (bool, string) {
	b, err := os.ReadFile(in.OutputPath)
	if err != nil {
		return false, "output: " + err.Error()
	}
	first, _, _ := strings.Cut(strings.TrimSpace(string(b)), "\n")
	if first != "exit=0" {
		return false, fmt.Sprintf("wrapped command reported %q", first)
	}
	return true, "wrapped command exited 0"
}

// load: the open-loop load curve of the production stack (e07_stack_load, ADR 0026). Errors under overload
// (timeouts, 5xx, generator overload) are measurements; the run fails if the ingest is incomplete, a step is
// missing or short, any 2xx answer had the wrong shape or any request was rejected as a client error (4xx), or
// the lowest rate of an endpoint got no correct answer at all.
func load(in Input) (bool, string) {
	if in.ResultRows != in.ExpectedRows {
		return false, fmt.Sprintf("ZJRESULT rows=%d, expected %d (ingest incomplete)", in.ResultRows, in.ExpectedRows)
	}
	b, err := os.ReadFile(in.OutputPath)
	if err != nil {
		return false, "output: " + err.Error()
	}
	var out struct {
		Rows  int64 `json:"rows"`
		Steps []struct {
			Stage    string         `json:"stage"`
			Endpoint string         `json:"endpoint"`
			RateRPS  float64        `json:"rate_rps"`
			Planned  int            `json:"planned"`
			Sent     int            `json:"sent"`
			OK       int            `json:"ok"`
			Errors   map[string]int `json:"errors"`
		} `json:"steps"`
	}
	if err := json.Unmarshal(b, &out); err != nil {
		return false, "output: " + err.Error()
	}
	if out.Rows != in.ExpectedRows {
		return false, fmt.Sprintf("out.json rows=%d, expected %d", out.Rows, in.ExpectedRows)
	}
	lowest := map[string]float64{}
	okAtLowest := map[string]int{}
	var requests, ok int
	for _, st := range out.Steps {
		if st.Endpoint != "search" && st.Endpoint != "ask" {
			return false, fmt.Sprintf("step %q: unknown endpoint %q", st.Stage, st.Endpoint)
		}
		if st.Planned <= 0 {
			return false, fmt.Sprintf("step %q: no requests planned", st.Stage)
		}
		errs := 0
		for _, n := range st.Errors {
			errs += n
		}
		if st.OK+errs != st.Planned {
			return false, fmt.Sprintf("step %q: ok %d + errors %d != planned %d", st.Stage, st.OK, errs, st.Planned)
		}
		if n := st.Errors["invalid"] + st.Errors["http_4xx"]; n > 0 {
			return false, fmt.Sprintf("step %q: %d wrong-shaped or rejected (4xx) answers", st.Stage, n)
		}
		if r, seen := lowest[st.Endpoint]; !seen || st.RateRPS < r {
			lowest[st.Endpoint], okAtLowest[st.Endpoint] = st.RateRPS, st.OK
		}
		requests += st.Planned
		ok += st.OK
	}
	for _, ep := range []string{"search", "ask"} {
		if _, seen := lowest[ep]; !seen {
			return false, "no " + ep + " step"
		}
		if okAtLowest[ep] == 0 {
			return false, fmt.Sprintf("%s: no correct answer at the lowest rate %v/s", ep, lowest[ep])
		}
	}
	return true, fmt.Sprintf("%d steps, %d requests, %d ok, %d rows ingested", len(out.Steps), requests, ok, out.Rows)
}

// RunCmd runs an external verifier (e.g. zj_eval.verify_track_a). Fail-closed: the run passes only if
// the last non-empty stdout line is JSON with "passed": true AND the process exits 0.
func RunCmd(ctx context.Context, dir string, argv []string, timeout time.Duration) (bool, string) {
	passed, detail, _ := RunCmdQuality(ctx, dir, argv, timeout)
	return passed, detail
}

// RunCmdQuality is RunCmd plus the verifier's optional "quality" object (e.g. recall_at_10, ndcg_at_10),
// which the runner copies into the result record. Quality is nil when the verifier gives none.
func RunCmdQuality(ctx context.Context, dir string, argv []string, timeout time.Duration) (bool, string, map[string]any) {
	if len(argv) == 0 {
		return false, "empty verify_cmd (fail-closed)", nil
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...) //nolint:gosec // G204: verifier command from tasks.yaml
	cmd.Dir = dir
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	runErr := cmd.Run()
	var last string
	for _, line := range strings.Split(strings.TrimSpace(stdout.String()), "\n") {
		if strings.TrimSpace(line) != "" {
			last = line
		}
	}
	var res struct {
		Passed  *bool          `json:"passed"`
		Detail  string         `json:"detail"`
		Quality map[string]any `json:"quality"`
	}
	if err := json.Unmarshal([]byte(last), &res); err != nil || res.Passed == nil {
		return false, fmt.Sprintf("verifier gave no JSON result (exit %v): %s", runErr, lastNonEmpty(stderr.String())), nil
	}
	if *res.Passed != (runErr == nil) {
		return false, fmt.Sprintf("verifier JSON passed=%v but exit status %v (fail-closed): %s", *res.Passed, runErr, res.Detail), res.Quality
	}
	return *res.Passed, res.Detail, res.Quality
}

func lastNonEmpty(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.LastIndexByte(s, '\n'); i >= 0 {
		return s[i+1:]
	}
	return s
}
