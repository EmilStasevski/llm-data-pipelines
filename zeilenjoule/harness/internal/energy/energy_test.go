package energy

import (
	"context"
	"errors"
	"io/fs"
	"math"
	"os"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

func near(t *testing.T, name string, got, want, tol float64) {
	t.Helper()
	if math.Abs(got-want) > tol {
		t.Errorf("%s = %.6f, want %.6f ± %g", name, got, want, tol)
	}
}

func sec(s float64) time.Duration { return time.Duration(s * float64(time.Second)) }

func TestWrapDelta(t *testing.T) {
	const maxR = 1000
	cases := []struct{ prev, cur, want uint64 }{
		{100, 150, 50},
		{150, 150, 0},
		{990, 10, 21}, // 990→1000 is 10, wrap to 0 is +1, then +10
		{0, maxR, maxR},
	}
	for _, c := range cases {
		if got := WrapDelta(c.prev, c.cur, maxR); got != c.want {
			t.Errorf("WrapDelta(%d,%d) = %d, want %d", c.prev, c.cur, got, c.want)
		}
	}
}

func TestDomainForZone(t *testing.T) {
	for name, want := range map[string]string{
		"package-0": "cpu_pkg", "package-1": "cpu_pkg", "core": "cpu_core", "dram": "dram",
		"uncore": "cpu_uncore", "psys": "psys", "weird": "rapl_weird",
	} {
		if got := DomainForZone(name); got != want {
			t.Errorf("DomainForZone(%q) = %q, want %q", name, got, want)
		}
	}
}

type fakeFS map[string]string

func (f fakeFS) read(p string) ([]byte, error) {
	v, ok := f[p]
	switch {
	case !ok:
		return nil, fs.ErrNotExist
	case v == "EACCES":
		return nil, fs.ErrPermission
	}
	return []byte(v), nil
}

func (f fakeFS) glob(string) ([]string, error) {
	return []string{"/sys/class/powercap/intel-rapl:0:0", "/sys/class/powercap/intel-rapl:0", "/sys/class/powercap/intel-rapl:1"}, nil
}

func TestDiscoverRAPL(t *testing.T) {
	f := fakeFS{
		"/sys/class/powercap/intel-rapl:0/name":                  "package-0\n",
		"/sys/class/powercap/intel-rapl:0/energy_uj":             "123\n",
		"/sys/class/powercap/intel-rapl:0/max_energy_range_uj":   "262143328850\n",
		"/sys/class/powercap/intel-rapl:0:0/name":                "core\n",
		"/sys/class/powercap/intel-rapl:0:0/energy_uj":           "EACCES",
		"/sys/class/powercap/intel-rapl:0:0/max_energy_range_uj": "262143328850\n",
		// intel-rapl:1 has no name file → skipped silently
	}
	zones, unreadable, err := DiscoverRAPL(f.read, f.glob)
	if err != nil {
		t.Fatal(err)
	}
	if len(zones) != 1 || zones[0].Domain != "cpu_pkg" || zones[0].MaxRangeUJ != 262143328850 {
		t.Fatalf("zones = %+v", zones)
	}
	if len(unreadable) != 1 || unreadable[0] != "/sys/class/powercap/intel-rapl:0:0" {
		t.Fatalf("unreadable = %v", unreadable)
	}
}

// synthetic: GPU constant 100 W, package-0 constant 50 W (wrapping), package-1 constant 10 W, 10 Hz, 10 s.
func syntheticSamples(failAt int) ([]Sample, []RAPLZone) {
	const maxR = 200_000_000 // 200 J → package-0 at 50 W wraps every 4 s
	zones := []RAPLZone{
		{Name: "package-0", Domain: "cpu_pkg", MaxRangeUJ: maxR},
		{Name: "package-1", Domain: "cpu_pkg", MaxRangeUJ: maxR},
	}
	var out []Sample
	for i := 0; i <= 100; i++ {
		ts := float64(i) / 10
		s := Sample{
			T:           sec(ts),
			GPUOK:       true,
			GPUEnergyMJ: 5_000_000 + uint64(ts*100*1000), // counter starts at an arbitrary offset
			GPUPowerMW:  100_000,
			GPUTempC:    40 + uint32(i/10),
			GPUMemUsed:  uint64(i) << 20,
			GPUUtilPct:  50,
			RAPL: []uint64{
				(150_000_000 + uint64(ts*50e6)) % (maxR + 1),
				uint64(ts * 10e6),
			},
			RAPLOK: []bool{true, true},
		}
		if i == failAt {
			s.GPUOK, s.RAPLOK = false, []bool{false, true}
			s.GPUEnergyMJ, s.RAPL[0] = 0, 0
		}
		out = append(out, s)
	}
	return out, zones
}

func TestSeriesConstantPowerWithWrap(t *testing.T) {
	samples, zones := syntheticSamples(-1)
	s := BuildSeries(samples, zones, true)
	w := s.Between(sec(1), sec(9))
	near(t, "t", w.Seconds, 8, 1e-9)
	near(t, "gpu J (power samples, primary)", *w.GPUJ, 800, 1e-6)
	near(t, "gpu counter J", *w.GPUCounterJ, 800, 1e-6)
	near(t, "cpu_pkg J (both packages, wrapped)", w.Domains["cpu_pkg"], 8*(50+10), 1e-3)
	if *w.GPUPeakTempC != 49 || *w.GPUMeanUtil != 50 {
		t.Errorf("peaks: temp %v util %v", *w.GPUPeakTempC, *w.GPUMeanUtil)
	}
	// window edges between samples are interpolated
	w2 := s.Between(sec(1.234), sec(3.456))
	near(t, "gpu J (interpolated edges)", *w2.GPUJ, 222.2, 1e-6)
	near(t, "cpu_pkg J (interpolated edges)", w2.Domains["cpu_pkg"], 2.222*60, 1e-3)
	gpuW, dw := s.MeanPower(sec(0), sec(10))
	near(t, "mean gpu W", *gpuW, 100, 1e-9)
	near(t, "mean cpu_pkg W", dw["cpu_pkg"], 60, 1e-6)
}

func TestSeriesFailedReadCarriedForward(t *testing.T) {
	samples, zones := syntheticSamples(50)
	s := BuildSeries(samples, zones, true)
	w := s.Between(sec(0), sec(10))
	// one missed sample is bridged by the next good read: totals stay exact
	near(t, "gpu J", *w.GPUJ, 1000, 1e-6)
	near(t, "cpu_pkg J", w.Domains["cpu_pkg"], 600, 1e-3)
}

func TestBetweenClampsOutsideRange(t *testing.T) {
	samples, zones := syntheticSamples(-1)
	s := BuildSeries(samples, zones, true)
	w := s.Between(sec(-5), sec(50))
	near(t, "gpu J clamped", *w.GPUJ, 1000, 1e-6)
}

func TestNoGPU(t *testing.T) {
	samples, zones := syntheticSamples(-1)
	w := BuildSeries(samples, zones, false).Between(sec(0), sec(10))
	if w.GPUJ != nil || w.GPUCounterJ != nil {
		t.Fatal("GPU fields must be nil without a GPU")
	}
}

type fakeGPU struct{ calls atomic.Int64 }

func (g *fakeGPU) EnergyMJ() (uint64, error) { return uint64(g.calls.Add(1)) * 10, nil }
func (g *fakeGPU) PowerMW() (uint32, error)  { return 42_000, nil }
func (*fakeGPU) TempC() (uint32, error)      { return 33, nil }
func (*fakeGPU) MemUsedBytes() (uint64, error) {
	return 1 << 30, nil
}
func (*fakeGPU) UtilPct() (uint32, error) { return 0, errors.New("not supported") }

func TestSamplerCollectsAtRate(t *testing.T) {
	var counter atomic.Uint64
	read := func(string) ([]byte, error) {
		return []byte(strconv.FormatUint(counter.Add(1000), 10)), nil
	}
	s := NewSampler(&fakeGPU{}, []RAPLZone{{Name: "package-0", Domain: "cpu_pkg", Path: "/x", MaxRangeUJ: 1 << 40}}, read, 100)
	s.Start(context.Background())
	time.Sleep(300 * time.Millisecond)
	samples, errs := s.Stop()
	if errs != 0 {
		t.Errorf("errs = %d", errs)
	}
	if len(samples) < 15 || len(samples) > 40 {
		t.Errorf("got %d samples in 300 ms at 100 Hz", len(samples))
	}
	for i := 1; i < len(samples); i++ {
		if samples[i].T <= samples[i-1].T {
			t.Fatalf("timestamps not increasing at %d", i)
		}
	}
}

// Opt-in: touches the real GPU (read-only NVML queries). ZJ_NVML_TEST=1 go test ./internal/energy
func TestNVMLLive(t *testing.T) {
	if os.Getenv("ZJ_NVML_TEST") != "1" {
		t.Skip("set ZJ_NVML_TEST=1 to query the real GPU")
	}
	g, err := OpenNVML(0)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = g.Close() }()
	info, err := g.Info()
	if err != nil {
		t.Fatal(err)
	}
	e0, err := g.EnergyMJ()
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(time.Second)
	e1, _ := g.EnergyMJ()
	t.Logf("%+v; 1 s idle: %d mJ", info, e1-e0)
	if e1 <= e0 {
		t.Errorf("energy counter did not advance: %d → %d", e0, e1)
	}
}

// Calibration (ADR 0006): on the RTX 5060 Ti the counter advanced at 146.3 W while all power
// readings showed 180 W. The primary GPU energy must follow the power samples, not the counter.
func TestGPUPrimaryFollowsPowerSamplesNotCounter(t *testing.T) {
	var samples []Sample
	for i := 0; i <= 100; i++ {
		ts := float64(i) / 10
		samples = append(samples, Sample{
			T: sec(ts), GPUOK: true, GPUPowerMW: 180_000,
			GPUEnergyMJ: uint64(ts * 146.3 * 1000), // counter runs at 81.3 % of the true power
		})
	}
	w := BuildSeries(samples, nil, true).Between(sec(0), sec(10))
	near(t, "primary gpu J", *w.GPUJ, 1800, 1e-6)
	near(t, "counter gpu J", *w.GPUCounterJ, 1463, 1e-6)
	gpuW, _ := BuildSeries(samples, nil, true).MeanPower(sec(0), sec(10))
	near(t, "idle/mean power uses the primary source", *gpuW, 180, 1e-9)
}
