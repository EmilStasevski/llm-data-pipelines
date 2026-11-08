package energy

import (
	"math"
	"sort"
	"time"
)

// Series is the integrable form of a sample list: cumulative joules per domain, starting at 0.
type Series struct {
	T        []float64            // seconds since sampler epoch
	GPUCumJ  []float64            // from the NVML energy counter; nil if no GPU
	GPUPowW  []float64            // instantaneous power samples
	Domains  map[string][]float64 // cpu_pkg, cpu_core, dram … cumulative J (RAPL, unwrapped, summed per domain)
	GPUTempC []float64
	GPUMemB  []float64
	GPUUtil  []float64
}

// BuildSeries unwraps RAPL counters and converts everything to cumulative joules.
// Samples whose GPU or RAPL read failed carry the previous cumulative value forward.
func BuildSeries(samples []Sample, zones []RAPLZone, hasGPU bool) Series {
	n := len(samples)
	s := Series{T: make([]float64, n), Domains: map[string][]float64{}}
	if hasGPU {
		s.GPUCumJ, s.GPUPowW = make([]float64, n), make([]float64, n)
		s.GPUTempC, s.GPUMemB, s.GPUUtil = make([]float64, n), make([]float64, n), make([]float64, n)
	}
	zoneCum := make([][]float64, len(zones))
	for z := range zones {
		zoneCum[z] = make([]float64, n)
	}
	var gpuBase uint64
	gpuBaseSet := false
	lastRaw := make([]uint64, len(zones))
	lastOK := make([]bool, len(zones))
	for i, smp := range samples {
		s.T[i] = smp.T.Seconds()
		if hasGPU {
			switch {
			case smp.GPUOK && !gpuBaseSet:
				gpuBase, gpuBaseSet = smp.GPUEnergyMJ, true
				s.GPUCumJ[i] = 0
			case smp.GPUOK && smp.GPUEnergyMJ >= gpuBase:
				s.GPUCumJ[i] = float64(smp.GPUEnergyMJ-gpuBase) / 1000
			case i > 0:
				s.GPUCumJ[i] = s.GPUCumJ[i-1]
			}
			s.GPUPowW[i] = float64(smp.GPUPowerMW) / 1000
			s.GPUTempC[i] = float64(smp.GPUTempC)
			s.GPUMemB[i] = float64(smp.GPUMemUsed)
			s.GPUUtil[i] = float64(smp.GPUUtilPct)
		}
		for z, zone := range zones {
			prev := 0.0
			if i > 0 {
				prev = zoneCum[z][i-1]
			}
			ok := z < len(smp.RAPLOK) && smp.RAPLOK[z]
			switch {
			case ok && lastOK[z]:
				zoneCum[z][i] = prev + float64(WrapDelta(lastRaw[z], smp.RAPL[z], zone.MaxRangeUJ))/1e6
			default:
				zoneCum[z][i] = prev
			}
			if ok {
				lastRaw[z], lastOK[z] = smp.RAPL[z], true
			}
		}
	}
	for z, zone := range zones {
		if acc, ok := s.Domains[zone.Domain]; ok {
			for i := range acc {
				acc[i] += zoneCum[z][i]
			}
		} else {
			s.Domains[zone.Domain] = zoneCum[z]
		}
	}
	return s
}

// interp linearly interpolates ys at x (clamped to the sampled range).
func interp(xs, ys []float64, x float64) float64 {
	n := len(xs)
	if n == 0 || len(ys) != n {
		return math.NaN()
	}
	if x <= xs[0] {
		return ys[0]
	}
	if x >= xs[n-1] {
		return ys[n-1]
	}
	i := sort.SearchFloat64s(xs, x) // xs[i-1] < x <= xs[i]
	x0, x1 := xs[i-1], xs[i]
	if x1 == x0 {
		return ys[i]
	}
	return ys[i-1] + (ys[i]-ys[i-1])*(x-x0)/(x1-x0)
}

// Window is energy and peaks over [t0, t1].
type Window struct {
	Seconds      float64            `json:"t_s"`
	GPUJ         *float64           `json:"gpu_j"`         // primary: trapezoid over power samples (ADR 0006)
	GPUCounterJ  *float64           `json:"gpu_counter_j"` // NVML total-energy counter (cross-check only)
	Domains      map[string]float64 `json:"domains_j"`
	GPUPeakTempC *float64           `json:"gpu_peak_temp_c,omitempty"`
	GPUPeakMemB  *float64           `json:"gpu_peak_mem_bytes,omitempty"`
	GPUMeanUtil  *float64           `json:"gpu_mean_util_pct,omitempty"`
	Samples      int                `json:"samples"`
}

func ptr(v float64) *float64 { return &v }

// Between integrates the series over [t0, t1] (durations since the sampler epoch).
func (s Series) Between(t0, t1 time.Duration) Window {
	a, b := t0.Seconds(), t1.Seconds()
	w := Window{Seconds: b - a, Domains: map[string]float64{}}
	for d, cum := range s.Domains {
		w.Domains[d] = interp(s.T, cum, b) - interp(s.T, cum, a)
	}
	inside := 0
	for _, t := range s.T {
		if t >= a && t <= b {
			inside++
		}
	}
	w.Samples = inside
	if s.GPUCumJ == nil {
		return w
	}
	w.GPUJ = ptr(trapezoid(s.T, s.GPUPowW, a, b))
	w.GPUCounterJ = ptr(interp(s.T, s.GPUCumJ, b) - interp(s.T, s.GPUCumJ, a))
	peakT, peakM, sumU, nU := math.Inf(-1), math.Inf(-1), 0.0, 0
	for i, t := range s.T {
		if t < a || t > b {
			continue
		}
		peakT, peakM = math.Max(peakT, s.GPUTempC[i]), math.Max(peakM, s.GPUMemB[i])
		sumU += s.GPUUtil[i]
		nU++
	}
	if nU > 0 {
		w.GPUPeakTempC, w.GPUPeakMemB, w.GPUMeanUtil = ptr(peakT), ptr(peakM), ptr(sumU/float64(nU))
	}
	return w
}

// trapezoid integrates ys over [a, b] ∩ [first sample, last sample], interpolating the edges.
// Power outside the sampled range is unknown, so it is not extrapolated.
func trapezoid(xs, ys []float64, a, b float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	a, b = math.Max(a, xs[0]), math.Min(b, xs[len(xs)-1])
	if b <= a {
		return 0
	}
	pts := []float64{a}
	for _, x := range xs {
		if x > a && x < b {
			pts = append(pts, x)
		}
	}
	pts = append(pts, b)
	total := 0.0
	for i := 1; i < len(pts); i++ {
		y0, y1 := interp(xs, ys, pts[i-1]), interp(xs, ys, pts[i])
		total += (y0 + y1) / 2 * (pts[i] - pts[i-1])
	}
	return total
}

// MeanPower returns average watts per domain (and GPU) over [t0, t1]: used for idle baselines.
func (s Series) MeanPower(t0, t1 time.Duration) (gpuW *float64, domainsW map[string]float64) {
	w := s.Between(t0, t1)
	domainsW = map[string]float64{}
	if w.Seconds <= 0 {
		return nil, domainsW
	}
	for d, j := range w.Domains {
		domainsW[d] = j / w.Seconds
	}
	if w.GPUJ != nil {
		gpuW = ptr(*w.GPUJ / w.Seconds)
	}
	return gpuW, domainsW
}
