package runner

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"zeilenjoule/harness/internal/doctor"
	"zeilenjoule/harness/internal/energy"
	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/pkg/zjmark"
)

// HarnessVersion is recorded with every run.
const HarnessVersion = "zj 0.2.0"

func readStr(read func(string) ([]byte, error), p string) string {
	b, err := read(p)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func currentGovernor(read func(string) ([]byte, error)) string {
	paths, _ := filepath.Glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor")
	vals := make([]string, 0, len(paths))
	for _, p := range paths {
		if v := readStr(read, p); v != "" {
			vals = append(vals, v)
		}
	}
	s, _ := doctor.SummarizeGovernors(vals)
	if epp := readStr(read, "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"); epp != "" {
		s += ", EPP " + epp
	}
	return s
}

func fileSHA256(p string) string {
	b, err := os.ReadFile(p) //nolint:gosec // G304: repo config files
	if err != nil {
		return ""
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

// CollectEnv builds the static part of the environment fingerprint (once per session).
func CollectEnv(repo string, read func(string) ([]byte, error), gpuInfo *energy.GPUInfo, zones []energy.RAPLZone) results.Env {
	cpuModel, nCPU := doctor.ParseCPUModel([]byte(readStr(read, "/proc/cpuinfo")))
	zoneNames := make([]string, 0, len(zones))
	for _, z := range zones {
		zoneNames = append(zoneNames, z.Name+"→"+z.Domain)
	}
	host := map[string]any{
		"os":         doctor.ParseOSRelease([]byte(readStr(read, "/etc/os-release"))),
		"kernel":     readStr(read, "/proc/sys/kernel/osrelease"),
		"cpu":        cpuModel,
		"cpus":       nCPU,
		"smt":        readStr(read, "/sys/devices/system/cpu/smt/control"),
		"no_turbo":   readStr(read, "/sys/devices/system/cpu/intel_pstate/no_turbo"),
		"rapl_zones": zoneNames,
		"go":         runtime.Version(),
		"config_sha256": map[string]string{
			"bench.yaml":       fileSHA256(filepath.Join(repo, "config", "bench.yaml")),
			"contestants.yaml": fileSHA256(filepath.Join(repo, "config", "contestants.yaml")),
			"tasks.yaml":       fileSHA256(filepath.Join(repo, "config", "tasks.yaml")),
		},
	}
	env := results.Env{Host: host, Harness: HarnessVersion}
	if gpuInfo != nil {
		env.Driver, env.CUDA = gpuInfo.Driver, gpuInfo.CUDADriver
		env.GPU = map[string]any{
			"name": gpuInfo.Name, "uuid": gpuInfo.UUID, "power_limit_w": gpuInfo.PowerLimitW,
			"persistence": gpuInfo.Persistence, "max_sm_clock_mhz": gpuInfo.MaxSMClockMHz,
			"max_mem_clock_mhz": gpuInfo.MaxMemClockMHz, "mem_total_bytes": gpuInfo.MemTotalBytes,
		}
	}
	return env
}

// fingerprint hashes the stable parts of the environment (not load/temperature).
func fingerprint(e results.Env) string {
	s, err := zjmark.Canonical(map[string]any{
		"driver": e.Driver, "cuda": e.CUDA, "governor": e.Governor, "gpu": e.GPU,
		"host": e.Host, "versions": e.Versions, "harness": e.Harness,
	})
	if err != nil {
		return ""
	}
	h := sha256.Sum256([]byte(s))
	return "sha256:" + hex.EncodeToString(h[:])
}
