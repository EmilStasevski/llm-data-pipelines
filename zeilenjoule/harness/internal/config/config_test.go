package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var repoConfig = filepath.Join("..", "..", "..", "config")

// The real config files in the repo must always load (guards against broken edits).
func TestRepoConfigsLoad(t *testing.T) {
	b, err := LoadBench(filepath.Join(repoConfig, "bench.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if b.Scales["L"] != 1_000_000 || b.Reps != 5 || b.LoadGuard.MaxBusyPct != 5 {
		t.Fatalf("unexpected bench values: %+v", b)
	}
	cs, err := LoadContestants(filepath.Join(repoConfig, "contestants.yaml"), b)
	if err != nil {
		t.Fatal(err)
	}
	if len(cs) < 2 {
		t.Fatalf("expected the smoke contestants, got %d", len(cs))
	}
	tasks, err := LoadTasks(filepath.Join(repoConfig, "tasks.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if err := CheckTasksExist(cs, tasks); err != nil {
		t.Fatal(err)
	}
	if _, ok := FindContestant(cs, "x01_smoke_rs"); !ok {
		t.Fatal("x01_smoke_rs not found")
	}
	if d, err := b.Runs.OutputDirAbs(); err != nil || strings.HasPrefix(d, "~") {
		t.Fatalf("OutputDirAbs = %q, %v", d, err)
	}
}

func TestCheckTasksExist(t *testing.T) {
	err := CheckTasksExist([]Contestant{{ID: "a01_x", Task: "a.full"}}, map[string]Task{"x.smoke": {}})
	if err == nil || !strings.Contains(err.Error(), "a01_x → a.full") {
		t.Fatalf("got %v", err)
	}
}

func TestUnknownYAMLKeyRejected(t *testing.T) {
	p := filepath.Join(t.TempDir(), "bench.yaml")
	src, err := os.ReadFile(filepath.Join(repoConfig, "bench.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, append(src, []byte("\nrepz: 3\n")...), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadBench(p); err == nil || !strings.Contains(err.Error(), "repz") {
		t.Fatalf("typo key must be rejected, got %v", err)
	}
}

func TestBenchValidateCollectsAllErrors(t *testing.T) {
	b := Bench{Scales: map[string]int{"S": 0}}
	err := b.Validate()
	if err == nil {
		t.Fatal("want error")
	}
	for _, want := range []string{"reps", "timeout_s", "scales.S", "gpu_test_image must be pinned", "keep_outputs", "max_wait_s"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error should mention %q: %v", want, err)
		}
	}
}

func TestValidateContestants(t *testing.T) {
	bench := &Bench{Scales: map[string]int{"S": 10, "M": 100}}
	ok := Contestant{
		ID: "a09_polars_eager", Track: "A", Task: "a.full", Cmd: []string{"x"},
		Args: []string{"--output", "{output}", "--threads", "{threads}"}, Scales: []string{"S", "M"},
	}
	if err := ValidateContestants([]Contestant{ok}, bench); err != nil {
		t.Fatalf("valid contestant rejected: %v", err)
	}
	mut := func(f func(*Contestant)) Contestant { c := ok; f(&c); return c }
	tests := []struct {
		name string
		cs   []Contestant
		want string
	}{
		{"bad id", []Contestant{mut(func(c *Contestant) { c.ID = "A09-polars" })}, "id must match"},
		{"track mismatch", []Contestant{mut(func(c *Contestant) { c.Track = "B" })}, "does not match id prefix"},
		{"duplicate", []Contestant{ok, ok}, "duplicate id"},
		{"bad task", []Contestant{mut(func(c *Contestant) { c.Task = "b.embed" })}, "task"},
		{"empty cmd", []Contestant{mut(func(c *Contestant) { c.Cmd = nil })}, "cmd must not be empty"},
		{"placeholder", []Contestant{mut(func(c *Contestant) { c.Args = []string{"{output}", "{inptu}"} })}, "unknown placeholder {inptu}"},
		{"no output", []Contestant{mut(func(c *Contestant) { c.Args = []string{"--threads", "{threads}"} })}, "{output}"},
		{"scale", []Contestant{mut(func(c *Contestant) { c.Scales = []string{"XXL"} })}, `scale "XXL"`},
		{"timeout", []Contestant{mut(func(c *Contestant) { c.TimeoutS = -1 })}, `timeout_s must be >= 0`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateContestants(tt.cs, bench)
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("want error containing %q, got %v", tt.want, err)
			}
		})
	}
}

func TestContestantTimeoutOverride(t *testing.T) {
	if got := (Contestant{}).Timeout(1800); got != 1800 {
		t.Fatalf("default: got %d, want 1800", got)
	}
	if got := (Contestant{TimeoutS: 9000}).Timeout(1800); got != 9000 {
		t.Fatalf("override: got %d, want 9000", got)
	}
}
