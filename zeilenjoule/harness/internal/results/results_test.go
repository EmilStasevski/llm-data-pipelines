package results

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

func f(v float64) *float64 { return &v }

func TestTotalAndNet(t *testing.T) {
	gross := Domains{GPU: f(100), CPUPkg: f(50), CPUCore: f(30)}
	if got := *gross.Total(); got != 150 {
		t.Errorf("Total = %v, want 150 (core is part of pkg)", got)
	}
	if (Domains{CPUCore: f(1)}).Total() != nil {
		t.Error("Total must be nil when neither gpu nor cpu_pkg measured")
	}
	net := Net(gross, Domains{GPU: f(6), CPUPkg: f(10)}, 5)
	if *net.GPU != 70 || *net.CPUPkg != 0 || net.CPUCore != nil || net.DRAM != nil {
		t.Errorf("Net = %+v", net)
	}
}

func TestDerive(t *testing.T) {
	r := Record{Rows: 1000, WallS: 2, EnergyJ: Domains{GPU: f(10), CPUPkg: f(40)}}
	r.Derive()
	if *r.Throughput.RowsPerS != 500 || *r.Throughput.RowsPerKJ != 20000 || *r.Throughput.JPerRow != 0.05 {
		t.Errorf("throughput = %+v / %v / %v", *r.Throughput.RowsPerS, *r.Throughput.RowsPerKJ, *r.Throughput.JPerRow)
	}
	z := Record{Rows: 0, WallS: 1}
	z.Derive()
	if z.Throughput.RowsPerS != nil || z.Throughput.RowsPerKJ != nil {
		t.Error("zero rows must leave throughput nil")
	}
}

func TestUnmeasuredSerializesAsNull(t *testing.T) {
	b, err := json.Marshal(Record{EnergyJ: Domains{GPU: f(1)}})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), `"energy_j":{"gpu":1,"cpu_pkg":null,"cpu_core":null,"dram":null,"wall":null}`) {
		t.Errorf("unmeasured domains must be null, got %s", b)
	}
}

func TestNewID(t *testing.T) {
	re := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	seen := map[string]bool{}
	for range 1000 {
		id := NewID()
		if !re.MatchString(id) || seen[id] {
			t.Fatalf("bad or duplicate id %q", id)
		}
		seen[id] = true
	}
}

func TestStoreRoundTripAndTruncation(t *testing.T) {
	p := filepath.Join(t.TempDir(), "sub", "runs.jsonl")
	s, err := NewStore(p)
	if err != nil {
		t.Fatal(err)
	}
	for i := range 3 {
		if err := s.Append(Record{RunID: NewID(), Rep: i, Status: StatusOK}); err != nil {
			t.Fatal(err)
		}
	}
	recs, err := ReadRecords(p)
	if err != nil || len(recs) != 3 || recs[2].Rep != 2 {
		t.Fatalf("read %d records, %v", len(recs), err)
	}
	// simulate a crash mid-write
	fh, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0o600)
	_, _ = fh.WriteString(`{"run_id":"trunc`)
	_ = fh.Close()
	recs, err = ReadRecords(p)
	if err == nil || !strings.Contains(err.Error(), "line 4") || len(recs) != 3 {
		t.Fatalf("want error on line 4 and 3 good records, got %d, %v", len(recs), err)
	}
	if recs, err := ReadRecords(filepath.Join(t.TempDir(), "missing.jsonl")); err != nil || recs != nil {
		t.Fatalf("missing file: %v %v", recs, err)
	}
}
