// Package energy samples GPU (NVML) and CPU (RAPL) energy and integrates it over time windows.
//
// Design: CPU energy comes from the cumulative RAPL counters (energy_uj), differenced and linearly
// interpolated at the window edges. GPU energy comes from integrating the 20 Hz NVML power samples
// (trapezoid); the NVML total-energy counter is recorded as a cross-check only, because on the
// RTX 5060 Ti / driver 580 it reads ~19 % low under load (calibration, ADR 0006).
package energy

import (
	"context"
	"slices"
	"sync"
	"time"
)

// Sample is one poll of all sources. T is monotonic time since the sampler epoch.
type Sample struct {
	T           time.Duration
	GPUOK       bool
	GPUEnergyMJ uint64
	GPUPowerMW  uint32
	GPUTempC    uint32
	GPUMemUsed  uint64
	GPUUtilPct  uint32
	RAPL        []uint64 // raw energy_uj per zone (same order as Sampler zones); 0 if a read failed
	RAPLOK      []bool
}

// Sampler polls the sources at a fixed rate in a background goroutine.
type Sampler struct {
	gpu      GPU // may be nil (no GPU energy)
	zones    []RAPLZone
	readFile func(string) ([]byte, error)
	period   time.Duration
	epoch    time.Time

	mu      sync.Mutex
	samples []Sample
	errs    int
	stop    context.CancelFunc
	done    chan struct{}
}

// NewSampler creates a sampler; epoch is now. gpu may be nil.
func NewSampler(gpu GPU, zones []RAPLZone, readFile func(string) ([]byte, error), hz float64) *Sampler {
	if hz <= 0 {
		hz = 20
	}
	return &Sampler{
		gpu: gpu, zones: zones, readFile: readFile,
		period: time.Duration(float64(time.Second) / hz),
		epoch:  time.Now(),
	}
}

// Since returns monotonic time since the sampler epoch (use it to timestamp markers).
func (s *Sampler) Since() time.Duration { return time.Since(s.epoch) }

// Epoch is the wall-clock start of the sampler (its monotonic reading is used by Since).
func (s *Sampler) Epoch() time.Time { return s.epoch }

// Zones returns the RAPL zones this sampler reads.
func (s *Sampler) Zones() []RAPLZone { return s.zones }

// Period returns the sampling period.
func (s *Sampler) Period() time.Duration { return s.period }

// Poll takes one sample immediately and stores it.
func (s *Sampler) Poll() Sample {
	smp := Sample{RAPL: make([]uint64, len(s.zones)), RAPLOK: make([]bool, len(s.zones))}
	failed := 0
	if s.gpu != nil {
		var err error
		smp.GPUEnergyMJ, err = s.gpu.EnergyMJ()
		smp.GPUOK = err == nil
		if err != nil {
			failed++
		}
		// best-effort secondary metrics
		smp.GPUPowerMW, _ = s.gpu.PowerMW()
		smp.GPUTempC, _ = s.gpu.TempC()
		smp.GPUMemUsed, _ = s.gpu.MemUsedBytes()
		smp.GPUUtilPct, _ = s.gpu.UtilPct()
	}
	for i, z := range s.zones {
		v, err := ReadCounter(s.readFile, z.Path+"/energy_uj")
		smp.RAPL[i], smp.RAPLOK[i] = v, err == nil
		if err != nil {
			failed++
		}
	}
	smp.T = s.Since() // timestamp after the reads: the counters reflect "now"
	s.mu.Lock()
	s.samples = append(s.samples, smp)
	s.errs += failed
	s.mu.Unlock()
	return smp
}

// Start begins background polling until Stop is called or ctx ends.
func (s *Sampler) Start(ctx context.Context) {
	ctx, s.stop = context.WithCancel(ctx)
	s.done = make(chan struct{})
	go func() {
		defer close(s.done)
		t := time.NewTicker(s.period)
		defer t.Stop()
		s.Poll()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				s.Poll()
			}
		}
	}()
}

// Stop ends polling, takes a final sample and returns all samples and the read-error count.
func (s *Sampler) Stop() ([]Sample, int) {
	if s.stop != nil {
		s.stop()
		<-s.done
	}
	s.Poll()
	s.mu.Lock()
	defer s.mu.Unlock()
	return slices.Clone(s.samples), s.errs
}
