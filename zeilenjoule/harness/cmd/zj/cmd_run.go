package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/internal/runner"
)

func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func cmdRun(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	fs.SetOutput(stderr)
	repo := fs.String("repo", "", "repository root")
	track := fs.String("track", "", "run all contestants of a track (A–E, X)")
	ids := fs.String("id", "", "comma-separated contestant ids (alternative to --track)")
	scales := fs.String("scale", "S", "comma-separated scales")
	reps := fs.Int("reps", -1, "measured repetitions (default: bench.yaml reps)")
	warmup := fs.Int("warmup", -1, "warm-up runs (default: bench.yaml warmup)")
	threads := fs.Int("threads", 0, "threads passed as {threads} (default: all CPUs)")
	seed := fs.Int64("seed", 0, "schedule seed (default: bench.yaml seed)")
	session := fs.String("session", "", "resume an existing session id")
	noInterleave := fs.Bool("no-interleave", false, "contestant-major order instead of round-robin")
	dryRun := fs.Bool("dry-run", false, "print the plan and exit")
	allowMissing := fs.Bool("allow-missing-energy", false, "run even if GPU (NVML) or CPU (RAPL) energy is unavailable")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	p, err := loadProject(*repo, stderr)
	if err != nil {
		return fail(stderr, "run", err)
	}

	var m runner.Manifest
	if *session != "" {
		if m, err = runner.LoadManifest(p.repo, *session); err != nil {
			return fail(stderr, "run", fmt.Errorf("resume %s: %w", *session, err))
		}
	} else {
		m, err = newManifest(p, *track, *ids, *scales, *reps, *warmup, *threads, *seed, !*noInterleave, args)
		if err != nil {
			return fail(stderr, "run", err)
		}
	}
	if *dryRun {
		fmt.Fprintf(stdout, "session %s (dry run): %d items\n", m.SessionID, len(m.Items))
		for i, it := range m.Items {
			kind := "rep"
			if it.Warmup {
				kind = "warmup"
			}
			fmt.Fprintf(stdout, "%4d  %-28s %-3s %s %d\n", i+1, it.Contestant, it.Scale, kind, it.Rep)
		}
		return 0
	}
	p.openSources()
	defer p.close()
	if problems := p.energyProblems(); len(problems) > 0 && !*allowMissing {
		// a measured session without its energy sources wastes the quiet machine time (e.g. after a reboot
		// the non-persistent RAPL chmod and persistence mode are gone)
		return fail(stderr, "run", fmt.Errorf("energy sources missing: %s — fix them (docs/CHECKLIST_GATE1.md C1/C2) "+
			"or pass --allow-missing-energy to record those domains as null", strings.Join(problems, "; ")))
	}
	if *session == "" {
		if err := runner.SaveManifest(p.repo, m); err != nil {
			return fail(stderr, "run", err)
		}
	}
	r, err := p.runner(true)
	if err != nil {
		return fail(stderr, "run", err)
	}
	idleStore, err := results.NewStore(filepath.Join(p.repo, "results", "idle.jsonl"))
	if err != nil {
		return fail(stderr, "run", err)
	}
	start := time.Now()
	sum, err := r.RunSession(ctx, runner.SessionOpts{Manifest: m, Contestants: p.contestantMap(), IdleStore: idleStore})
	fmt.Fprintf(stdout, "session %s: ran %d, skipped %d (already recorded), statuses %v, %s\n",
		m.SessionID, sum.Ran, sum.Skipped, sum.ByStatus, time.Since(start).Round(time.Second))
	switch {
	case errors.Is(err, runner.ErrInterrupted):
		fmt.Fprintf(stderr, "interrupted — resume with: zj run --session %s\n", m.SessionID)
		return 130
	case err != nil:
		fmt.Fprintf(stderr, "resume later with: zj run --session %s\n", m.SessionID)
		return fail(stderr, "run", err)
	}
	return 0
}

func newManifest(p *project, track, ids, scales string, reps, warmup, threads int, seed int64, interleave bool, args []string) (runner.Manifest, error) {
	var cs []config.Contestant
	switch {
	case track != "" && ids != "":
		return runner.Manifest{}, errors.New("use either --track or --id")
	case track != "":
		t := strings.ToUpper(track)
		for _, c := range p.contestants {
			if c.Track == t {
				cs = append(cs, c)
			}
		}
	case ids != "":
		for _, id := range splitList(ids) {
			c, ok := config.FindContestant(p.contestants, id)
			if !ok {
				return runner.Manifest{}, fmt.Errorf("unknown contestant %q", id)
			}
			cs = append(cs, c)
		}
	default:
		return runner.Manifest{}, errors.New("need --track or --id")
	}
	if len(cs) == 0 {
		return runner.Manifest{}, fmt.Errorf("no contestants for track %q", track)
	}
	sc := splitList(scales)
	for _, s := range sc {
		if _, ok := p.bench.Scales[s]; !ok {
			return runner.Manifest{}, fmt.Errorf("unknown scale %q", s)
		}
	}
	if reps < 0 {
		reps = p.bench.Reps
	}
	if warmup < 0 {
		warmup = p.bench.Warmup
	}
	if seed == 0 {
		seed = p.bench.Seed
	}
	if !p.bench.Interleave {
		interleave = false
	}
	items := runner.MakePlan(cs, sc, p.bench.Scales, reps, warmup, interleave, seed)
	if len(items) == 0 {
		return runner.Manifest{}, fmt.Errorf("empty plan: no contestant declares scales %v", sc)
	}
	names := make([]string, 0, len(cs))
	for _, c := range cs {
		names = append(names, c.ID)
	}
	slices.Sort(names)
	return runner.Manifest{
		SessionID: results.NewID(), Created: time.Now().UTC().Format(time.RFC3339), Seed: seed,
		Reps: reps, Warmup: warmup, Interleave: interleave, Threads: threads, Scales: sc,
		Contestants: names, Items: items, Args: args,
	}, nil
}
