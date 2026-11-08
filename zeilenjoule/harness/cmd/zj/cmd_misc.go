package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/energy"
	"zeilenjoule/harness/internal/exporter"
	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/internal/runner"
)

func fmtW(v *float64) string {
	if v == nil {
		return "nicht gemessen / not measured"
	}
	return fmt.Sprintf("%.2f W", *v)
}

func cmdIdle(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("idle", flag.ContinueOnError)
	fs.SetOutput(stderr)
	repo := fs.String("repo", "", "repository root")
	dur := fs.Duration("duration", 0, "measurement duration (default: bench.yaml idle_baseline.duration_s)")
	noGuard := fs.Bool("no-guard", false, "do not wait for loadavg1 <= guard first")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	p, err := loadProject(*repo, stderr)
	if err != nil {
		return fail(stderr, "idle", err)
	}
	p.openSources()
	defer p.close()
	r, err := p.runner(false)
	if err != nil {
		return fail(stderr, "idle", err)
	}
	r.SkipGuards = *noGuard
	d := *dur
	if d == 0 {
		d = time.Duration(p.bench.IdleBaseline.DurationS) * time.Second
	}
	fmt.Fprintf(stderr, "measuring idle power for %s — keep the machine untouched…\n", d)
	rec, err := r.MeasureIdle(ctx, "standalone", d)
	if err != nil {
		return fail(stderr, "idle", err)
	}
	store, err := results.NewStore(filepath.Join(p.repo, "results", "idle.jsonl"))
	if err != nil {
		return fail(stderr, "idle", err)
	}
	if err := store.Append(rec); err != nil {
		return fail(stderr, "idle", err)
	}
	fmt.Fprintf(stdout, "idle over %.1f s (loadavg1 %.2f, quiet=%v):\n  gpu      %s\n  cpu_pkg  %s\n  cpu_core %s\n  dram     %s\n",
		rec.DurationS, rec.LoadAvg1, rec.Quiet, fmtW(rec.PowerW.GPU), fmtW(rec.PowerW.CPUPkg), fmtW(rec.PowerW.CPUCore), fmtW(rec.PowerW.DRAM))
	return 0
}

// cmdVerify runs a contestant once (no guards, not stored) and reports the verdict.
func cmdVerify(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("verify", flag.ContinueOnError)
	fs.SetOutput(stderr)
	repo := fs.String("repo", "", "repository root")
	scale := fs.String("scale", "S", "scale")
	asJSON := fs.Bool("json", false, "print the full record")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() != 1 {
		fmt.Fprintln(stderr, "usage: zj verify [--scale S] [--json] <contestant-id>")
		return 2
	}
	p, err := loadProject(*repo, stderr)
	if err != nil {
		return fail(stderr, "verify", err)
	}
	c, ok := config.FindContestant(p.contestants, fs.Arg(0))
	if !ok {
		return fail(stderr, "verify", fmt.Errorf("unknown contestant %q", fs.Arg(0)))
	}
	if _, ok := p.bench.Scales[*scale]; !ok {
		return fail(stderr, "verify", fmt.Errorf("unknown scale %q", *scale))
	}
	r, err := p.runner(false)
	if err != nil {
		return fail(stderr, "verify", err)
	}
	r.SkipGuards = true
	if len(c.Build) > 0 {
		if out, err := runCaptureSimple(ctx, p.repo, c.Build); err != nil {
			return fail(stderr, "verify", fmt.Errorf("build: %w\n%s", err, out))
		}
	}
	rec, err := r.Run(ctx, runner.RunSpec{Contestant: c, Scale: *scale, Rep: 0, SessionID: "verify"})
	if err != nil {
		return fail(stderr, "verify", err)
	}
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(rec)
	} else {
		fmt.Fprintf(stdout, "%s @ %s: status=%s  verify=%s  wall=%.3fs  log=%s\n",
			c.ID, *scale, rec.Status, rec.Verify.Detail, rec.WallS, rec.LogPath)
		if rec.StatusDetail != "" {
			fmt.Fprintf(stdout, "  detail: %s\n", rec.StatusDetail)
		}
	}
	if rec.Status != results.StatusOK {
		return 1
	}
	return 0
}

func runCaptureSimple(ctx context.Context, dir string, argv []string) (string, error) {
	cmd := execCommand(ctx, dir, argv)
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func cmdExporter(ctx context.Context, args []string, _, stderr io.Writer) int {
	fs := flag.NewFlagSet("exporter", flag.ContinueOnError)
	fs.SetOutput(stderr)
	listen := fs.String("listen", "127.0.0.1:9877", "listen address")
	gpuIndex := fs.Int("gpu", 0, "GPU index")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	var gpu energy.GPU
	g, err := energy.OpenNVML(*gpuIndex)
	if err != nil {
		fmt.Fprintln(stderr, "zj exporter: GPU metrics disabled:", err)
	} else {
		defer func() { _ = g.Close() }()
		gpu = g
	}
	zones, unreadable, err := energy.DiscoverRAPL(os.ReadFile, filepath.Glob)
	if err != nil || len(unreadable) > 0 {
		fmt.Fprintln(stderr, "zj exporter: some RAPL zones unavailable:", err, unreadable)
	}
	e := exporter.New(gpu, zones, os.ReadFile)
	go e.Run(ctx, time.Second)
	srv := &http.Server{
		Addr: *listen, Handler: e.Handler(), ReadHeaderTimeout: 5 * time.Second,
		BaseContext: func(net.Listener) context.Context { return ctx },
	}
	go func() {
		<-ctx.Done()
		// ctx is already canceled here; shutdown needs its own deadline but keeps ctx values
		shCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shCtx)
	}()
	fmt.Fprintf(stderr, "zj exporter listening on http://%s/metrics\n", *listen)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return fail(stderr, "exporter", err)
	}
	return 0
}

func execCommand(ctx context.Context, dir string, argv []string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...) //nolint:gosec // G204: registry build command
	cmd.Dir = dir
	return cmd
}
