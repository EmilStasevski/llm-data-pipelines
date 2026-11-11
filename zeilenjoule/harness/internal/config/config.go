// Package config loads and validates config/bench.yaml and config/contestants.yaml.
package config

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"go.yaml.in/yaml/v3"
)

// Bench mirrors config/bench.yaml (PLAN §15.1).
type Bench struct {
	Reps         int            `yaml:"reps"`
	Warmup       int            `yaml:"warmup"`
	TimeoutS     int            `yaml:"timeout_s"`
	SampleHz     float64        `yaml:"sample_hz"`
	Cooldown     Cooldown       `yaml:"cooldown"`
	IdleBaseline IdleBaseline   `yaml:"idle_baseline"`
	LoadGuard    LoadGuard      `yaml:"load_guard"`
	Interleave   bool           `yaml:"interleave"`
	Seed         int64          `yaml:"seed"`
	Scales       map[string]int `yaml:"scales"`
	Energy       Energy         `yaml:"energy"`
	VRAMBudgetGB float64        `yaml:"vram_budget_gb"`
	GPUIndex     int            `yaml:"gpu_index"`
	Runs         Runs           `yaml:"runs"`
	Doctor       Doctor         `yaml:"doctor"`
}

// Runs controls per-run files and process supervision.
type Runs struct {
	OutputDir   string  `yaml:"output_dir"`
	KeepOutputs string  `yaml:"keep_outputs"`
	TreePollHz  float64 `yaml:"tree_poll_hz"`
	SettleS     float64 `yaml:"settle_s"`
}

// OutputDirAbs expands a leading ~ in Runs.OutputDir.
func (r Runs) OutputDirAbs() (string, error) {
	if rest, ok := strings.CutPrefix(r.OutputDir, "~/"); ok {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		return filepath.Join(home, rest), nil
	}
	return r.OutputDir, nil
}

type Cooldown struct {
	GPUTempC float64 `yaml:"gpu_temp_c"`
	MaxWaitS int     `yaml:"max_wait_s"`
}

type IdleBaseline struct {
	DurationS int `yaml:"duration_s"`
	EveryMin  int `yaml:"every_min"`
}

// LoadGuard: CPU busy fraction over window_s must be <= max_busy_pct before a run (ADR 0007).
type LoadGuard struct {
	MaxBusyPct float64 `yaml:"max_busy_pct"`
	WindowS    float64 `yaml:"window_s"`
	MaxWaitS   int     `yaml:"max_wait_s"`
}

type Energy struct {
	PriceEURPerKWh map[string]float64 `yaml:"price_eur_per_kwh"`
	CO2GPerKWh     float64            `yaml:"co2_g_per_kwh"`
	WallMeter      WallMeter          `yaml:"wall_meter"`
}

type WallMeter struct {
	Enabled bool   `yaml:"enabled"`
	Kind    string `yaml:"kind"`
	URL     string `yaml:"url"`
}

type Doctor struct {
	MinFreeGB    float64 `yaml:"min_free_gb"`
	GPUTestImage string  `yaml:"gpu_test_image"`
}

// Contestant is one entry of config/contestants.yaml (PLAN §5.3).
type Contestant struct {
	ID          string   `yaml:"id"`
	Track       string   `yaml:"track"`
	Task        string   `yaml:"task"`
	Build       []string `yaml:"build"`
	VersionsCmd []string `yaml:"versions_cmd"`
	Cmd         []string `yaml:"cmd"`
	Args        []string `yaml:"args"`
	NeedsGPU    bool     `yaml:"needs_gpu"`
	Servers     []string `yaml:"servers"`
	Scales      []string `yaml:"scales"`
	AntiPattern bool     `yaml:"anti_pattern"`
	// TimeoutS overrides bench.yaml timeout_s for this contestant (0 = use the global value), e.g. the Track E
	// end-to-end pipelines on 1 M rows run far longer than any single-stage contestant.
	TimeoutS int `yaml:"timeout_s"`
}

// Timeout returns the contestant's own limit or the global one.
func (c Contestant) Timeout(global int) int {
	if c.TimeoutS > 0 {
		return c.TimeoutS
	}
	return global
}

