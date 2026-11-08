// Package zjmark emits and parses ZeilenJoule stage markers and result lines (spec/zjmark.md).
//
// Contestants written in Go use Stage / EmitResult; the harness uses ParseMarker / ParseResult
// on the lines it reads from a contestant's stderr / stdout.
package zjmark

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"
)

const (
	markPrefix   = "ZJMARK "
	resultPrefix = "ZJRESULT "
	EvStart      = "start"
	EvEnd        = "end"
)

var stageRE = regexp.MustCompile(`^[a-z][a-z0-9_]{0,31}$`)

// ErrInvalidStage reports a stage name that violates ^[a-z][a-z0-9_]{0,31}$.
var ErrInvalidStage = errors.New("invalid stage name")

// Marker is one ZJMARK event.
type Marker struct {
	Ev    string `json:"ev"`
	Stage string `json:"stage"`
}

// Result is the final ZJRESULT payload.
type Result struct {
	Extra map[string]any `json:"extra"`
	Notes string         `json:"notes"`
	Rows  int64          `json:"rows"`
}

// Canonical encodes v as compact JSON with sorted map keys and without HTML escaping.
// Struct fields must be declared in lexicographic JSON-name order (as Marker and Result are).
// Known difference to the Python/Rust helpers: Go always escapes U+2028/U+2029.
func Canonical(v any) (string, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return "", err
	}
	return strings.TrimSuffix(buf.String(), "\n"), nil
}

func checkStage(name string) error {
	if !stageRE.MatchString(name) {
		return fmt.Errorf("%w %q: must match %s", ErrInvalidStage, name, stageRE)
	}
	return nil
}

func markerLine(ev, stage string) (string, error) {
	if err := checkStage(stage); err != nil {
		return "", err
	}
	s, err := Canonical(Marker{Ev: ev, Stage: stage})
	return markPrefix + s, err
}

// StartLine returns the ZJMARK start line for stage.
func StartLine(stage string) (string, error) { return markerLine(EvStart, stage) }

// EndLine returns the ZJMARK end line for stage.
func EndLine(stage string) (string, error) { return markerLine(EvEnd, stage) }

// ResultLine returns the ZJRESULT line. A nil extra is encoded as {}.
func ResultLine(rows int64, notes string, extra map[string]any) (string, error) {
	if rows < 0 {
		return "", fmt.Errorf("rows must be >= 0, got %d", rows)
	}
	if extra == nil {
		extra = map[string]any{}
	}
	s, err := Canonical(Result{Extra: extra, Notes: notes, Rows: rows})
	return resultPrefix + s, err
}

// Stage writes the start marker to stderr and returns a func that writes the end marker.
// Typical use: end, err := zjmark.Stage("parse"); if err != nil {…}; defer end().
func Stage(name string) (func(), error) {
	return stageTo(os.Stderr, name)
}

func stageTo(w io.Writer, name string) (func(), error) {
	start, err := StartLine(name)
	if err != nil {
		return nil, err
	}
	end, _ := EndLine(name) // same name, cannot fail
	// os.Stderr is unbuffered: each Fprintln reaches the harness immediately.
	if _, err := fmt.Fprintln(w, start); err != nil {
		return nil, err
	}
	return func() { _, _ = fmt.Fprintln(w, end) }, nil
}

// EmitResult prints the single final ZJRESULT line on stdout.
func EmitResult(rows int64, notes string, extra map[string]any) error {
	line, err := ResultLine(rows, notes, extra)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(os.Stdout, line)
	return err
}

func strictDecode(s string, v any) error {
	dec := json.NewDecoder(strings.NewReader(s))
	dec.DisallowUnknownFields()
	dec.UseNumber()
	if err := dec.Decode(v); err != nil {
		return err
	}
	if dec.More() {
		return errors.New("trailing data after JSON object")
	}
	return nil
}

// ParseMarker parses a ZJMARK line. ok is false (and err nil) if line is not a ZJMARK line at all.
func ParseMarker(line string) (m Marker, ok bool, err error) {
	payload, found := strings.CutPrefix(strings.TrimRight(line, "\r\n"), markPrefix)
	if !found {
		return Marker{}, false, nil
	}
	if err := strictDecode(payload, &m); err != nil {
		return Marker{}, true, fmt.Errorf("ZJMARK: %w", err)
	}
	if m.Ev != EvStart && m.Ev != EvEnd {
		return Marker{}, true, fmt.Errorf("ZJMARK: ev must be %q or %q, got %q", EvStart, EvEnd, m.Ev)
	}
	if err := checkStage(m.Stage); err != nil {
		return Marker{}, true, fmt.Errorf("ZJMARK: %w", err)
	}
	return m, true, nil
}

// ParseResult parses a ZJRESULT line. ok is false (and err nil) if line is not a ZJRESULT line.
// All three keys are required.
func ParseResult(line string) (r Result, ok bool, err error) {
	payload, found := strings.CutPrefix(strings.TrimRight(line, "\r\n"), resultPrefix)
	if !found {
		return Result{}, false, nil
	}
	var raw struct {
		Extra map[string]any `json:"extra"`
		Notes *string        `json:"notes"`
		Rows  *json.Number   `json:"rows"`
	}
	if err := strictDecode(payload, &raw); err != nil {
		return Result{}, true, fmt.Errorf("ZJRESULT: %w", err)
	}
	if raw.Extra == nil || raw.Notes == nil || raw.Rows == nil {
		return Result{}, true, errors.New("ZJRESULT: keys extra, notes and rows are all required")
	}
	rows, err := raw.Rows.Int64()
	if err != nil || rows < 0 {
		return Result{}, true, fmt.Errorf("ZJRESULT: rows must be a non-negative integer, got %s", raw.Rows)
	}
	return Result{Extra: raw.Extra, Notes: *raw.Notes, Rows: rows}, true, nil
}
