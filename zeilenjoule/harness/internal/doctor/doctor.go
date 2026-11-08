// Package doctor checks that the machine is in a known, measurable state (PLAN §4 Phase 0, §5.2).
// Every system access goes through Deps so the checks are unit-testable with fakes.
package doctor

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/cpustat"
)

// Status of one check. Fail makes `zj doctor` exit non-zero.
type Status string

const (
	OK   Status = "OK"
	Warn Status = "WARN"
	Fail Status = "FAIL"
)

// Check is one line of the doctor report.
type Check struct {
	Name   string `json:"name"`
	Status Status `json:"status"`
	Value  string `json:"value"`
	Hint   string `json:"hint,omitempty"`
}

// Deps abstracts the system so checks can be tested.
type Deps struct {
	ReadFile  func(path string) ([]byte, error)
	Glob      func(pattern string) ([]string, error)
	Run       func(ctx context.Context, name string, args ...string) (string, error)
	LookPath  func(file string) (string, error)
	FreeBytes func(path string) (uint64, error)
	Sleep     func(time.Duration)
}

// SystemDeps returns Deps backed by the real OS.
func SystemDeps() Deps {
	return Deps{
		ReadFile: os.ReadFile,
		Glob:     filepath.Glob,
		Run: func(ctx context.Context, name string, args ...string) (string, error) {
			//nolint:gosec // G204: command names/args are fixed in this package, not user input
			out, err := exec.CommandContext(ctx, name, args...).CombinedOutput()
			return strings.TrimSpace(string(out)), err
		},
		LookPath: exec.LookPath,
		Sleep:    time.Sleep,
		FreeBytes: func(path string) (uint64, error) {
			var st syscall.Statfs_t
			if err := syscall.Statfs(path, &st); err != nil {
				return 0, err
			}
			return st.Bavail * uint64(st.Bsize), nil //nolint:gosec // Bsize is positive on Linux
		},
	}
}

// Options control which checks run.
type Options struct {
	RepoRoot string
	Bench    *config.Bench
	GPUTest  bool // also run a GPU container (slow-ish, pulls the pinned image if missing)
}

// Run executes all checks in a fixed order.
func Run(ctx context.Context, d Deps, o Options) []Check {
	checks := []Check{
		checkOS(d), checkKernel(d), checkCPU(d), checkMemory(d),
		checkDisk(d, o), checkLoad(d, o), checkGovernor(d),
	}
	checks = append(checks, checkRAPL(d)...)
	checks = append(checks, checkGPU(ctx, d), checkDocker(ctx, d))
	if o.GPUTest {
		checks = append(checks, checkDockerGPU(ctx, d, o))
	}
	return append(checks, checkTools(ctx, d)...)
}

// HasFail reports whether any check failed.
func HasFail(cs []Check) bool {
	return slices.ContainsFunc(cs, func(c Check) bool { return c.Status == Fail })
}

// ---------- pure parsers ----------

// ParseOSRelease returns PRETTY_NAME from /etc/os-release content.
func ParseOSRelease(b []byte) string {
	for line := range strings.Lines(string(b)) {
		if v, ok := strings.CutPrefix(strings.TrimSpace(line), "PRETTY_NAME="); ok {
			return strings.Trim(v, `"`)
		}
	}
	return ""
}

// ParseCPUModel returns the first "model name" from /proc/cpuinfo and the number of processors.
func ParseCPUModel(b []byte) (model string, n int) {
	for line := range strings.Lines(string(b)) {
		k, v, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		switch strings.TrimSpace(k) {
		case "model name":
			if model == "" {
				model = strings.TrimSpace(v)
			}
		case "processor":
			n++
		}
	}
	return model, n
}

// ParseMemTotalKiB returns MemTotal in KiB from /proc/meminfo.
func ParseMemTotalKiB(b []byte) (uint64, error) {
	for line := range strings.Lines(string(b)) {
		if v, ok := strings.CutPrefix(line, "MemTotal:"); ok {
			f := strings.Fields(v)
			if len(f) == 0 {
				break
			}
			return strconv.ParseUint(f[0], 10, 64)
		}
	}
	return 0, errors.New("MemTotal not found")
}