var (
	idRE     = regexp.MustCompile(`^([a-ex])[0-9]{2}_[a-z0-9]+(_[a-z0-9]+)*$`)
	taskRE   = regexp.MustCompile(`^[a-ex]\.[a-z_]+$`)
	placeRE  = regexp.MustCompile(`\{([a-z_]*)\}`)
	allowed  = []string{"input", "output", "threads", "rows", "scale"}
	errValid = errors.New("invalid config")
)

func decodeStrict(path string, v any) error {
	b, err := os.ReadFile(path) //nolint:gosec // G304: path is the operator-supplied config file
	if err != nil {
		return err
	}
	dec := yaml.NewDecoder(bytes.NewReader(b))
	dec.KnownFields(true) // typos in keys are errors, not silently ignored
	if err := dec.Decode(v); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return nil
}

// LoadBench reads and validates bench.yaml.
func LoadBench(path string) (*Bench, error) {
	var b Bench
	if err := decodeStrict(path, &b); err != nil {
		return nil, err
	}
	if err := b.Validate(); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return &b, nil
}

// Validate checks value ranges; it collects all problems instead of stopping at the first.
func (b *Bench) Validate() error {
	var errs []string
	check := func(cond bool, msg string) {
		if !cond {
			errs = append(errs, msg)
		}
	}
	check(b.Reps >= 1, "reps must be >= 1")
	check(b.Warmup >= 0, "warmup must be >= 0")
	check(b.TimeoutS > 0, "timeout_s must be > 0")
	check(b.SampleHz > 0 && b.SampleHz <= 1000, "sample_hz must be in (0, 1000]")
	check(b.IdleBaseline.DurationS > 0, "idle_baseline.duration_s must be > 0")
	check(b.LoadGuard.MaxBusyPct > 0 && b.LoadGuard.MaxBusyPct <= 100, "load_guard.max_busy_pct must be in (0, 100]")
	check(b.LoadGuard.WindowS > 0, "load_guard.window_s must be > 0")
	check(b.LoadGuard.MaxWaitS > 0, "load_guard.max_wait_s must be > 0")
	check(b.GPUIndex >= 0, "gpu_index must be >= 0")
	check(b.Runs.OutputDir != "", "runs.output_dir must be set")
	check(slices.Contains([]string{"failed", "all", "none"}, b.Runs.KeepOutputs), "runs.keep_outputs must be failed|all|none")
	check(b.Runs.TreePollHz > 0, "runs.tree_poll_hz must be > 0")
	check(b.Runs.SettleS >= 0, "runs.settle_s must be >= 0")
	check(len(b.Scales) > 0, "scales must not be empty")
	for name, rows := range b.Scales {
		check(rows > 0, fmt.Sprintf("scales.%s must be > 0", name))
	}
	check(b.Energy.CO2GPerKWh > 0, "energy.co2_g_per_kwh must be > 0")
	for k, v := range b.Energy.PriceEURPerKWh {
		check(v > 0, fmt.Sprintf("energy.price_eur_per_kwh.%s must be > 0", k))
	}
	check(b.VRAMBudgetGB > 0, "vram_budget_gb must be > 0")
	check(b.Doctor.MinFreeGB >= 0, "doctor.min_free_gb must be >= 0")
	check(strings.Contains(b.Doctor.GPUTestImage, "@sha256:"), "doctor.gpu_test_image must be pinned by digest")
	if len(errs) > 0 {
		slices.Sort(errs)
		return fmt.Errorf("%w: %s", errValid, strings.Join(errs, "; "))
	}
	return nil
}

// LoadContestants reads contestants.yaml and validates every entry against the bench scales.
func LoadContestants(path string, bench *Bench) ([]Contestant, error) {
	var f struct {
		Contestants []Contestant `yaml:"contestants"`
	}
	if err := decodeStrict(path, &f); err != nil {
		return nil, err
	}
	if err := ValidateContestants(f.Contestants, bench); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return f.Contestants, nil
}

