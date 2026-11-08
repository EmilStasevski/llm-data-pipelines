package cpustat

import (
	"math"
	"os"
	"testing"
	"time"
)

const stat0 = "cpu  100 10 50 1000 40 5 5 0 7 0\ncpu0 1 2 3 4 5 6 7 8 9 10\nintr 1\n"
const stat1 = "cpu  130 10 60 1460 50 5 5 0 9 0\n"

func TestParseAndBusyPct(t *testing.T) {
	a, err := Parse([]byte(stat0))
	if err != nil || a.Busy != 170 || a.Total != 1210 {
		t.Fatalf("Parse = %+v, %v (guest must not be added: busy 100+10+50+5+5+0)", a, err)
	}
	b, _ := Parse([]byte(stat1))
	// Δbusy = 40 (user 30 + system 10), Δtotal = 40 + Δidle 460 + Δiowait 10 = 510 → 7.84 %
	if got := BusyPct(a, b); math.Abs(got-100*40.0/510) > 1e-9 {
		t.Errorf("BusyPct = %v", got)
	}
	if !math.IsNaN(BusyPct(a, a)) {
		t.Error("no elapsed ticks must give NaN")
	}
}

func TestParseErrors(t *testing.T) {
	for _, bad := range []string{"", "cpu0 1 2 3\n", "cpu  1 2 3\n", "cpu  1 2 x 4 5 6 7 8\n"} {
		if _, err := Parse([]byte(bad)); err == nil {
			t.Errorf("Parse(%q) should fail", bad)
		}
	}
}

func TestLiveProcStat(t *testing.T) {
	a, err := Read(os.ReadFile)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(200 * time.Millisecond)
	b, _ := Read(os.ReadFile)
	if p := BusyPct(a, b); math.IsNaN(p) || p < 0 || p > 100 {
		t.Errorf("live busy %% = %v", p)
	}
}
