package verify

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func write(t *testing.T, content string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "out.txt")
	if err := os.WriteFile(p, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestSmoke(t *testing.T) {
	good := write(t, "0\n1\n2\n")
	tests := []struct {
		name string
		in   Input
		pass bool
		want string
	}{
		{"ok", Input{OutputPath: good, ExpectedRows: 3, ResultRows: 3}, true, "3 lines"},
		{"reported rows wrong", Input{OutputPath: good, ExpectedRows: 3, ResultRows: 2}, false, "ZJRESULT rows=2"},
		{"too few lines", Input{OutputPath: write(t, "0\n1\n"), ExpectedRows: 3, ResultRows: 3}, false, "has 2 lines"},
		{"wrong content", Input{OutputPath: write(t, "0\n7\n2\n"), ExpectedRows: 3, ResultRows: 3}, false, `line 2: got "7"`},
		{"missing file", Input{OutputPath: "/nonexistent/x", ExpectedRows: 0, ResultRows: 0}, false, "output:"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			pass, detail := Run("builtin:x.smoke", tt.in)
			if pass != tt.pass || !strings.Contains(detail, tt.want) {
				t.Fatalf("got %v %q, want %v containing %q", pass, detail, tt.pass, tt.want)
			}
		})
	}
}

func TestCalib(t *testing.T) {
	if ok, d := Run("builtin:x.calib", Input{OutputPath: write(t, "exit=0\n")}); !ok {
		t.Fatal(d)
	}
	if ok, d := Run("builtin:x.calib", Input{OutputPath: write(t, "exit=3\n")}); ok || !strings.Contains(d, "exit=3") {
		t.Fatalf("got %v %q", ok, d)
	}
}

func TestFailClosed(t *testing.T) {
	for _, spec := range []string{"builtin:a.full", "cmd:whatever", ""} {
		if ok, d := Run(spec, Input{}); ok || !strings.Contains(d, "fail-closed") {
			t.Errorf("Run(%q) = %v %q; must fail closed", spec, ok, d)
		}
	}
}

func TestRunCmd(t *testing.T) {
	ctx := context.Background()
	cases := []struct {
		name, script string
		pass         bool
		want         string
	}{
		{"pass", `echo noise; echo '{"passed": true, "detail": "10 rows match"}'`, true, "10 rows match"},
		{"fail", `echo '{"passed": false, "detail": "mismatch: kosten_eur: 3 rows"}'; exit 1`, false, "kosten_eur"},
		{"no json", `echo crashed >&2; exit 2`, false, "no JSON"},
		{"json pass but exit 1", `echo '{"passed": true, "detail": "x"}'; exit 1`, false, "fail-closed"},
		{"json fail but exit 0", `echo '{"passed": false, "detail": "y"}'`, false, "fail-closed"},
		{"passed missing", `echo '{"detail": "z"}'`, false, "no JSON"},
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			pass, detail := RunCmd(ctx, t.TempDir(), []string{"sh", "-c", tt.script}, 10*time.Second)
			if pass != tt.pass || !strings.Contains(detail, tt.want) {
				t.Fatalf("got %v %q; want %v containing %q", pass, detail, tt.pass, tt.want)
			}
		})
	}
	if pass, d := RunCmd(ctx, t.TempDir(), nil, time.Second); pass || !strings.Contains(d, "fail-closed") {
		t.Fatalf("empty argv: %v %q", pass, d)
	}
}

func TestRunCmdQuality(t *testing.T) {
	ctx := context.Background()
	script := `echo '{"passed": true, "detail": "ok", "quality": {"recall_at_10": 0.97, "ndcg_at_10": 0.81}}'`
	pass, _, q := RunCmdQuality(ctx, t.TempDir(), []string{"sh", "-c", script}, 10*time.Second)
	if !pass || q["recall_at_10"] != 0.97 || q["ndcg_at_10"] != 0.81 {
		t.Fatalf("got %v %v", pass, q)
	}
	if _, _, q := RunCmdQuality(ctx, t.TempDir(), []string{"sh", "-c", `echo '{"passed": true, "detail": "x"}'`}, 10*time.Second); q != nil {
		t.Fatalf("no quality object should give nil, got %v", q)
	}
}

func TestLoad(t *testing.T) {
	step := func(stage, ep string, rate float64, planned, ok int, errs string) string {
		return `{"stage":"` + stage + `","endpoint":"` + ep + `","rate_rps":` + strings.TrimRight(strings.TrimRight(
			strconv.FormatFloat(rate, 'f', 2, 64), "0"), ".") + `,"planned":` + strconv.Itoa(planned) + `,"sent":` +
			strconv.Itoa(planned) + `,"ok":` + strconv.Itoa(ok) + `,"errors":{` + errs + `}}`
	}
	doc := func(rows int, steps ...string) string {
		return `{"rows":` + strconv.Itoa(rows) + `,"steps":[` + strings.Join(steps, ",") + `]}`
	}
	good := []string{step("search_5", "search", 5, 300, 300, ""), step("search_160", "search", 160, 100, 40, `"timeout":50,"overload":10`),
		step("ask_0p5", "ask", 0.5, 45, 45, ""), step("ask_8", "ask", 8, 720, 500, `"http_5xx":220`)}
	cases := []struct {
		name string
		out  string
		rows int64
		pass bool
	}{
		{"overload errors are results", doc(1000, good...), 1000, true},
		{"incomplete ingest", doc(999, good...), 999, false},
		{"result rows differ", doc(1000, good...), 999, false},
		{"wrong shape", doc(1000, append([]string{step("search_1", "search", 1, 60, 59, `"invalid":1`)}, good...)...), 1000, false},
		{"client error", doc(1000, append([]string{step("ask_9", "ask", 9, 10, 9, `"http_4xx":1`)}, good...)...), 1000, false},
		{"counts do not add up", doc(1000, append([]string{step("ask_9", "ask", 9, 10, 5, `"timeout":1`)}, good...)...), 1000, false},
		{"no ask step", doc(1000, good[:2]...), 1000, false},
		{"nothing right at the lowest rate", doc(1000, step("search_5", "search", 5, 300, 0, `"http_5xx":300`), good[2]), 1000, false},
		{"not json", "x", 1000, false},
	}
	for _, c := range cases {
		ok, detail := Run("builtin:e.load", Input{OutputPath: write(t, c.out), ExpectedRows: 1000, ResultRows: c.rows})
		if ok != c.pass {
			t.Errorf("%s: passed=%v (%s), want %v", c.name, ok, detail, c.pass)
		}
	}
}
