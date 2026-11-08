// Package cpustat measures the instantaneous CPU busy fraction from /proc/stat. It is the load
// guard signal (ADR 0007): unlike loadavg1 (an exponentially decaying 1-min average that stayed
// > 1 for ~3.5 min after a CPU-heavy run), it describes exactly the measured window.
package cpustat

import (
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
)

// Times are aggregate jiffies from the "cpu" line of /proc/stat.
type Times struct {
	Busy, Total uint64
}

// Parse reads the aggregate "cpu " line. Busy = user+nice+system+irq+softirq+steal;
// idle and iowait are not busy; guest/guest_nice are already contained in user/nice.
func Parse(b []byte) (Times, error) {
	for line := range strings.Lines(string(b)) {
		rest, ok := strings.CutPrefix(line, "cpu ")
		if !ok {
			continue
		}
		f := strings.Fields(rest)
		if len(f) < 8 {
			return Times{}, fmt.Errorf("/proc/stat cpu line has %d fields, want ≥ 8", len(f))
		}
		var v [8]uint64
		for i := range v {
			n, err := strconv.ParseUint(f[i], 10, 64)
			if err != nil {
				return Times{}, fmt.Errorf("/proc/stat field %d: %w", i, err)
			}
			v[i] = n
		}
		user, nice, system, idle, iowait, irq, softirq, steal := v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]
		busy := user + nice + system + irq + softirq + steal
		return Times{Busy: busy, Total: busy + idle + iowait}, nil
	}
	return Times{}, errors.New("/proc/stat: no aggregate cpu line")
}

// Read parses /proc/stat via readFile.
func Read(readFile func(string) ([]byte, error)) (Times, error) {
	b, err := readFile("/proc/stat")
	if err != nil {
		return Times{}, err
	}
	return Parse(b)
}

// BusyPct is the busy share of all CPUs between two readings, in percent.
// NaN if no ticks elapsed (callers must treat NaN as "not quiet").
func BusyPct(a, b Times) float64 {
	if b.Total <= a.Total || b.Busy < a.Busy {
		return math.NaN()
	}
	return 100 * float64(b.Busy-a.Busy) / float64(b.Total-a.Total)
}
