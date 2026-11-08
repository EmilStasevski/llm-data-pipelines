// Package results defines the run record (PLAN §5.5), derived metrics (§5.6) and the
// append-only JSONL store results/runs.jsonl (source of truth; DuckDB is derived from it).
package results

import (
	"bufio"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
)

// Status values. Only "ok" runs count (rule 4).
const (
	StatusOK           = "ok"
	StatusDNF          = "dnf"
	StatusOOM          = "oom"
	StatusFail         = "fail"
	StatusDisqualified = "disqualified"
)

// Domains is energy (J) or power (W) per measurement domain. nil = not measured (rule 3).
type Domains struct {
	GPU     *float64 `json:"gpu"`
	CPUPkg  *float64 `json:"cpu_pkg"`
	CPUCore *float64 `json:"cpu_core"`
	DRAM    *float64 `json:"dram"`
	Wall    *float64 `json:"wall"`
}

// Total returns GPU + CPU package + DRAM (core is a subset of package; wall is reported separately).
// nil if neither GPU nor CPU package was measured.
func (d Domains) Total() *float64 {
	if d.GPU == nil && d.CPUPkg == nil {
		return nil
	}
	t := 0.0
	for _, v := range []*float64{d.GPU, d.CPUPkg, d.DRAM} {
		if v != nil {
			t += *v
		}
	}
	return &t
}

// Net returns gross − idle×t per domain; a domain stays nil if gross or idle is nil.
func Net(gross, idleW Domains, seconds float64) Domains {
	sub := func(g, p *float64) *float64 {
		if g == nil || p == nil {
			return nil
		}
		v := *g - *p*seconds
		return &v
	}
	return Domains{
		GPU: sub(gross.GPU, idleW.GPU), CPUPkg: sub(gross.CPUPkg, idleW.CPUPkg),
		CPUCore: sub(gross.CPUCore, idleW.CPUCore), DRAM: sub(gross.DRAM, idleW.DRAM),
		Wall: sub(gross.Wall, idleW.Wall),
	}
}

// Verify is the correctness verdict.
type Verify struct {
	Passed *bool  `json:"passed"` // nil = not verified (e.g. run failed before)
	Detail string `json:"detail"`
}

// Stage is one ZJMARK start/end pair.
type Stage struct {
	Name   string   `json:"name"`
	Depth  int      `json:"depth"`
	StartS float64  `json:"start_s"` // relative to process start
	TS     float64  `json:"t_s"`
	GPUJ   *float64 `json:"gpu_j"`
	CPUJ   *float64 `json:"cpu_j"` // cpu_pkg
	DRAMJ  *float64 `json:"dram_j"`
}

// EnergyCheck compares the primary GPU source (integrated power samples) with the NVML energy
// counter. DeviationPct = (counter − integrated) / integrated × 100 (ADR 0006).
type EnergyCheck struct {
	GPUCounterJ    *float64 `json:"gpu_counter_j"`
	GPUIntegratedJ *float64 `json:"gpu_integrated_j"`
	DeviationPct   *float64 `json:"deviation_pct"`
	Samples        int      `json:"samples"`
	ReadErrors     int      `json:"read_errors"`
}

// Throughput metrics.
type Throughput struct {
	RowsPerS  *float64 `json:"rows_per_s"`
	RowsPerKJ *float64 `json:"rows_per_kj"`
	JPerRow   *float64 `json:"j_per_row"`
}

// ContestantResult is the parsed ZJRESULT line.
type ContestantResult struct {
	Rows  int64          `json:"rows"`
	Notes string         `json:"notes"`
	Extra map[string]any `json:"extra"`
}

// Config of the run.
type Config struct {
	Threads   int     `json:"threads"`
	BatchSize *int    `json:"batch_size"`
	Model     *string `json:"model"`
}

// Env is the environment fingerprint (rule 9).
type Env struct {
	Fingerprint   string         `json:"fingerprint"`
	Driver        string         `json:"driver"`
	CUDA          string         `json:"cuda"`
	Governor      string         `json:"governor"`
	GPU           map[string]any `json:"gpu"`
	Host          map[string]any `json:"host"`
	Versions      map[string]any `json:"versions"`
	LoadAvgStart  float64        `json:"loadavg1_start"`     // informational (ADR 0007)
	CPUBusyStart  *float64       `json:"cpu_busy_pct_start"` // last load-guard measurement; nil if guards skipped
	GPUTempStartC *float64       `json:"gpu_temp_start_c"`
	Harness       string         `json:"harness"`
}

