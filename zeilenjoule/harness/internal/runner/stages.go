package runner

import (
	"fmt"
	"time"

	"zeilenjoule/harness/pkg/zjmark"
)

// Span is a completed stage.
type Span struct {
	Name       string
	Depth      int
	Start, End time.Duration
}

// PairStages matches start/end markers. Stages may nest but must not interleave (spec/zjmark.md).
func PairStages(ms []Marker) ([]Span, []string) {
	type open struct {
		name string
		at   time.Duration
	}
	var stack []open
	var spans []Span
	var errs []string
	for _, m := range ms {
		switch m.Ev {
		case zjmark.EvStart:
			stack = append(stack, open{m.Stage, m.At})
		case zjmark.EvEnd:
			if len(stack) == 0 || stack[len(stack)-1].name != m.Stage {
				errs = append(errs, fmt.Sprintf("end %q does not match the innermost open stage", m.Stage))
				continue
			}
			top := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			spans = append(spans, Span{Name: top.name, Depth: len(stack), Start: top.at, End: m.At})
		}
	}
	for _, o := range stack {
		errs = append(errs, fmt.Sprintf("stage %q never ended", o.name))
	}
	return spans, errs
}