// ParseLoadavg1 returns the 1-minute load average from /proc/loadavg.
func ParseLoadavg1(b []byte) (float64, error) {
	f := strings.Fields(string(b))
	if len(f) == 0 {
		return 0, errors.New("empty loadavg")
	}
	return strconv.ParseFloat(f[0], 64)
}

// SummarizeGovernors turns per-CPU governor values into "powersave×16" or a mixed listing.
func SummarizeGovernors(vals []string) (summary string, uniform bool) {
	counts := map[string]int{}
	for _, v := range vals {
		counts[strings.TrimSpace(v)]++
	}
	keys := slices.Sorted(func(yield func(string) bool) {
		for k := range counts {
			if !yield(k) {
				return
			}
		}
	})
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s×%d", k, counts[k]))
	}
	return strings.Join(parts, ", "), len(keys) == 1
}

// GPUInfo is one row of the nvidia-smi query.
type GPUInfo struct {
	Name, ComputeCap, Driver, Persistence, PowerLimitW, MemoryMiB string
}

// ParseNvidiaSmi parses `--query-gpu=name,compute_cap,driver_version,persistence_mode,power.limit,
// memory.total --format=csv,noheader,nounits` output (first GPU).
func ParseNvidiaSmi(out string) (GPUInfo, error) {
	line, _, _ := strings.Cut(strings.TrimSpace(out), "\n")
	f := strings.Split(line, ",")
	if len(f) != 6 {
		return GPUInfo{}, fmt.Errorf("unexpected nvidia-smi output %q", line)
	}
	for i := range f {
		f[i] = strings.TrimSpace(f[i])
	}
	return GPUInfo{f[0], f[1], f[2], f[3], f[4], f[5]}, nil
}

// IsSnapDockerRoot reports whether the Docker data root belongs to the snap package (ADR 0001).
func IsSnapDockerRoot(root string) bool {
	return strings.HasPrefix(root, "/var/snap/") || strings.HasPrefix(root, "/snap/")
}

// ---------- checks ----------

func readTrim(d Deps, path string) (string, error) {
	b, err := d.ReadFile(path)
	return strings.TrimSpace(string(b)), err
}

func checkOS(d Deps) Check {
	b, err := d.ReadFile("/etc/os-release")
	if err != nil {
		return Check{Name: "os", Status: Warn, Value: err.Error()}
	}
	return Check{Name: "os", Status: OK, Value: ParseOSRelease(b)}
}

func checkKernel(d Deps) Check {
	v, err := readTrim(d, "/proc/sys/kernel/osrelease")
	if err != nil {
		return Check{Name: "kernel", Status: Warn, Value: err.Error()}
	}
	return Check{Name: "kernel", Status: OK, Value: v}
}

func checkCPU(d Deps) Check {
	b, err := d.ReadFile("/proc/cpuinfo")
	if err != nil {
		return Check{Name: "cpu", Status: Warn, Value: err.Error()}
	}
	model, n := ParseCPUModel(b)
	return Check{Name: "cpu", Status: OK, Value: fmt.Sprintf("%s, %d logical CPUs", model, n)}
}

func checkMemory(d Deps) Check {
	b, err := d.ReadFile("/proc/meminfo")
	if err != nil {
		return Check{Name: "memory", Status: Warn, Value: err.Error()}
	}
	kib, err := ParseMemTotalKiB(b)
	if err != nil {
		return Check{Name: "memory", Status: Warn, Value: err.Error()}
	}
	return Check{Name: "memory", Status: OK, Value: fmt.Sprintf("%.1f GiB", float64(kib)/(1<<20))}
}

func checkDisk(d Deps, o Options) Check {
	free, err := d.FreeBytes(o.RepoRoot)
	if err != nil {
		return Check{Name: "disk_free", Status: Warn, Value: err.Error()}
	}
	gb := float64(free) / 1e9
	c := Check{Name: "disk_free", Status: OK, Value: fmt.Sprintf("%.0f GB free at %s", gb, o.RepoRoot)}
	if gb < o.Bench.Doctor.MinFreeGB {
		c.Status, c.Hint = Warn, fmt.Sprintf("below doctor.min_free_gb=%.0f; XL data + models need space", o.Bench.Doctor.MinFreeGB)
	}
	return c
}

