package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"zeilenjoule/harness/internal/calib"
	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/internal/runner"
)

func readWatts(path string) *float64 {
	b, err := os.ReadFile(path) //nolint:gosec // G304: sysfs
	if err != nil {
		return nil
	}
	uw, err := strconv.ParseFloat(strings.TrimSpace(string(b)), 64)
	if err != nil {
		return nil
	}
	w := uw / 1e6
	return &w
}

func cmdCalibrate(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("calibrate", flag.ContinueOnError)
	fs.SetOutput(stderr)
	repo := fs.String("repo", "", "repository root")
	idleRuns := fs.Int("idle-runs", 3, "number of idle baselines")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	p, err := loadProject(*repo, stderr)
	if err != nil {
		return fail(stderr, "calibrate", err)
	}
	ids := []string{calib.IDOverhead, calib.IDCPU, calib.IDGPU} // GPU last: its heat must not skew the others
	cs := p.contestantMap()
	items := make([]runner.Item, 0, len(ids))
	for _, id := range ids {
		if _, ok := cs[id]; !ok {
			return fail(stderr, "calibrate", fmt.Errorf("calibration contestant %q missing in contestants.yaml", id))
		}
		reps := 1
		if id == calib.IDOverhead {
			reps = 3 // median over 3 (robust against short background bursts)
		}
		for rep := 1; rep <= reps; rep++ {
			items = append(items, runner.Item{Contestant: id, Scale: "S", Rep: rep})
		}
	}
	m := runner.Manifest{
		SessionID: results.NewID(), Created: time.Now().UTC().Format(time.RFC3339), Seed: p.bench.Seed,
		Reps: 1, Contestants: ids, Scales: []string{"S"}, Items: items, Args: append([]string{"calibrate"}, args...),
	}
	p.openSources()
	defer p.close()
	if problems := p.energyProblems(); len(problems) > 0 {
		return fail(stderr, "calibrate", fmt.Errorf("energy sources missing: %s (docs/CHECKLIST_GATE1.md C1/C2); "+
			"a calibration without them is meaningless", strings.Join(problems, "; ")))
	}
	if err := runner.SaveManifest(p.repo, m); err != nil {
		return fail(stderr, "calibrate", err)
	}
	r, err := p.runner(true)
	if err != nil {
		return fail(stderr, "calibrate", err)
	}
	idleStore, err := results.NewStore(filepath.Join(p.repo, "results", "idle.jsonl"))
	if err != nil {
		return fail(stderr, "calibrate", err)
	}

	d := time.Duration(p.bench.IdleBaseline.DurationS) * time.Second
	var idles []runner.IdleRecord
	for i := range *idleRuns {
		fmt.Fprintf(stderr, "idle baseline %d/%d (%s) — keep the machine untouched…\n", i+1, *idleRuns, d)
		rec, err := r.MeasureQuietIdle(ctx, m.SessionID, d, 3, func(rec runner.IdleRecord) error { return idleStore.Append(rec) })
		if err != nil {
			return calibErr(stderr, m.SessionID, err)
		}
		idles = append(idles, rec)
	}
	_, base := calib.IdleStats(idles)
	r.Idle = &runner.IdleRecord{SessionID: m.SessionID, PowerW: base, Taken: time.Now()}

	if _, err := r.RunSession(ctx, runner.SessionOpts{Manifest: m, Contestants: cs}); err != nil {
		return calibErr(stderr, m.SessionID, err)
	}
	all, err := results.ReadRecords(r.Store.Path())
	if err != nil {
		return fail(stderr, "calibrate", err)
	}
	var recs []results.Record
	for _, rec := range all {
		if rec.SessionID == m.SessionID {
			recs = append(recs, rec)
		}
	}
	var pl1, pl2 *float64
	for _, z := range p.zones {
		if z.Domain == "cpu_pkg" {
			pl1, pl2 = readWatts(z.Path+"/constraint_0_power_limit_uw"), readWatts(z.Path+"/constraint_1_power_limit_uw")
			break
		}
	}
	var gpuLimit *float64
	if p.gpuInfo != nil {
		gpuLimit = &p.gpuInfo.PowerLimitW
	}
	rep := calib.Evaluate(m.SessionID, time.Now().UTC().Format(time.RFC3339), idles, recs, pl1, pl2, gpuLimit)
	if err := writeCalibration(p.repo, rep); err != nil {
		return fail(stderr, "calibrate", err)
	}
	for _, c := range rep.Checks {
		mark := "–"
		if c.Pass != nil {
			mark = map[bool]string{true: "PASS", false: "FAIL"}[*c.Pass]
		}
		fmt.Fprintf(stdout, "%-5s %-45s %s  (expected %s)\n", mark, c.Name, c.Value, c.Expect)
	}
	fmt.Fprintf(stdout, "\nwrote docs/CALIBRATION.md and results/calibration/%s.json\n", m.SessionID)
	if !rep.AllPass() {
		return 1
	}
	return 0
}

func calibErr(stderr io.Writer, session string, err error) int {
	if errors.Is(err, runner.ErrBusy) || errors.Is(err, runner.ErrIdleNotQuiet) {
		fmt.Fprintln(stderr, "machine not quiet enough for calibration — pause k3s, other benchmarks and containers (checklist C5/C6), then rerun")
	}
	fmt.Fprintf(stderr, "calibration session %s aborted (no report written)\n", session)
	return fail(stderr, "calibrate", err)
}

func writeCalibration(repo string, rep calib.Report) error {
	dir := filepath.Join(repo, "results", "calibration")
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return err
	}
	b, err := json.MarshalIndent(rep, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(dir, rep.SessionID+".json"), append(b, '\n'), 0o600); err != nil {
		return err
	}
	md, err := rep.Markdown()
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(repo, "docs", "CALIBRATION.md"), []byte(md), 0o600)
}
