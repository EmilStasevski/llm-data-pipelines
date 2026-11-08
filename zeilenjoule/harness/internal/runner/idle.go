package runner

import (
	"context"
	"errors"
	"fmt"
	"math"
	"time"

	"zeilenjoule/harness/internal/cpustat"
	"zeilenjoule/harness/internal/energy"
	"zeilenjoule/harness/internal/results"
)

// IdleRecord is one idle baseline (results/idle.jsonl), used for E_net = E_gross − P_idle × t.
type IdleRecord struct {
	SessionID  string          `json:"session_id"`
	TS         string          `json:"ts"`
	DurationS  float64         `json:"duration_s"`
	PowerW     results.Domains `json:"power_w"`
	LoadAvg1   float64         `json:"loadavg1"`
	GPUTempC   *float64        `json:"gpu_temp_c"`
	Samples    int             `json:"samples"`
	ReadErrs   int             `json:"read_errors"`
	CPUBusyPct *float64        `json:"cpu_busy_pct"` // over the whole window (ADR 0007)
	Quiet      bool            `json:"quiet"`        // cpu_busy_pct <= load_guard.max_busy_pct
	Taken      time.Time       `json:"-"`
}

// MeasureIdle samples for d with nothing launched by the harness and returns mean power per domain.
func (r *Runner) MeasureIdle(ctx context.Context, sessionID string, d time.Duration) (IdleRecord, error) {
	if !r.SkipGuards {
		if _, err := r.waitForQuiet(ctx); err != nil {
			return IdleRecord{}, err
		}
	}
	start := time.Now()
	cpuA, statErr := cpustat.Read(r.ReadFile)
	s := energy.NewSampler(r.GPU, r.Zones, r.ReadFile, r.Bench.SampleHz)
	s.Start(ctx)
	t0 := s.Since()
	err := sleepCtx(ctx, d)
	t1 := s.Since()
	samples, errs := s.Stop()
	if err != nil {
		return IdleRecord{}, ErrInterrupted
	}
	busy := math.NaN()
	if cpuB, err2 := cpustat.Read(r.ReadFile); statErr == nil && err2 == nil {
		busy = cpustat.BusyPct(cpuA, cpuB) // over the whole idle window, not just a moment
	}
	series := energy.BuildSeries(samples, r.Zones, r.GPU != nil)
	gpuW, domW := series.MeanPower(t0, t1)
	w := series.Between(t0, t1)
	rec := IdleRecord{
		SessionID: sessionID, TS: start.UTC().Format(time.RFC3339Nano), DurationS: (t1 - t0).Seconds(),
		LoadAvg1: loadavg1(r.ReadFile), GPUTempC: w.GPUPeakTempC, Samples: w.Samples, ReadErrs: errs, Taken: start,
	}
	if !math.IsNaN(busy) {
		rec.CPUBusyPct = &busy
	}
	rec.Quiet = r.quiet(busy)
	rec.PowerW.GPU = gpuW
	d2 := domainsFromWindow(energy.Window{Domains: domW}, r.Zones)
	rec.PowerW.CPUPkg, rec.PowerW.CPUCore, rec.PowerW.DRAM = d2.CPUPkg, d2.CPUCore, d2.DRAM
	return rec, nil
}

// ErrIdleNotQuiet means no idle baseline stayed quiet within the allowed attempts.
var ErrIdleNotQuiet = errors.New("idle baseline not quiet")

// MeasureQuietIdle repeats MeasureIdle until one measurement stayed quiet (CPU busy fraction over
// the whole window within the guard). Every attempt is passed to record (stored as data, with quiet=false
// where applicable), but only a quiet one is returned for use: a contaminated baseline would make
// E_net wrong (Phase 2 smoke session: baseline 15.4 W vs. 3 W runs → negative E_net).
func (r *Runner) MeasureQuietIdle(ctx context.Context, sessionID string, d time.Duration, attempts int, record func(IdleRecord) error) (IdleRecord, error) {
	for i := 1; i <= attempts; i++ {
		rec, err := r.MeasureIdle(ctx, sessionID, d)
		if err != nil {
			return IdleRecord{}, err
		}
		if record != nil {
			if err := record(rec); err != nil {
				return IdleRecord{}, err
			}
		}
		if rec.Quiet {
			return rec, nil
		}
		r.logger().Warn("idle baseline not quiet, discarded", "attempt", i, "of", attempts, "loadavg1_end", rec.LoadAvg1)
	}
	return IdleRecord{}, fmt.Errorf("%w after %d attempts: background load during the measurement (checklist C5/C6)", ErrIdleNotQuiet, attempts)
}
