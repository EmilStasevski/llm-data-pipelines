package doctor

import (
	"context"
	"errors"
	"io/fs"
	"path"
	"slices"
	"strings"
	"testing"
	"time"

	"zeilenjoule/harness/internal/config"
)

// fakeSystem models the machine as found in Phase 0 (ENVIRONMENT.md), with knobs for failure modes.
type fakeSystem struct {
	files     map[string]string
	denied    map[string]bool
	cmds      map[string]string // "name arg0 arg1…" prefix → output
	cmdErr    map[string]error
	missing   map[string]bool
	freeBytes uint64
	statSeq   []string // successive /proc/stat contents (last one repeats)
	statN     int
}

func healthy() *fakeSystem {
	return &fakeSystem{
		files: map[string]string{
			"/etc/os-release":            "NAME=\"Ubuntu\"\nPRETTY_NAME=\"Ubuntu 24.04.5 LTS\"\n",
			"/proc/sys/kernel/osrelease": "6.17.0-1023-oem\n",
			"/proc/cpuinfo":              "processor\t: 0\nmodel name\t: Intel(R) Core(TM) i5-14400F\n\nprocessor\t: 1\nmodel name\t: Intel(R) Core(TM) i5-14400F\n",
			"/proc/meminfo":              "MemTotal:       32695260 kB\nMemFree: 1 kB\n",
			"/proc/loadavg":              "0.21 0.30 0.40 1/2000 12345\n",
			"/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor":              "powersave\n",
			"/sys/devices/system/cpu/cpu1/cpufreq/scaling_governor":              "powersave\n",
			"/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference": "power\n",
			"/sys/class/powercap/intel-rapl:0/name":                              "package-0\n",
			"/sys/class/powercap/intel-rapl:0/energy_uj":                         "123\n",
			"/sys/class/powercap/intel-rapl:0:0/name":                            "core\n",
			"/sys/class/powercap/intel-rapl:0:0/energy_uj":                       "45\n",
		},
		denied: map[string]bool{},
		cmds: map[string]string{
			"nvidia-smi":  "NVIDIA GeForce RTX 5060 Ti, 12.0, 580.178.04, Enabled, 180.00, 16311",
			"docker info": "29.9.3|/var/lib/docker|io.containerd.runc.v2,nvidia,runc,",
			"docker run":  "Unable to find image…\nGPU 0: NVIDIA GeForce RTX 5060 Ti (UUID: GPU-x)",
			"uv":          "uv 0.12.21",
			"go":          "go version go1.27.3 linux/amd64",
		},
		cmdErr:    map[string]error{},
		missing:   map[string]bool{},
		freeBytes: 232e9,
		statSeq:   []string{"cpu  1000 0 0 9000 0 0 0 0\n", "cpu  1020 0 0 9980 0 0 0 0\n"}, // 2 % busy
	}
}

func (f *fakeSystem) deps() Deps {
	return Deps{
		ReadFile: func(p string) ([]byte, error) {
			if p == "/proc/stat" && len(f.statSeq) > 0 {
				s := f.statSeq[min(f.statN, len(f.statSeq)-1)]
				f.statN++
				return []byte(s), nil
			}
			if f.denied[p] {
				return nil, &fs.PathError{Op: "open", Path: p, Err: fs.ErrPermission}
			}
			if s, ok := f.files[p]; ok {
				return []byte(s), nil
			}
			return nil, &fs.PathError{Op: "open", Path: p, Err: fs.ErrNotExist}
		},
		Glob: func(pattern string) ([]string, error) {
			seen := map[string]bool{}
			for p := range f.files {
				// match the pattern against p or any parent dir of p
				for q := p; q != "/" && q != "."; q = path.Dir(q) {
					if ok, _ := path.Match(pattern, q); ok {
						seen[q] = true
					}
				}
			}
			return slices.Sorted(func(yield func(string) bool) {
				for k := range seen {
					if !yield(k) {
						return
					}
				}
			}), nil
		},
		Run: func(_ context.Context, name string, args ...string) (string, error) {
			full := strings.Join(append([]string{name}, args...), " ")
			for prefix, err := range f.cmdErr {
				if strings.HasPrefix(full, prefix) {
					return "boom", err
				}
			}
			best := ""
			for prefix := range f.cmds {
				if strings.HasPrefix(full, prefix) && len(prefix) > len(best) {
					best = prefix
				}
			}
			if best == "" {
				return name + " 1.0", nil
			}
			return f.cmds[best], nil
		},
		LookPath: func(file string) (string, error) {
			if f.missing[file] {
				return "", errors.New("not found")
			}
			return "/usr/bin/" + file, nil
		},
		FreeBytes: func(string) (uint64, error) { return f.freeBytes, nil },
		Sleep:     func(time.Duration) {},
	}
}

func opts(gpuTest bool) Options {
	return Options{
		RepoRoot: "/repo",
		Bench: &config.Bench{
			LoadGuard: config.LoadGuard{MaxBusyPct: 5, WindowS: 1},
			Doctor:    config.Doctor{MinFreeGB: 200, GPUTestImage: "nvidia/cuda:x@sha256:abc"},
		},
		GPUTest: gpuTest,
	}
}

