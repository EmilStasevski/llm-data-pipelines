package exporter

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"zeilenjoule/harness/internal/energy"
)

type gpu struct{}

func (gpu) EnergyMJ() (uint64, error)     { return 123_456, nil }
func (gpu) PowerMW() (uint32, error)      { return 15_500, nil }
func (gpu) TempC() (uint32, error)        { return 41, nil }
func (gpu) MemUsedBytes() (uint64, error) { return 1 << 30, nil }
func (gpu) UtilPct() (uint32, error)      { return 7, nil }

func TestMetricsUnwrapRAPL(t *testing.T) {
	// counter (µJ) wraps at 1e9: 900e6 → 990e6 (+90 J) → 20e6 (+10e6+1+20e6 µJ = +30.000001 J) → 120.000 J
	seq := []uint64{900_000_000, 990_000_000, 20_000_000}
	i := 0
	read := func(string) ([]byte, error) {
		v := seq[min(i, len(seq)-1)]
		i++
		return []byte(strconv.FormatUint(v, 10)), nil
	}
	e := New(gpu{}, []energy.RAPLZone{{Name: "package-0", Domain: "cpu_pkg", Path: "/z", MaxRangeUJ: 1_000_000_000}}, read)
	e.poll()
	srv := httptest.NewServer(e.Handler())
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/metrics") //nolint:noctx // test
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	body := string(raw)
	for _, want := range []string{
		"zj_gpu_energy_joules_total 123.456",
		"zj_gpu_power_watts 15.500",
		"zj_gpu_utilization_ratio 0.07",
		`zj_cpu_energy_joules_total{domain="cpu_pkg"} 120.000` + "\n",
		"# TYPE zj_cpu_energy_joules_total counter",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("missing %q in:\n%s", want, body)
		}
	}
}