// checkLoad measures the CPU busy fraction over 1 s, the load-guard signal (ADR 0007);
// loadavg1 is shown for information only.
func checkLoad(d Deps, o Options) Check {
	a, err := cpustat.Read(d.ReadFile)
	if err != nil {
		return Check{Name: "cpu_busy", Status: Warn, Value: err.Error()}
	}
	d.Sleep(time.Second)
	b, err := cpustat.Read(d.ReadFile)
	if err != nil {
		return Check{Name: "cpu_busy", Status: Warn, Value: err.Error()}
	}
	busy := cpustat.BusyPct(a, b)
	load := "?"
	if lb, err := d.ReadFile("/proc/loadavg"); err == nil {
		if l, err := ParseLoadavg1(lb); err == nil {
			load = fmt.Sprintf("%.2f", l)
		}
	}
	c := Check{Name: "cpu_busy", Status: OK, Value: fmt.Sprintf("%.1f %% over 1 s (guard %.1f %%), loadavg1 %s", busy, o.Bench.LoadGuard.MaxBusyPct, load)}
	if math.IsNaN(busy) || busy > o.Bench.LoadGuard.MaxBusyPct {
		c.Status, c.Hint = Warn, "machine busy: measured runs will wait (stop k3s/other workloads, checklist C5/C6)"
	}
	return c
}

func checkGovernor(d Deps) Check {
	paths, _ := d.Glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor")
	vals := make([]string, 0, len(paths))
	for _, p := range paths {
		if v, err := readTrim(d, p); err == nil {
			vals = append(vals, v)
		}
	}
	if len(vals) == 0 {
		return Check{Name: "cpu_governor", Status: Warn, Value: "not readable"}
	}
	summary, uniform := SummarizeGovernors(vals)
	if epp, err := readTrim(d, "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"); err == nil {
		summary += ", EPP " + epp
	}
	c := Check{Name: "cpu_governor", Status: OK, Value: summary}
	if !uniform {
		c.Status, c.Hint = Warn, "mixed governors across CPUs; ADR 0003 requires one governor per session"
	}
	return c
}

func checkRAPL(d Deps) []Check {
	zones, _ := d.Glob("/sys/class/powercap/intel-rapl:*")
	slices.Sort(zones)
	if len(zones) == 0 {
		return []Check{{Name: "rapl", Status: Warn, Value: "no RAPL zones", Hint: "CPU energy will be 'not measured'"}}
	}
	out := make([]Check, 0, len(zones))
	for _, z := range zones {
		name, _ := readTrim(d, z+"/name")
		c := Check{Name: "rapl " + name, Status: OK, Value: filepath.Base(z) + " energy_uj readable"}
		if _, err := d.ReadFile(z + "/energy_uj"); err != nil {
			c.Status, c.Value = Warn, filepath.Base(z)+" energy_uj NOT readable"
			if errors.Is(err, fs.ErrPermission) {
				c.Hint = "root-only: CPU energy = 'not measured' until checklist C1 (chmod o+r / udev rule)"
			}
		}
		out = append(out, c)
	}
	return out
}

