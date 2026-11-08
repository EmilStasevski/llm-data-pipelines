// Command zj is the ZeilenJoule benchmark harness.
//
// Phase 1: `zj version`, `zj doctor`. Phase 2 adds run / verify / idle / exporter.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"syscall"
	"text/tabwriter"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/doctor"
)

const usage = `zj — ZeilenJoule benchmark harness

usage:
  zj version
  zj doctor   [--repo DIR] [--json] [--gpu-test]
  zj run      (--track A | --id a,b) [--scale S,M] [--reps N] [--warmup N] [--threads N] [--dry-run]
  zj run      --session ID              resume a session
  zj idle     [--duration 60s] [--no-guard]
  zj verify   [--scale S] [--json] <contestant-id>
  zj exporter [--listen 127.0.0.1:9877]
  zj calibrate [--idle-runs 3]          idle ×3 + sleep/CPU/GPU load → docs/CALIBRATION.md
`

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	code := run(ctx, os.Args[1:], os.Stdout, os.Stderr)
	stop()
	os.Exit(code)
}

func run(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprint(stderr, usage)
		return 2
	}
	switch args[0] {
	case "version":
		fmt.Fprintln(stdout, versionString())
		return 0
	case "doctor":
		return cmdDoctor(ctx, args[1:], stdout, stderr)
	case "run":
		return cmdRun(ctx, args[1:], stdout, stderr)
	case "idle":
		return cmdIdle(ctx, args[1:], stdout, stderr)
	case "verify":
		return cmdVerify(ctx, args[1:], stdout, stderr)
	case "exporter":
		return cmdExporter(ctx, args[1:], stdout, stderr)
	case "calibrate":
		return cmdCalibrate(ctx, args[1:], stdout, stderr)
	case "-h", "--help", "help":
		fmt.Fprint(stdout, usage)
		return 0
	default:
		fmt.Fprintf(stderr, "zj: unknown command %q\n\n%s", args[0], usage)
		return 2
	}
}

func versionString() string {
	rev := "unknown"
	if bi, ok := debug.ReadBuildInfo(); ok {
		for _, s := range bi.Settings {
			if s.Key == "vcs.revision" {
				rev = s.Value
			}
		}
	}
	return fmt.Sprintf("zj 0.2.0 (rev %s, %s, %s/%s)", rev, runtime.Version(), runtime.GOOS, runtime.GOARCH)
}

// findRepoRoot walks up from dir until it finds config/bench.yaml.
func findRepoRoot(dir string) (string, error) {
	dir, err := filepath.Abs(dir)
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "config", "bench.yaml")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("repo root not found (no config/bench.yaml in any parent dir); use --repo")
		}
		dir = parent
	}
}

func cmdDoctor(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("doctor", flag.ContinueOnError)
	fs.SetOutput(stderr)
	repo := fs.String("repo", "", "repository root (default: search upwards from the working dir)")
	asJSON := fs.Bool("json", false, "print JSON instead of a table")
	gpuTest := fs.Bool("gpu-test", false, "also run the pinned CUDA image with --gpus all")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	root := *repo
	if root == "" {
		var err error
		if root, err = findRepoRoot("."); err != nil {
			fmt.Fprintln(stderr, "zj doctor:", err)
			return 2
		}
	}
	bench, err := config.LoadBench(filepath.Join(root, "config", "bench.yaml"))
	if err != nil {
		fmt.Fprintln(stderr, "zj doctor:", err)
		return 2
	}
	checks := doctor.Run(ctx, doctor.SystemDeps(), doctor.Options{RepoRoot: root, Bench: bench, GPUTest: *gpuTest})
	if _, err := config.LoadContestants(filepath.Join(root, "config", "contestants.yaml"), bench); err != nil {
		checks = append(checks, doctor.Check{Name: "contestants.yaml", Status: doctor.Fail, Value: err.Error()})
	} else {
		checks = append(checks, doctor.Check{Name: "contestants.yaml", Status: doctor.OK, Value: "valid"})
	}

	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(checks); err != nil {
			fmt.Fprintln(stderr, "zj doctor:", err)
			return 2
		}
	} else {
		tw := tabwriter.NewWriter(stdout, 0, 0, 2, ' ', 0)
		for _, c := range checks {
			fmt.Fprintf(tw, "%s\t%s\t%s\n", c.Status, c.Name, c.Value)
			if c.Hint != "" {
				fmt.Fprintf(tw, "\t\t  ↳ %s\n", c.Hint)
			}
		}
		_ = tw.Flush()
	}
	if doctor.HasFail(checks) {
		return 1
	}
	return 0
}
