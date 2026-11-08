package runner

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/results"
)

func TestRunSessionBuildsVersionsIdleResume(t *testing.T) {
	r, repo := rig(t)
	r.Idle = nil
	r.Bench.IdleBaseline = config.IdleBaseline{DurationS: 1, EveryMin: 30}
	c := shContestant(okScript)
	c.Build = []string{"sh", "-c", "echo built > built.txt"}
	c.VersionsCmd = []string{"sh", "-c", `echo '{"tool":"1.2.3"}'`}
	idleStore, _ := results.NewStore(filepath.Join(repo, "results", "idle.jsonl"))
	m := Manifest{SessionID: "sess-1", Items: []Item{{c.ID, "S", 1, true}, {c.ID, "S", 1, false}, {c.ID, "S", 2, false}}}
	opts := SessionOpts{Manifest: m, Contestants: map[string]config.Contestant{c.ID: c}, IdleStore: idleStore}

	sum, err := r.RunSession(context.Background(), opts)
	if err != nil {
		t.Fatal(err)
	}
	if sum.Ran != 3 || sum.ByStatus[results.StatusOK] != 3 {
		t.Fatalf("summary %+v", sum)
	}
	if _, err := os.Stat(filepath.Join(repo, "built.txt")); err != nil {
		t.Error("build step did not run in the repo dir")
	}
	idle, _ := os.ReadFile(idleStore.Path())
	if strings.Count(string(idle), "\n") != 1 {
		t.Errorf("want exactly one idle baseline, got:\n%s", idle)
	}
	recs, _ := results.ReadRecords(r.Store.Path())
	v, ok := recs[0].Env.Versions[c.ID].(map[string]any)
	if !ok || v["tool"] != "1.2.3" {
		t.Errorf("versions not recorded: %#v", recs[0].Env.Versions)
	}
	if recs[1].IdleW.CPUPkg == nil || recs[1].EnergyNetJ.CPUPkg == nil {
		t.Error("idle baseline not applied to runs")
	}

	// resume: everything is already recorded → nothing runs again
	sum, err = r.RunSession(context.Background(), opts)
	if err != nil || sum.Ran != 0 || sum.Skipped != 3 {
		t.Fatalf("resume: %+v, %v", sum, err)
	}
}

func TestRunSessionBuildFailureAbortsBeforeMeasuring(t *testing.T) {
	r, _ := rig(t)
	c := shContestant(okScript)
	c.Build = []string{"sh", "-c", "echo nope >&2; exit 1"}
	_, err := r.RunSession(context.Background(), SessionOpts{
		Manifest:    Manifest{SessionID: "s", Items: []Item{{c.ID, "S", 1, false}}},
		Contestants: map[string]config.Contestant{c.ID: c},
	})
	if err == nil || !strings.Contains(err.Error(), "build") || !strings.Contains(err.Error(), "nope") {
		t.Fatalf("got %v", err)
	}
	if recs, _ := results.ReadRecords(r.Store.Path()); len(recs) != 0 {
		t.Fatal("nothing may be recorded when the build fails")
	}
}

func TestManifestRoundTripNoOverwrite(t *testing.T) {
	repo := t.TempDir()
	m := Manifest{SessionID: "abc", Seed: 7, Items: []Item{{"x01_a", "S", 1, false}}}
	if err := SaveManifest(repo, m); err != nil {
		t.Fatal(err)
	}
	if err := SaveManifest(repo, m); err == nil {
		t.Fatal("existing session must not be overwritten")
	}
	got, err := LoadManifest(repo, "abc")
	if err != nil || got.Seed != 7 || len(got.Items) != 1 {
		t.Fatalf("%+v %v", got, err)
	}
}

func TestParseVersions(t *testing.T) {
	if v, ok := parseVersions(`{"a":1}`).(map[string]any); !ok || v["a"] != float64(1) {
		t.Error("JSON not parsed")
	}
	if parseVersions("rustc 1.99.1\n") != "rustc 1.99.1" {
		t.Error("plain text not trimmed")
	}
}

func TestMeasureQuietIdle(t *testing.T) {
	r, _ := rig(t)
	var stored []IdleRecord
	keep := func(rec IdleRecord) error { stored = append(stored, rec); return nil }

	rec, err := r.MeasureQuietIdle(context.Background(), "s", 100*time.Millisecond, 3, keep)
	if err != nil || !rec.Quiet || len(stored) != 1 {
		t.Fatalf("quiet machine: %+v, %v, %d stored", rec, err, len(stored))
	}

	stored = nil
	r.Bench.LoadGuard.MaxBusyPct = -1 // every measurement counts as "not quiet"
	_, err = r.MeasureQuietIdle(context.Background(), "s", 50*time.Millisecond, 2, keep)
	if !errors.Is(err, ErrIdleNotQuiet) {
		t.Fatalf("want ErrIdleNotQuiet, got %v", err)
	}
	if len(stored) != 2 || stored[0].Quiet {
		t.Fatalf("both attempts must be stored as data (quiet=false), got %+v", stored)
	}
}

func TestWaitForQuietUsesBusyFraction(t *testing.T) {
	r, _ := rig(t)
	r.Bench.LoadGuard = config.LoadGuard{MaxBusyPct: 5, WindowS: 0.01, MaxWaitS: 5}
	// each guard measurement reads /proc/stat twice: busy (50 %), busy, then quiet (2 %)
	seq := []string{
		"cpu  0 0 0 0 0 0 0 0\n", "cpu  500 0 0 500 0 0 0 0\n",
		"cpu  500 0 0 500 0 0 0 0\n", "cpu  1000 0 0 1000 0 0 0 0\n",
		"cpu  1000 0 0 1000 0 0 0 0\n", "cpu  1020 0 0 1980 0 0 0 0\n",
	}
	n := 0
	base := r.ReadFile
	r.ReadFile = func(p string) ([]byte, error) {
		if p == "/proc/stat" {
			s := seq[min(n, len(seq)-1)]
			n++
			return []byte(s), nil
		}
		return base(p)
	}
	busy, err := r.waitForQuiet(context.Background())
	if err != nil || busy != 2 || n != 6 {
		t.Fatalf("busy=%v err=%v reads=%d; want release on the 3rd window at 2 %%", busy, err, n)
	}

	r.Bench.LoadGuard.MaxWaitS = 0
	n = 0
	seq = seq[:2] // stays at 50 % forever (last line repeats → NaN, also not quiet)
	if _, err := r.waitForQuiet(context.Background()); !errors.Is(err, ErrBusy) {
		t.Fatalf("permanently busy machine must give ErrBusy, got %v", err)
	}
}