// Record is one line of runs.jsonl.
type Record struct {
	RunID            string            `json:"run_id"`
	SessionID        string            `json:"session_id"`
	TS               string            `json:"ts"`
	Contestant       string            `json:"contestant"`
	Track            string            `json:"track"`
	Task             string            `json:"task"`
	Scale            string            `json:"scale"`
	Rows             int64             `json:"rows"`
	Rep              int               `json:"rep"`
	Warmup           bool              `json:"warmup"`
	AntiPattern      bool              `json:"anti_pattern"`
	Status           string            `json:"status"`
	StatusDetail     string            `json:"status_detail,omitempty"`
	Verify           Verify            `json:"verify"`
	WallS            float64           `json:"wall_s"`
	CPUUserS         float64           `json:"cpu_user_s"`
	CPUSysS          float64           `json:"cpu_sys_s"`
	MaxRSSMB         float64           `json:"max_rss_mb"`        // peak summed RSS of the process tree
	MaxRSSSingleMB   float64           `json:"max_rss_single_mb"` // wait4 ru_maxrss (largest single process)
	VRAMPeakMB       float64           `json:"vram_peak_mb"`      // device-level peak minus pre-run usage
	EnergyJ          Domains           `json:"energy_j"`
	IdleW            Domains           `json:"idle_w"`
	EnergyNetJ       Domains           `json:"energy_net_j"`
	EnergyConfidence string            `json:"energy_confidence"`
	EnergyCheck      EnergyCheck       `json:"energy_check"`
	Stages           []Stage           `json:"stages"`
	Quality          map[string]any    `json:"quality"`
	Throughput       Throughput        `json:"throughput"`
	Env              Env               `json:"env"`
	Config           Config            `json:"config"`
	Result           *ContestantResult `json:"result"`
	ExitCode         int               `json:"exit_code"`
	Signal           string            `json:"signal,omitempty"`
	LogPath          string            `json:"log_path"`
	OutputPath       string            `json:"output_path,omitempty"`
}

// Derive fills throughput from wall time, rows and total gross energy.
func (r *Record) Derive() {
	if r.WallS > 0 && r.Rows > 0 {
		v := float64(r.Rows) / r.WallS
		r.Throughput.RowsPerS = &v
	}
	if tot := r.EnergyJ.Total(); tot != nil && *tot > 0 && r.Rows > 0 {
		perKJ := float64(r.Rows) / (*tot / 1000)
		perRow := *tot / float64(r.Rows)
		r.Throughput.RowsPerKJ, r.Throughput.JPerRow = &perKJ, &perRow
	}
}

// NewID returns a random RFC 4122 v4 UUID.
func NewID() string {
	var b [16]byte
	_, _ = rand.Read(b[:]) // crypto/rand.Read never fails on Linux
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// Store appends JSON lines durably (O_APPEND + fsync per record).
type Store struct {
	mu   sync.Mutex
	path string
}

// NewStore creates the parent directory if needed.
func NewStore(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, err
	}
	return &Store{path: path}, nil
}

// Path returns the file path.
func (s *Store) Path() string { return s.path }

// Append writes v as one JSON line and fsyncs.
func (s *Store) Append(v any) error {
	line, err := json.Marshal(v)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	f, err := os.OpenFile(s.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if _, err := f.Write(append(line, '\n')); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}

// ReadRecords reads all records; a missing file is an empty list. A truncated last line
// (crash mid-write) is reported as an error naming the line.
func ReadRecords(path string) ([]Record, error) {
	f, err := os.Open(path) //nolint:gosec // G304: results path from config
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()
	return decodeLines(f)
}

func decodeLines(r io.Reader) ([]Record, error) {
	var out []Record
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 1<<20), 64<<20)
	for n := 1; sc.Scan(); n++ {
		if len(sc.Bytes()) == 0 {
			continue
		}
		var rec Record
		if err := json.Unmarshal(sc.Bytes(), &rec); err != nil {
			return out, fmt.Errorf("line %d: %w", n, err)
		}
		out = append(out, rec)
	}
	return out, sc.Err()
}
