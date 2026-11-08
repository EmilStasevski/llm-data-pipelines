package runner

import (
	"context"
	"errors"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/energy"
	"zeilenjoule/harness/internal/procs"
	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/pkg/zjmark"
)

const fakeZone = "/fake/rapl:0"

// rig builds a Runner whose only RAPL zone ramps at exactly 10 W of wall time.
func rig(t *testing.T) (*Runner, string) {
	t.Helper()
	repo := t.TempDir()
	epoch := time.Now()
	read := func(p string) ([]byte, error) {
		if p == fakeZone+"/energy_uj" {
			uj := uint64(time.Since(epoch).Microseconds()) * 10 // 10 µJ per µs = 10 W
			return []byte(strconv.FormatUint(uj, 10)), nil
		}
		return os.ReadFile(p) //nolint:gosec // test
	}
	store, err := results.NewStore(filepath.Join(repo, "results", "runs.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	b := &config.Bench{
		TimeoutS: 2, SampleHz: 100, Scales: map[string]int{"S": 5},
		LoadGuard: config.LoadGuard{MaxBusyPct: 100, WindowS: 0.05, MaxWaitS: 1},
		Runs:      config.Runs{OutputDir: filepath.Join(repo, "out"), KeepOutputs: "all", TreePollHz: 50, SettleS: 0.05},
	}
	r := &Runner{
		Repo: repo, Bench: b, ReadFile: read, Store: store, SkipGuards: true,
		Tasks: map[string]config.Task{"x.smoke": {Output: "out.txt", Verifier: "builtin:x.smoke"}},
		Zones: []energy.RAPLZone{{Name: "package-0", Domain: "cpu_pkg", Path: fakeZone, MaxRangeUJ: 1 << 50}},
		Tree:  procs.FS{ReadFile: os.ReadFile, Glob: filepath.Glob},
		Idle:  &IdleRecord{PowerW: results.Domains{CPUPkg: ptrF(4)}},
	}
	return r, repo
}

func ptrF(v float64) *float64 { return &v }

func shContestant(script string) config.Contestant {
	return config.Contestant{
		ID: "x99_test_sh", Track: "X", Task: "x.smoke",
		Cmd: []string{"sh", "-c", script, "sh"}, Args: []string{"{output}", "{rows}"}, Scales: []string{"S"},
	}
}

const okScript = `
echo 'ZJMARK {"ev":"start","stage":"parse"}' >&2
echo 'ZJMARK {"ev":"start","stage":"inner"}' >&2
sleep 0.2
echo 'ZJMARK {"ev":"end","stage":"inner"}' >&2
seq 0 $(($2-1)) > "$1"
echo 'ZJMARK {"ev":"end","stage":"parse"}' >&2
echo "noise on stderr" >&2
echo 'ZJRESULT {"extra":{},"notes":"","rows":'$2'}'
`

func run(t *testing.T, r *Runner, script string) results.Record {
	t.Helper()
	rec, err := r.Run(context.Background(), RunSpec{Contestant: shContestant(script), Scale: "S", Rep: 1, SessionID: "sess"})
	if err != nil {
		t.Fatal(err)
	}
	return rec
}

func TestRunOK(t *testing.T) {
	r, repo := rig(t)
	rec := run(t, r, okScript)
	if rec.Status != results.StatusOK || rec.Verify.Passed == nil || !*rec.Verify.Passed {
		t.Fatalf("status %s (%s), verify %+v", rec.Status, rec.StatusDetail, rec.Verify)
	}
	if rec.WallS < 0.2 || rec.WallS > 1.5 {
		t.Errorf("wall_s = %v", rec.WallS)
	}
	// fake zone is exactly 10 W → energy ≈ 10 × wall; net = gross − 4 W × wall
	if e := *rec.EnergyJ.CPUPkg; math.Abs(e-10*rec.WallS) > 0.15*10*rec.WallS {
		t.Errorf("cpu_pkg = %.3f J for %.3f s (want ≈ 10 W)", e, rec.WallS)
	}
	if n := *rec.EnergyNetJ.CPUPkg; math.Abs(n-6*rec.WallS) > 0.2*6*rec.WallS {
		t.Errorf("net cpu_pkg = %.3f J (want ≈ 6 W × wall)", n)
	}
	if rec.EnergyJ.GPU != nil || rec.EnergyJ.DRAM != nil {
		t.Error("unmeasured domains must be nil")
	}
	if rec.EnergyConfidence != "low" {
		t.Error("windows < 5 s must be low confidence")
	}
	names := []string{}
	for _, s := range rec.Stages {
		names = append(names, s.Name+":"+strconv.Itoa(s.Depth))
	}
	if !slices.Equal(names, []string{"inner:1", "parse:0"}) {
		t.Errorf("stages = %v", names)
	}
	if rec.Stages[0].TS < 0.18 {
		t.Errorf("inner stage %v s, want ≥ 0.2", rec.Stages[0].TS)
	}
	if rec.Throughput.RowsPerS == nil || rec.Throughput.RowsPerKJ == nil {
		t.Error("throughput not derived")
	}
	log, _ := os.ReadFile(filepath.Join(repo, rec.LogPath))
	if !strings.Contains(string(log), "noise on stderr") || strings.Contains(string(log), `"stage":"parse"`) {
		t.Errorf("log should contain non-marker stderr only:\n%s", log)
	}
	recs, _ := results.ReadRecords(r.Store.Path())
	if len(recs) != 1 || recs[0].RunID != rec.RunID {
		t.Fatalf("store has %d records", len(recs))
	}
}

func TestRunStatuses(t *testing.T) {
	tests := []struct {
		name, script, status, detail string
	}{
		{"disqualified", `seq 0 2 > "$1"; echo 'ZJRESULT {"extra":{},"notes":"","rows":5}'`, results.StatusDisqualified, "has 3 lines"},
		{"exit code", `echo boom >&2; exit 3`, results.StatusFail, "exit code 3"},
		{"timeout", `sleep 10 & sleep 10`, results.StatusDNF, "timeout"},
		{"sigkill", `kill -9 $$`, results.StatusOOM, "SIGKILL"},
		{"unclosed stage", `echo 'ZJMARK {"ev":"start","stage":"a"}' >&2; seq 0 4 > "$1"; echo 'ZJRESULT {"extra":{},"notes":"","rows":5}'`, results.StatusFail, `"a" never ended`},
		{"bad marker", `echo 'ZJMARK {"ev":"start","stage":"Bad"}' >&2; seq 0 4 > "$1"; echo 'ZJRESULT {"extra":{},"notes":"","rows":5}'`, results.StatusFail, "invalid stage"},
		{"no result", `seq 0 4 > "$1"`, results.StatusFail, "no ZJRESULT"},
		{"two results", `seq 0 4 > "$1"; echo 'ZJRESULT {"extra":{},"notes":"","rows":5}'; echo 'ZJRESULT {"extra":{},"notes":"","rows":5}'`, results.StatusFail, "2 ZJRESULT"},
		{"missing binary", ``, results.StatusFail, "start:"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r, _ := rig(t)
			c := shContestant(tt.script)
			if tt.name == "missing binary" {
				c.Cmd = []string{"/nonexistent/zj-contestant"}
			}
			start := time.Now()
			rec, err := r.Run(context.Background(), RunSpec{Contestant: c, Scale: "S", Rep: 1, SessionID: "s"})
			if err != nil {
				t.Fatal(err)
			}
			if rec.Status != tt.status || !strings.Contains(rec.StatusDetail, tt.detail) {
				t.Fatalf("status %q detail %q; want %q containing %q", rec.Status, rec.StatusDetail, tt.status, tt.detail)
			}
			if time.Since(start) > 6*time.Second {
				t.Errorf("took %v (timeout must kill the whole process group)", time.Since(start))
			}
		})
	}
}

