package calib

import (
	"math"
	"strings"
	"testing"

	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/internal/runner"
)

func f(v float64) *float64 { return &v }

func idle(gpu, pkg float64) runner.IdleRecord {
	return runner.IdleRecord{PowerW: results.Domains{GPU: f(gpu), CPUPkg: f(pkg)}}
}

func TestIdleStats(t *testing.T) {
	stats, base := IdleStats([]runner.IdleRecord{idle(6, 10), idle(6.2, 10.5), idle(5.8, 9.5)})
	if len(stats) != 2 || stats[0].Domain != "gpu" || stats[1].Domain != "cpu_pkg" {
		t.Fatalf("stats = %+v", stats)
	}
	if math.Abs(*base.GPU-6) > 1e-9 || math.Abs(stats[1].SDW-0.5) > 1e-9 || !stats[0].Stable || base.DRAM != nil {
		t.Fatalf("stats %+v base %+v", stats, base)
	}
	unstable, _ := IdleStats([]runner.IdleRecord{idle(5, 10), idle(9, 10)})
	if unstable[0].Stable {
		t.Error("CV ~40 % must be unstable")
	}
}

func rec(id string, wall float64, stageName string, stageS float64, gpuJ, cpuJ, netGPU, netCPU, dev *float64) results.Record {
	return results.Record{
		Contestant: id, Status: results.StatusOK, WallS: wall,
		Stages:      []results.Stage{{Name: stageName, TS: stageS, GPUJ: gpuJ, CPUJ: cpuJ}},
		EnergyJ:     results.Domains{GPU: gpuJ, CPUPkg: cpuJ},
		EnergyNetJ:  results.Domains{GPU: netGPU, CPUPkg: netCPU},
		EnergyCheck: results.EnergyCheck{DeviationPct: dev},
	}
}

func TestEvaluateAllPassAndRender(t *testing.T) {
	recs := []results.Record{
		rec(IDCPU, 61, "work", 60, f(600), f(60*120), f(0), f(6000), f(1)),
		rec(IDGPU, 66, "burn", 60, f(60*170), f(60*30), f(9000), f(1000), f(-2.5)),
		rec(IDOverhead, 30.02, "work", 30, f(180), f(300), f(5), f(-10), f(0)),
	}
	idles := []runner.IdleRecord{idle(6, 10), idle(6, 10), idle(6, 10)}
	r := Evaluate("s1", "2026-11-06T00:00:00Z", idles, recs, f(125), f(150), f(180))
	if !r.AllPass() {
		for _, c := range r.Checks {
			t.Logf("%s: %v %s (%s)", c.Name, c.Pass != nil && *c.Pass, c.Value, c.Expect)
		}
		t.Fatal("expected all checks to pass")
	}
	if got := *r.Runs[IDGPU].GPUMeanW; got != 170 {
		t.Errorf("GPU mean over the burn stage = %v, want 170", got)
	}
	md, err := r.Markdown()
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"Generiert von `zj calibrate`", "170.00 W", "| ✅ |", "`x03_gpuburn_torch`", "PL1 125.00 W"} {
		if !strings.Contains(md, want) {
			t.Errorf("markdown misses %q:\n%s", want, md)
		}
	}
}

func TestEvaluateFailuresAndUnmeasured(t *testing.T) {
	recs := []results.Record{
		rec(IDGPU, 66, "burn", 60, f(60*90), nil, f(1), nil, f(12)), // weak GPU, big deviation
		rec(IDCPU, 61, "work", 60, nil, nil, nil, nil, nil),         // RAPL not readable
	}
	r := Evaluate("s", "", []runner.IdleRecord{idle(6, 10)}, recs, nil, nil, f(180))
	byName := map[string]Check{}
	for _, c := range r.Checks {
		byName[c.Name] = c
	}
	if p := byName["GPU load: steady power vs enforced limit"].Pass; p == nil || *p {
		t.Error("90 W of 180 W must fail")
	}
	if c := byName["GPU: NVML energy counter vs power samples"]; !c.Info || !strings.Contains(c.Value, "12.00") {
		t.Errorf("counter deviation must be an info line, got %+v", c)
	}
	cpu := byName["CPU load: RAPL package power plausible"]
	if cpu.Pass != nil || !strings.Contains(cpu.Value, "not measured") {
		t.Errorf("unmeasured CPU must be unevaluable, got %+v", cpu)
	}
	if r.AllPass() {
		t.Error("AllPass must be false")
	}
}

func TestGPUAboveLimitFailsAndInfoDoesNotBlockAllPass(t *testing.T) {
	over := Evaluate("s", "", []runner.IdleRecord{idle(6, 10), idle(6, 10)},
		[]results.Record{rec(IDGPU, 66, "burn", 60, f(60*200), nil, nil, nil, f(-19))}, nil, nil, f(180))
	var gpu, info Check
	for _, c := range over.Checks {
		switch c.Name {
		case "GPU load: steady power vs enforced limit":
			gpu = c
		case "GPU: NVML energy counter vs power samples":
			info = c
		}
	}
	if gpu.Pass == nil || *gpu.Pass {
		t.Error("200 W on a 180 W card is implausible and must fail")
	}
	ok := Evaluate("s", "", []runner.IdleRecord{idle(6, 10), idle(6, 10)},
		[]results.Record{rec(IDGPU, 66, "burn", 60, f(60*179), nil, nil, nil, f(-19))}, nil, nil, f(180))
	if !ok.AllPass() || !info.Info {
		t.Errorf("a -19 %% counter deviation is informational and must not fail calibration: %+v", ok.Checks)
	}
}

func TestOverheadMedianIgnoresOneBurst(t *testing.T) {
	recs := []results.Record{
		rec(IDOverhead, 30.004, "work", 30, f(140), f(72), f(4), f(-1), nil),
		rec(IDOverhead, 30.003, "work", 30, f(206), f(114), f(70), f(42), nil), // burst: +112 J
		rec(IDOverhead, 30.004, "work", 30, f(141), f(73), f(5), f(0), nil),
	}
	r := Evaluate("s", "", []runner.IdleRecord{idle(4.6, 2.4), idle(4.6, 2.4)}, recs, nil, nil, nil)
	if !r.AllPass() {
		t.Fatalf("median of {3, 112, 5} J must pass: %+v", r.Checks)
	}
	if len(r.Runs) != 3 {
		t.Errorf("all three reps must be reported, got %d", len(r.Runs))
	}
	recs[0].EnergyNetJ, recs[2].EnergyNetJ = recs[1].EnergyNetJ, recs[1].EnergyNetJ
	if Evaluate("s", "", nil, recs, nil, nil, nil).AllPass() {
		t.Error("a systematic +112 J in every rep must fail")
	}
}
