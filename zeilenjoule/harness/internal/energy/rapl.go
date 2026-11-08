package energy

import (
	"fmt"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
)

// RAPLZone is one readable powercap zone (Intel and AMD use the intel-rapl naming).
type RAPLZone struct {
	Name       string // raw zone name, e.g. "package-0", "core", "dram"
	Domain     string // harness domain: cpu_pkg, cpu_core, dram, cpu_uncore, psys
	Path       string // directory, e.g. /sys/class/powercap/intel-rapl:0
	MaxRangeUJ uint64 // counter wraps after this value
}

// DomainForZone maps a powercap zone name to the harness energy domain.
func DomainForZone(name string) string {
	switch {
	case strings.HasPrefix(name, "package-"):
		return "cpu_pkg"
	case name == "core":
		return "cpu_core"
	case name == "dram":
		return "dram"
	case name == "uncore":
		return "cpu_uncore"
	case name == "psys":
		return "psys"
	default:
		return "rapl_" + name
	}
}

// DiscoverRAPL returns all zones whose energy_uj is readable, plus the paths that were not.
func DiscoverRAPL(readFile func(string) ([]byte, error), glob func(string) ([]string, error)) (zones []RAPLZone, unreadable []string, err error) {
	paths, err := glob("/sys/class/powercap/intel-rapl:*")
	if err != nil {
		return nil, nil, err
	}
	slices.Sort(paths)
	for _, p := range paths {
		name, err := readUint64OrString(readFile, filepath.Join(p, "name"))
		if err != nil {
			continue
		}
		if _, err := ReadCounter(readFile, filepath.Join(p, "energy_uj")); err != nil {
			unreadable = append(unreadable, p)
			continue
		}
		maxS, err := readUint64OrString(readFile, filepath.Join(p, "max_energy_range_uj"))
		if err != nil {
			return nil, nil, fmt.Errorf("%s: max_energy_range_uj: %w", p, err)
		}
		maxV, err := strconv.ParseUint(maxS, 10, 64)
		if err != nil || maxV == 0 {
			return nil, nil, fmt.Errorf("%s: bad max_energy_range_uj %q", p, maxS)
		}
		zones = append(zones, RAPLZone{Name: name, Domain: DomainForZone(name), Path: p, MaxRangeUJ: maxV})
	}
	return zones, unreadable, nil
}

func readUint64OrString(readFile func(string) ([]byte, error), path string) (string, error) {
	b, err := readFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(b)), nil
}

// ReadCounter reads an energy_uj counter.
func ReadCounter(readFile func(string) ([]byte, error), path string) (uint64, error) {
	s, err := readUint64OrString(readFile, path)
	if err != nil {
		return 0, err
	}
	return strconv.ParseUint(s, 10, 64)
}

// WrapDelta returns cur-prev for a counter that wraps to 0 after maxRange.
// Correct as long as at most one wrap happened between the two reads (the sampler polls far
// more often than the ~29 min wrap period at 150 W).
func WrapDelta(prev, cur, maxRange uint64) uint64 {
	if cur >= prev {
		return cur - prev
	}
	return maxRange - prev + cur + 1
}
