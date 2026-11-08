package zjmark

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGoldenSequenceMatchesSpec(t *testing.T) {
	golden, err := os.ReadFile(filepath.Join("..", "..", "..", "spec", "zjmark_golden.txt"))
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	for _, stage := range []string{"parse", "embed"} {
		end, err := stageTo(&buf, stage)
		if err != nil {
			t.Fatal(err)
		}
		end()
	}
	res, err := ResultLine(3, "Prüfung <ok> & fertig", map[string]any{
		"threads": 16, "engine": "cpu", "fallback_ops": []string{"Series.apply"},
	})
	if err != nil {
		t.Fatal(err)
	}
	buf.WriteString(res + "\n")
	if got := buf.String(); got != string(golden) {
		t.Fatalf("golden mismatch:\n got: %q\nwant: %q", got, golden)
	}
}

func TestInvalidStageNames(t *testing.T) {
	for _, bad := range []string{"", "Parse", "1parse", "pa rse", "größe", strings.Repeat("a", 33)} {
		if _, err := StartLine(bad); !errors.Is(err, ErrInvalidStage) {
			t.Errorf("StartLine(%q): want ErrInvalidStage, got %v", bad, err)
		}
	}
	if _, err := StartLine(strings.Repeat("a", 32)); err != nil {
		t.Errorf("32-char name should be valid: %v", err)
	}
}

func TestResultLineNilExtraAndNegativeRows(t *testing.T) {
	got, err := ResultLine(0, "", nil)
	if err != nil || got != `ZJRESULT {"extra":{},"notes":"","rows":0}` {
		t.Fatalf("got %q, %v", got, err)
	}
	if _, err := ResultLine(-1, "", nil); err == nil {
		t.Fatal("negative rows must fail")
	}
}

func TestParseMarker(t *testing.T) {
	tests := []struct {
		line    string
		ok      bool
		wantErr bool
		want    Marker
	}{
		{line: `ZJMARK {"ev":"start","stage":"parse"}`, ok: true, want: Marker{Ev: "start", Stage: "parse"}},
		{line: `ZJMARK {"stage":"embed","ev":"end"}` + "\r\n", ok: true, want: Marker{Ev: "end", Stage: "embed"}},
		{line: `some other stderr output`, ok: false},
		{line: `ZJRESULT {"extra":{},"notes":"","rows":1}`, ok: false},
		{line: `ZJMARK {"ev":"pause","stage":"parse"}`, ok: true, wantErr: true},
		{line: `ZJMARK {"ev":"start","stage":"Parse"}`, ok: true, wantErr: true},
		{line: `ZJMARK {"ev":"start","stage":"parse","ts":1}`, ok: true, wantErr: true},
		{line: `ZJMARK {"ev":"start","stage":"parse"} {}`, ok: true, wantErr: true},
		{line: `ZJMARK not json`, ok: true, wantErr: true},
	}
	for _, tt := range tests {
		got, ok, err := ParseMarker(tt.line)
		if ok != tt.ok || (err != nil) != tt.wantErr || (!tt.wantErr && got != tt.want) {
			t.Errorf("ParseMarker(%q) = %+v, %v, %v; want %+v, %v, err=%v", tt.line, got, ok, err, tt.want, tt.ok, tt.wantErr)
		}
	}
}

func TestParseResult(t *testing.T) {
	r, ok, err := ParseResult(`ZJRESULT {"extra":{"threads":16},"notes":"Prüfung","rows":3}`)
	if !ok || err != nil || r.Rows != 3 || r.Notes != "Prüfung" || r.Extra["threads"] == nil {
		t.Fatalf("got %+v, %v, %v", r, ok, err)
	}
	for _, bad := range []string{
		`ZJRESULT {"notes":"","rows":3}`,
		`ZJRESULT {"extra":{},"rows":3}`,
		`ZJRESULT {"extra":{},"notes":""}`,
		`ZJRESULT {"extra":{},"notes":"","rows":-1}`,
		`ZJRESULT {"extra":{},"notes":"","rows":1.5}`,
		`ZJRESULT {"extra":{},"notes":"","rows":1,"x":0}`,
	} {
		if _, ok, err := ParseResult(bad); !ok || err == nil {
			t.Errorf("ParseResult(%q): want ok=true with error, got ok=%v err=%v", bad, ok, err)
		}
	}
}

// FuzzParseMarker: never panics, and every accepted marker re-encodes to a line that parses back equal.
func FuzzParseMarker(f *testing.F) {
	f.Add(`ZJMARK {"ev":"start","stage":"parse"}`)
	f.Add(`ZJMARK {"ev":"end","stage":"a_1"}`)
	f.Add(`ZJMARK {`)
	f.Fuzz(func(t *testing.T, line string) {
		m, ok, err := ParseMarker(line)
		if !ok || err != nil {
			return
		}
		re, err := markerLine(m.Ev, m.Stage)
		if err != nil {
			t.Fatalf("accepted marker %+v does not re-encode: %v", m, err)
		}
		m2, _, err := ParseMarker(re)
		if err != nil || m2 != m {
			t.Fatalf("round trip %+v -> %q -> %+v, %v", m, re, m2, err)
		}
	})
}
