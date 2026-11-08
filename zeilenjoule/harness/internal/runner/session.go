package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/results"
)

// Manifest describes a session; saved to results/sessions/<id>.json so `--session` can resume it.
type Manifest struct {
	SessionID   string   `json:"session_id"`
	Created     string   `json:"created"`
	Seed        int64    `json:"seed"`
	Reps        int      `json:"reps"`
	Warmup      int      `json:"warmup"`
	Interleave  bool     `json:"interleave"`
	Threads     int      `json:"threads"`
	Scales      []string `json:"scales"`
	Contestants []string `json:"contestants"`
	Items       []Item   `json:"items"`
	Args        []string `json:"args"`
}

func manifestPath(repo, id string) string {
	return filepath.Join(repo, "results", "sessions", id+".json")
}

// SaveManifest writes the manifest (refuses to overwrite an existing session).
func SaveManifest(repo string, m Manifest) error {
	p := manifestPath(repo, m.SessionID)
	if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
		return err
	}
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600) //nolint:gosec // G304: harness path
	if err != nil {
		return err
	}
	if _, err := f.Write(append(b, '\n')); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}

// LoadManifest reads a saved session.
func LoadManifest(repo, id string) (Manifest, error) {
	var m Manifest
	b, err := os.ReadFile(manifestPath(repo, id)) //nolint:gosec // G304: harness path
	if err != nil {
		return m, err
	}
	return m, json.Unmarshal(b, &m)
}

// Summary counts outcomes of a session invocation.
type Summary struct {
	Ran, Skipped int
	ByStatus     map[string]int
}

// SessionOpts configures RunSession.
type SessionOpts struct {
	Manifest    Manifest
	Contestants map[string]config.Contestant
	IdleStore   *results.Store
}

// runCapture runs a helper command (build, versions) outside any measured window.
func runCapture(ctx context.Context, dir string, argv []string, logPath string) (string, error) {
	if len(argv) == 0 {
		return "", errors.New("empty command")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...) //nolint:gosec // G204: registry commands
	cmd.Dir = dir
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if logPath != "" {
		_ = os.MkdirAll(filepath.Dir(logPath), 0o750)
		_ = os.WriteFile(logPath, []byte(fmt.Sprintf("# %q\n%s\n--- stderr ---\n%s", argv, stdout.String(), stderr.String())), 0o600)
	}
	if err != nil {
		return stdout.String(), fmt.Errorf("%q: %w: %s", argv, err, lastLine(stderr.String()))
	}
	return stdout.String(), nil
}

func lastLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.LastIndexByte(s, '\n'); i >= 0 {
		return s[i+1:]
	}
	return s
}

// parseVersions stores JSON output as JSON, anything else as a trimmed string.
func parseVersions(out string) any {
	var v any
	if err := json.Unmarshal([]byte(out), &v); err == nil {
		return v
	}
	return strings.TrimSpace(out)
}

// RunSession executes a manifest's items, skipping those already recorded for this session.
func (r *Runner) RunSession(ctx context.Context, o SessionOpts) (Summary, error) {
	m := o.Manifest
	sum := Summary{ByStatus: map[string]int{}}
	log := r.logger().With("session", m.SessionID)

	done := map[string]bool{}
	if r.Store != nil {
		recs, err := results.ReadRecords(r.Store.Path())
		if err != nil {
			return sum, fmt.Errorf("reading %s: %w", r.Store.Path(), err)
		}
		for _, rec := range recs {
			if rec.SessionID == m.SessionID {
				done[Item{rec.Contestant, rec.Scale, rec.Rep, rec.Warmup}.Key()] = true
			}
		}
	}

	// builds + version fingerprints: once per contestant, before anything is measured
	versions := map[string]any{}
	seen := map[string]bool{}
	for _, it := range m.Items {
		if seen[it.Contestant] {
			continue
		}
		seen[it.Contestant] = true
		c, ok := o.Contestants[it.Contestant]
		if !ok {
			return sum, fmt.Errorf("contestant %q not in contestants.yaml", it.Contestant)
		}
		logDir := filepath.Join(r.Repo, "logs", "builds", m.SessionID)
		if len(c.Build) > 0 {
			log.Info("build", "id", c.ID)
			if _, err := runCapture(ctx, r.Repo, c.Build, filepath.Join(logDir, c.ID+".build.log")); err != nil {
				return sum, fmt.Errorf("build %s: %w", c.ID, err)
			}
		}
		if len(c.VersionsCmd) > 0 {
			out, err := runCapture(ctx, r.Repo, c.VersionsCmd, filepath.Join(logDir, c.ID+".versions.log"))
			if err != nil {
				return sum, fmt.Errorf("versions %s: %w", c.ID, err)
			}
			versions[c.ID] = parseVersions(out)
		}
	}

	governor := currentGovernor(r.ReadFile)
	every := time.Duration(r.Bench.IdleBaseline.EveryMin) * time.Minute
	for i, it := range m.Items {
		if done[it.Key()] {
			sum.Skipped++
			continue
		}
		if g := currentGovernor(r.ReadFile); g != governor {
			return sum, fmt.Errorf("CPU governor changed during the session (%q → %q); ADR 0003 forbids mixing", governor, g)
		}
		if r.Idle == nil || time.Since(r.Idle.Taken) > every {
			log.Info("idle baseline", "duration_s", r.Bench.IdleBaseline.DurationS)
			var store func(IdleRecord) error
			if o.IdleStore != nil {
				store = func(rec IdleRecord) error { return o.IdleStore.Append(rec) }
			}
			idle, err := r.MeasureQuietIdle(ctx, m.SessionID, time.Duration(r.Bench.IdleBaseline.DurationS)*time.Second, 3, store)
			if err != nil {
				return sum, err
			}
			r.Idle = &idle
		}
		log.Info("item", "n", fmt.Sprintf("%d/%d", i+1, len(m.Items)), "id", it.Contestant, "scale", it.Scale, "rep", it.Rep, "warmup", it.Warmup)
		rec, err := r.Run(ctx, RunSpec{
			Contestant: o.Contestants[it.Contestant], Scale: it.Scale, Rep: it.Rep, Warmup: it.Warmup,
			SessionID: m.SessionID, Threads: m.Threads, Versions: versions[it.Contestant],
		})
		if err != nil {
			return sum, err
		}
		sum.Ran++
		sum.ByStatus[rec.Status]++
	}
	return sum, nil
}