// ValidateContestants checks IDs, track/task consistency, placeholders and scales.
func ValidateContestants(cs []Contestant, bench *Bench) error {
	var errs []string
	seen := map[string]bool{}
	for i, c := range cs {
		where := fmt.Sprintf("contestants[%d] (%s)", i, c.ID)
		bad := func(msg string) { errs = append(errs, where+": "+msg) }
		m := idRE.FindStringSubmatch(c.ID)
		switch {
		case m == nil:
			bad("id must match " + idRE.String())
		case strings.ToUpper(m[1]) != c.Track:
			bad(fmt.Sprintf("track %q does not match id prefix %q", c.Track, m[1]))
		}
		if seen[c.ID] {
			bad("duplicate id")
		}
		seen[c.ID] = true
		if !taskRE.MatchString(c.Task) || !strings.EqualFold(c.Task[:1], c.Track) {
			bad(fmt.Sprintf("task %q must look like %s.<name>", c.Task, strings.ToLower(c.Track)))
		}
		if len(c.Cmd) == 0 {
			bad("cmd must not be empty")
		}
		for _, part := range slices.Concat(c.Cmd, c.Args, c.Build) {
			for _, ph := range placeRE.FindAllStringSubmatch(part, -1) {
				if !slices.Contains(allowed, ph[1]) {
					bad(fmt.Sprintf("unknown placeholder {%s} (allowed: %v)", ph[1], allowed))
				}
			}
		}
		if !slices.ContainsFunc(c.Args, func(a string) bool { return strings.Contains(a, "{output}") }) {
			bad("args must contain {output} (CLI contract)")
		}
		if len(c.Scales) == 0 {
			bad("scales must not be empty")
		}
		if c.TimeoutS < 0 {
			bad("timeout_s must be >= 0 (0 = bench.yaml timeout_s)")
		}
		for _, s := range c.Scales {
			if _, ok := bench.Scales[s]; !ok {
				bad(fmt.Sprintf("scale %q not defined in bench.yaml", s))
			}
		}
	}
	if len(errs) > 0 {
		return fmt.Errorf("%w:\n  %s", errValid, strings.Join(errs, "\n  "))
	}
	return nil
}

// Task describes input, output and verification of a task (config/tasks.yaml).
type Task struct {
	Input     string   `yaml:"input"`
	Output    string   `yaml:"output"`
	Verifier  string   `yaml:"verifier"`   // builtin:<name> | cmd
	VerifyCmd []string `yaml:"verify_cmd"` // for verifier: cmd; placeholders {output} {reference} {scale}
	Reference string   `yaml:"reference"`  // reference output path template ({scale}), relative to the repo
	NoRows    bool     `yaml:"no_rows"`
}

// LoadTasks reads tasks.yaml.
func LoadTasks(path string) (map[string]Task, error) {
	var f struct {
		Tasks map[string]Task `yaml:"tasks"`
	}
	if err := decodeStrict(path, &f); err != nil {
		return nil, err
	}
	var errs []string
	for name, t := range f.Tasks {
		if !taskRE.MatchString(name) {
			errs = append(errs, fmt.Sprintf("task %q: name must match %s", name, taskRE))
		}
		if t.Output == "" || strings.ContainsAny(t.Output, "/\\") {
			errs = append(errs, fmt.Sprintf("task %q: output must be a plain file name", name))
		}
		switch {
		case strings.HasPrefix(t.Verifier, "builtin:"):
		case t.Verifier == "cmd":
			if !slices.ContainsFunc(t.VerifyCmd, func(a string) bool { return strings.Contains(a, "{output}") }) {
				errs = append(errs, fmt.Sprintf("task %q: verify_cmd must pass {output}", name))
			}
		default:
			errs = append(errs, fmt.Sprintf("task %q: verifier must be builtin:<name> or cmd", name))
		}
	}
	if len(errs) > 0 {
		slices.Sort(errs)
		return nil, fmt.Errorf("%s: %w: %s", path, errValid, strings.Join(errs, "; "))
	}
	return f.Tasks, nil
}

// CheckTasksExist reports contestants whose task is not defined in tasks.yaml.
func CheckTasksExist(cs []Contestant, tasks map[string]Task) error {
	var missing []string
	for _, c := range cs {
		if _, ok := tasks[c.Task]; !ok {
			missing = append(missing, fmt.Sprintf("%s → %s", c.ID, c.Task))
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("%w: tasks not defined in tasks.yaml: %s", errValid, strings.Join(missing, ", "))
	}
	return nil
}

// FindContestant returns the contestant with id.
func FindContestant(cs []Contestant, id string) (Contestant, bool) {
	i := slices.IndexFunc(cs, func(c Contestant) bool { return c.ID == id })
	if i < 0 {
		return Contestant{}, false
	}
	return cs[i], true
}
