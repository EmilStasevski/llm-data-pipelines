package main

import (
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/energy"
	"zeilenjoule/harness/internal/procs"
	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/internal/runner"
)

// project bundles configuration and measurement sources for subcommands.
type project struct {
	repo        string
	bench       *config.Bench
	contestants []config.Contestant
	tasks       map[string]config.Task
	gpu         *energy.NVMLGPU
	gpuInfo     *energy.GPUInfo
	zones       []energy.RAPLZone
	log         *slog.Logger
}

func loadProject(repoFlag string, stderr io.Writer) (*project, error) {
	root := repoFlag
	if root == "" {
		var err error
		if root, err = findRepoRoot("."); err != nil {
			return nil, err
		}
	}
	cfg := filepath.Join(root, "config")
	bench, err := config.LoadBench(filepath.Join(cfg, "bench.yaml"))
	if err != nil {
		return nil, err
	}
	cs, err := config.LoadContestants(filepath.Join(cfg, "contestants.yaml"), bench)
	if err != nil {
		return nil, err
	}
	tasks, err := config.LoadTasks(filepath.Join(cfg, "tasks.yaml"))
	if err != nil {
		return nil, err
	}
	if err := config.CheckTasksExist(cs, tasks); err != nil {
		return nil, err
	}
	return &project{
		repo: root, bench: bench, contestants: cs, tasks: tasks,
		log: slog.New(slog.NewTextHandler(stderr, &slog.HandlerOptions{Level: slog.LevelInfo})),
	}, nil
}

// openSources opens NVML and discovers readable RAPL zones. Missing sources are logged loudly:
// the affected domains are recorded as null ("nicht gemessen / not measured"), never as 0.
func (p *project) openSources() {
	g, err := energy.OpenNVML(p.bench.GPUIndex)
	if err != nil {
		p.log.Warn("GPU energy NOT measured", "err", err)
	} else {
		p.gpu = g
		info, ierr := g.Info()
		if ierr != nil {
			p.log.Warn("incomplete GPU info", "err", ierr)
		}
		p.gpuInfo = &info
	}
	zones, unreadable, err := energy.DiscoverRAPL(os.ReadFile, filepath.Glob)
	if err != nil {
		p.log.Warn("RAPL discovery failed: CPU energy NOT measured", "err", err)
	}
	for _, u := range unreadable {
		p.log.Warn("RAPL zone not readable: its domain is NOT measured (checklist C1)", "zone", u)
	}
	p.zones = zones
}

// energyProblems lists missing measurement sources (empty = GPU and CPU package energy available).
func (p *project) energyProblems() []string {
	var out []string
	if p.gpu == nil {
		out = append(out, "GPU energy (NVML) unavailable")
	}
	if !slices.ContainsFunc(p.zones, func(z energy.RAPLZone) bool { return z.Domain == "cpu_pkg" }) {
		out = append(out, "CPU package energy (RAPL energy_uj) not readable")
	}
	return out
}

func (p *project) close() {
	if p.gpu != nil {
		_ = p.gpu.Close()
	}
}

func (p *project) runner(store bool) (*runner.Runner, error) {
	r := &runner.Runner{
		Repo: p.repo, Bench: p.bench, Tasks: p.tasks, Zones: p.zones,
		ReadFile: os.ReadFile, Tree: procs.FS{ReadFile: os.ReadFile, Glob: filepath.Glob}, Log: p.log,
	}
	if p.gpu != nil {
		r.GPU = p.gpu
	}
	r.Host = runner.CollectEnv(p.repo, os.ReadFile, p.gpuInfo, p.zones)
	if store {
		s, err := results.NewStore(filepath.Join(p.repo, "results", "runs.jsonl"))
		if err != nil {
			return nil, err
		}
		r.Store = s
	}
	return r, nil
}

func (p *project) contestantMap() map[string]config.Contestant {
	m := make(map[string]config.Contestant, len(p.contestants))
	for _, c := range p.contestants {
		m[c.ID] = c
	}
	return m
}

func fail(stderr io.Writer, cmd string, err error) int {
	fmt.Fprintf(stderr, "zj %s: %v\n", cmd, err)
	return 1
}