func checkGPU(ctx context.Context, d Deps) Check {
	if _, err := d.LookPath("nvidia-smi"); err != nil {
		return Check{Name: "gpu", Status: Fail, Value: "nvidia-smi not found"}
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	out, err := d.Run(ctx, "nvidia-smi",
		"--query-gpu=name,compute_cap,driver_version,persistence_mode,power.limit,memory.total",
		"--format=csv,noheader,nounits")
	if err != nil {
		return Check{Name: "gpu", Status: Fail, Value: fmt.Sprintf("nvidia-smi: %v: %s", err, out)}
	}
	g, err := ParseNvidiaSmi(out)
	if err != nil {
		return Check{Name: "gpu", Status: Fail, Value: err.Error()}
	}
	c := Check{Name: "gpu", Status: OK, Value: fmt.Sprintf("%s, cc %s, driver %s, %s MiB, limit %s W, persistence %s",
		g.Name, g.ComputeCap, g.Driver, g.MemoryMiB, g.PowerLimitW, g.Persistence)}
	if g.Persistence != "Enabled" {
		c.Status, c.Hint = Warn, "persistence mode off: idle power/init time less stable (checklist C2: sudo nvidia-smi -pm 1)"
	}
	return c
}

func checkDocker(ctx context.Context, d Deps) Check {
	if _, err := d.LookPath("docker"); err != nil {
		return Check{Name: "docker", Status: Fail, Value: "docker not found"}
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	out, err := d.Run(ctx, "docker", "info", "--format",
		"{{.ServerVersion}}|{{.DockerRootDir}}|{{range $k, $v := .Runtimes}}{{$k}},{{end}}")
	if err != nil {
		return Check{Name: "docker", Status: Fail, Value: fmt.Sprintf("docker info: %v: %s", err, out)}
	}
	f := strings.SplitN(out, "|", 3)
	if len(f) != 3 {
		return Check{Name: "docker", Status: Fail, Value: "unexpected docker info output: " + out}
	}
	c := Check{Name: "docker", Status: OK, Value: fmt.Sprintf("server %s, root %s, runtimes %s", f[0], f[1], strings.TrimSuffix(f[2], ","))}
	switch {
	case IsSnapDockerRoot(f[1]):
		c.Status, c.Hint = Fail, "snap Docker daemon is active: --gpus all and /tmp mounts break (ADR 0001)"
	case !slices.Contains(strings.Split(f[2], ","), "nvidia"):
		c.Status, c.Hint = Warn, "nvidia runtime not configured: sudo nvidia-ctk runtime configure --runtime=docker"
	}
	return c
}

func checkDockerGPU(ctx context.Context, d Deps, o Options) Check {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	out, err := d.Run(ctx, "docker", "run", "--rm", "--gpus", "all", o.Bench.Doctor.GPUTestImage, "nvidia-smi", "-L")
	if err != nil || !strings.Contains(out, "GPU 0:") {
		return Check{Name: "docker_gpu", Status: Fail, Value: fmt.Sprintf("--gpus all failed: %v: %s", err, lastLine(out))}
	}
	return Check{Name: "docker_gpu", Status: OK, Value: lastLine(out)}
}

type tool struct {
	name     string
	args     []string
	required bool
	hint     string
}

var tools = []tool{
	{name: "uv", args: []string{"--version"}, required: true},
	{name: "cargo", args: []string{"--version"}, required: true},
	{name: "rustc", args: []string{"--version"}, required: true},
	{name: "go", args: []string{"version"}, required: true},
	{name: "task", args: []string{"--version"}, hint: "go install github.com/go-task/task/v3/cmd/task@latest"},
	{name: "duckdb", args: []string{"-no-agent", "--version"}},
	{name: "golangci-lint", args: []string{"version"}, hint: "go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest"},
	{name: "govulncheck", args: []string{"-version"}, hint: "go install golang.org/x/vuln/cmd/govulncheck@latest"},
	{name: "stress-ng", args: []string{"--version"}, hint: "needed for calibration (checklist C3: sudo apt install stress-ng)"},
}

func checkTools(ctx context.Context, d Deps) []Check {
	out := make([]Check, 0, len(tools))
	for _, t := range tools {
		name := "tool " + t.name
		if _, err := d.LookPath(t.name); err != nil {
			st := Warn
			if t.required {
				st = Fail
			}
			out = append(out, Check{Name: name, Status: st, Value: "not found", Hint: t.hint})
			continue
		}
		tctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		v, err := d.Run(tctx, t.name, t.args...)
		cancel()
		if err != nil {
			out = append(out, Check{Name: name, Status: Warn, Value: fmt.Sprintf("version query failed: %v", err)})
			continue
		}
		first, _, _ := strings.Cut(v, "\n")
		out = append(out, Check{Name: name, Status: OK, Value: first})
	}
	return out
}

func lastLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.LastIndexByte(s, '\n'); i >= 0 {
		return s[i+1:]
	}
	return s
}