func TestLeftoverChildrenKilled(t *testing.T) {
	r, _ := rig(t)
	start := time.Now()
	rec := run(t, r, `sleep 30 & seq 0 4 > "$1"; echo 'ZJRESULT {"extra":{},"notes":"","rows":5}'`)
	if rec.Status != results.StatusOK || !strings.Contains(rec.StatusDetail, "leftover") {
		t.Fatalf("status %s detail %q", rec.Status, rec.StatusDetail)
	}
	if time.Since(start) > 3*time.Second {
		t.Fatalf("took %v: leftover sleep held the pipes", time.Since(start))
	}
}

func TestInterruptWritesNoRecord(t *testing.T) {
	r, _ := rig(t)
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	_, err := r.Run(ctx, RunSpec{Contestant: shContestant(`sleep 5`), Scale: "S", Rep: 1, SessionID: "s"})
	if !errors.Is(err, ErrInterrupted) {
		t.Fatalf("want ErrInterrupted, got %v", err)
	}
	if recs, _ := results.ReadRecords(r.Store.Path()); len(recs) != 0 {
		t.Fatalf("interrupted run must not be stored, got %d", len(recs))
	}
}

func TestKeepOutputsFailed(t *testing.T) {
	r, _ := rig(t)
	r.Bench.Runs.KeepOutputs = "failed"
	rec := run(t, r, okScript)
	if rec.OutputPath != "" {
		t.Errorf("ok run output should be removed, got %q", rec.OutputPath)
	}
	bad := run(t, r, `seq 0 1 > "$1"; echo 'ZJRESULT {"extra":{},"notes":"","rows":5}'`)
	if _, err := os.Stat(bad.OutputPath); err != nil {
		t.Errorf("disqualified output must be kept: %v", err)
	}
}

