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
