// Package exporter serves GPU/CPU energy as Prometheus metrics (PLAN §12.1, zj-energy-exporter).
//
// RAPL counters are unwrapped by a background poller: exposing raw energy_uj would make every
// wrap look like a counter reset, and Prometheus would lose the energy between the last scrape
// and the wrap point.
package exporter

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"sort"
	"sync"
	"time"

	"zeilenjoule/harness/internal/energy"
)

// Exporter keeps unwrapped RAPL totals and reads the GPU on scrape.
type Exporter struct {
	gpu      energy.GPU
	zones    []energy.RAPLZone
	readFile func(string) ([]byte, error)

	mu      sync.Mutex
	last    []uint64
	lastOK  []bool
	totalUJ map[string]float64 // per domain since exporter start
}

// New creates an exporter; call Run to start the RAPL poller.
func New(gpu energy.GPU, zones []energy.RAPLZone, readFile func(string) ([]byte, error)) *Exporter {
	e := &Exporter{gpu: gpu, zones: zones, readFile: readFile,
		last: make([]uint64, len(zones)), lastOK: make([]bool, len(zones)), totalUJ: map[string]float64{}}
	for _, z := range zones {
		e.totalUJ[z.Domain] = 0
	}
	e.poll()
	return e
}

func (e *Exporter) poll() {
	e.mu.Lock()
	defer e.mu.Unlock()
	for i, z := range e.zones {
		v, err := energy.ReadCounter(e.readFile, z.Path+"/energy_uj")
		if err != nil {
			continue
		}
		if e.lastOK[i] {
			e.totalUJ[z.Domain] += float64(energy.WrapDelta(e.last[i], v, z.MaxRangeUJ))
		}
		e.last[i], e.lastOK[i] = v, true
	}
}

// Run polls RAPL every interval until ctx ends (must be well below the wrap period).
func (e *Exporter) Run(ctx context.Context, interval time.Duration) {
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			e.poll()
		}
	}
}

// Write emits the Prometheus text exposition format.
func (e *Exporter) Write(w io.Writer) {
	e.poll() // include energy up to this scrape
	metric := func(name, typ, help string) {
		_, _ = fmt.Fprintf(w, "# HELP %s %s\n# TYPE %s %s\n", name, help, name, typ)
	}
	if e.gpu != nil {
		if v, err := e.gpu.EnergyMJ(); err == nil {
			metric("zj_gpu_energy_joules_total", "counter", "GPU board energy since driver load (NVML).")
			_, _ = fmt.Fprintf(w, "zj_gpu_energy_joules_total %.3f\n", float64(v)/1000)
		}
		if v, err := e.gpu.PowerMW(); err == nil {
			metric("zj_gpu_power_watts", "gauge", "GPU board power (NVML, averaged by the driver).")
			_, _ = fmt.Fprintf(w, "zj_gpu_power_watts %.3f\n", float64(v)/1000)
		}
		if v, err := e.gpu.TempC(); err == nil {
			metric("zj_gpu_temp_celsius", "gauge", "GPU core temperature.")
			_, _ = fmt.Fprintf(w, "zj_gpu_temp_celsius %d\n", v)
		}
		if v, err := e.gpu.MemUsedBytes(); err == nil {
			metric("zj_gpu_memory_used_bytes", "gauge", "GPU memory in use (device level).")
			_, _ = fmt.Fprintf(w, "zj_gpu_memory_used_bytes %d\n", v)
		}
		if v, err := e.gpu.UtilPct(); err == nil {
			metric("zj_gpu_utilization_ratio", "gauge", "GPU utilization (0-1).")
			_, _ = fmt.Fprintf(w, "zj_gpu_utilization_ratio %.2f\n", float64(v)/100)
		}
	}
	e.mu.Lock()
	domains := make([]string, 0, len(e.totalUJ))
	for d := range e.totalUJ {
		domains = append(domains, d)
	}
	sort.Strings(domains)
	if len(domains) > 0 {
		metric("zj_cpu_energy_joules_total", "counter", "CPU energy per RAPL domain since exporter start (unwrapped).")
		for _, d := range domains {
			_, _ = fmt.Fprintf(w, "zj_cpu_energy_joules_total{domain=%q} %.3f\n", d, e.totalUJ[d]/1e6)
		}
	}
	e.mu.Unlock()
}

// Handler serves /metrics and /healthz.
func (e *Exporter) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		e.Write(w)
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "ok\n")
	})
	return mux
}