func byName(cs []Check) map[string]Check {
	m := map[string]Check{}
	for _, c := range cs {
		m[c.Name] = c
	}
	return m
}

func TestHealthyMachineAllOK(t *testing.T) {
	cs := Run(context.Background(), healthy().deps(), opts(true))
	for _, c := range cs {
		if c.Status != OK {
			t.Errorf("%s: %s %q (%s)", c.Name, c.Status, c.Value, c.Hint)
		}
	}
	m := byName(cs)
	if got := m["cpu_governor"].Value; got != "powersave×2, EPP power" {
		t.Errorf("governor summary = %q", got)
	}
	if !strings.Contains(m["cpu"].Value, "i5-14400F, 2 logical CPUs") {
		t.Errorf("cpu = %q", m["cpu"].Value)
	}
	if _, ok := m["rapl core"]; !ok {
		t.Errorf("RAPL subzone missing: %v", m)
	}
}

// The machine exactly as audited in Phase 0: snap docker, RAPL root-only, busy, persistence off.
func TestPhase0MachineState(t *testing.T) {
	f := healthy()
	f.cmds["docker info"] = "29.8.0|/var/snap/docker/common/var-lib-docker|io.containerd.runc.v2,nvidia,runc,"
	f.cmds["nvidia-smi"] = "NVIDIA GeForce RTX 5060 Ti, 12.0, 580.178.04, Disabled, 180.00, 16311"
	f.files["/proc/loadavg"] = "8.35 6.67 5.19 34/2249 307397\n"
	f.statSeq = []string{"cpu  1000 0 0 9000 0 0 0 0\n", "cpu  1500 0 0 9500 0 0 0 0\n"} // 50 % busy
	f.denied["/sys/class/powercap/intel-rapl:0/energy_uj"] = true
	f.missing["stress-ng"] = true
	f.cmdErr["docker run"] = errors.New("exit status 125")

	cs := Run(context.Background(), f.deps(), opts(true))
	m := byName(cs)
	want := map[string]Status{
		"docker": Fail, "docker_gpu": Fail, "gpu": Warn, "cpu_busy": Warn,
		"rapl package-0": Warn, "rapl core": OK, "tool stress-ng": Warn,
	}
	for name, st := range want {
		if m[name].Status != st {
			t.Errorf("%s: want %s, got %s (%q)", name, st, m[name].Status, m[name].Value)
		}
	}
	if !strings.Contains(m["rapl package-0"].Hint, "C1") {
		t.Errorf("RAPL permission hint should point to checklist C1: %q", m["rapl package-0"].Hint)
	}
	if !HasFail(cs) {
		t.Error("HasFail should be true")
	}
}

func TestRequiredToolMissingFails(t *testing.T) {
	f := healthy()
	f.missing["uv"] = true
	m := byName(Run(context.Background(), f.deps(), opts(false)))
	if m["tool uv"].Status != Fail {
		t.Errorf("missing uv must FAIL, got %+v", m["tool uv"])
	}
	if _, ran := m["docker_gpu"]; ran {
		t.Error("docker_gpu must only run with GPUTest")
	}
}

func TestLowDiskWarns(t *testing.T) {
	f := healthy()
	f.freeBytes = 50e9
	if c := byName(Run(context.Background(), f.deps(), opts(false)))["disk_free"]; c.Status != Warn {
		t.Errorf("want WARN, got %+v", c)
	}
}

func TestParsers(t *testing.T) {
	if got := ParseOSRelease([]byte("ID=ubuntu\nPRETTY_NAME=\"Ubuntu 26.04 LTS\"\n")); got != "Ubuntu 26.04 LTS" {
		t.Errorf("ParseOSRelease = %q", got)
	}
	if _, err := ParseMemTotalKiB([]byte("MemFree: 1 kB\n")); err == nil {
		t.Error("missing MemTotal must error")
	}
	if l, err := ParseLoadavg1([]byte("1.50 0 0 1/1 1")); err != nil || l != 1.5 {
		t.Errorf("ParseLoadavg1 = %v, %v", l, err)
	}
	if _, err := ParseLoadavg1(nil); err == nil {
		t.Error("empty loadavg must error")
	}
	if s, uniform := SummarizeGovernors([]string{"performance", "powersave", "powersave"}); uniform || s != "performance×1, powersave×2" {
		t.Errorf("SummarizeGovernors = %q, %v", s, uniform)
	}
	if _, err := ParseNvidiaSmi("garbage"); err == nil {
		t.Error("bad nvidia-smi output must error")
	}
	for root, snap := range map[string]bool{"/var/lib/docker": false, "/var/snap/docker/common/var-lib-docker": true} {
		if IsSnapDockerRoot(root) != snap {
			t.Errorf("IsSnapDockerRoot(%q) != %v", root, snap)
		}
	}
}