func TestMeasureIdle(t *testing.T) {
	r, _ := rig(t)
	rec, err := r.MeasureIdle(context.Background(), "s", 300*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	if p := *rec.PowerW.CPUPkg; math.Abs(p-10) > 1 {
		t.Errorf("idle cpu_pkg = %.2f W, want 10", p)
	}
	if rec.PowerW.GPU != nil {
		t.Error("no GPU → nil")
	}
}

func TestPairStages(t *testing.T) {
	m := func(ev, st string, at int) Marker {
		return Marker{Marker: zjmark.Marker{Ev: ev, Stage: st}, At: time.Duration(at)}
	}
	spans, errs := PairStages([]Marker{m("start", "a", 1), m("start", "b", 2), m("end", "b", 3), m("end", "a", 4)})
	if len(errs) != 0 || len(spans) != 2 || spans[0].Name != "b" || spans[0].Depth != 1 || spans[1].End != 4 {
		t.Fatalf("nested: %+v %v", spans, errs)
	}
	_, errs = PairStages([]Marker{m("start", "a", 1), m("start", "b", 2), m("end", "a", 3), m("end", "b", 4)})
	if len(errs) == 0 {
		t.Fatal("interleaved stages must be an error")
	}
	_, errs = PairStages([]Marker{m("end", "x", 1)})
	if len(errs) != 1 {
		t.Fatal("end without start must be an error")
	}
}

func TestMakePlan(t *testing.T) {
	cs := []config.Contestant{
		{ID: "a01_x", Scales: []string{"S", "M"}},
		{ID: "a02_y", Scales: []string{"S", "M"}},
		{ID: "a03_z", Scales: []string{"S"}},
	}
	rows := map[string]int{"S": 10, "M": 100}
	p1 := MakePlan(cs, []string{"M", "S"}, rows, 3, 1, true, 42)
	p2 := MakePlan(cs, []string{"M", "S"}, rows, 3, 1, true, 42)
	if !slices.Equal(p1, p2) {
		t.Fatal("same seed must give the same plan")
	}
	if len(p1) != 3*(1+3)+2*(1+3) {
		t.Fatalf("plan length %d", len(p1))
	}
	if p1[0].Scale != "S" || p1[len(p1)-1].Scale != "M" {
		t.Error("scales must run in ascending row order")
	}
	for i := range 3 {
		if !p1[i].Warmup {
			t.Errorf("item %d should be a warmup: %+v", i, p1[i])
		}
	}
	for _, it := range p1 {
		if it.Contestant == "a03_z" && it.Scale == "M" {
			t.Fatal("a03_z does not declare scale M")
		}
	}
	// interleave: every round contains each contestant once
	round := p1[3:6]
	ids := []string{round[0].Contestant, round[1].Contestant, round[2].Contestant}
	slices.Sort(ids)
	if !slices.Equal(ids, []string{"a01_x", "a02_y", "a03_z"}) || round[0].Rep != 1 || round[2].Rep != 1 {
		t.Errorf("round 1 = %+v", round)
	}
	seq := MakePlan(cs[:1], []string{"S"}, rows, 2, 1, false, 1)
	if len(seq) != 3 || !seq[0].Warmup || seq[2].Rep != 2 {
		t.Errorf("sequential plan = %+v", seq)
	}
	// different seeds must be able to produce different orders (6 permutations per round)
	differs := false
	for seed := int64(1); seed <= 20 && !differs; seed++ {
		differs = !slices.Equal(MakePlan(cs, []string{"S"}, rows, 3, 1, true, seed), MakePlan(cs, []string{"S"}, rows, 3, 1, true, 42))
	}
	if !differs {
		t.Error("seed has no effect on the order")
	}

}
