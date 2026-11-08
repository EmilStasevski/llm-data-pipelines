package runner

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"

	"zeilenjoule/harness/internal/procs"
	"zeilenjoule/harness/pkg/zjmark"
)

// Marker is a ZJMARK event, timestamped by the harness on receipt.
type Marker struct {
	zjmark.Marker
	At time.Duration // on the sampler clock
}

// ExecSpec describes one contestant process.
type ExecSpec struct {
	Argv     []string
	Dir      string
	Env      []string
	Timeout  time.Duration
	Log      io.Writer            // non-marker stderr + non-result stdout go here
	Clock    func() time.Duration // sampler clock: markers and start/end use it
	Tree     procs.FS             // process-tree RSS polling
	TreePoll time.Duration        // 0 = no polling
	OnStart  func(pid int)        // optional hook (tests)
}

// ExecResult is everything observed about the process.
type ExecResult struct {
	Start, End     time.Duration
	ExitCode       int
	Signal         string
	TimedOut       bool
	Interrupted    bool
	StartErr       error
	Markers        []Marker
	ProtocolErrors []string
	Result         *zjmark.Result
	ResultLines    int
	Rusage         *syscall.Rusage
	PeakTreeRSS    uint64
	LeftoverKilled bool
}

// Execute runs the process to completion, timeout or ctx cancellation.
func Execute(ctx context.Context, s ExecSpec) ExecResult {
	var res ExecResult
	if len(s.Argv) == 0 {
		res.StartErr = errors.New("empty argv")
		return res
	}
	outR, outW, err := os.Pipe()
	if err != nil {
		res.StartErr = err
		return res
	}
	errR, errW, err := os.Pipe()
	if err != nil {
		res.StartErr = err
		return res
	}
	// Not CommandContext: on timeout/cancel we kill the whole process group ourselves, CommandContext
	// would only kill the leader and leave e.g. the python child of `uv run` behind.
	cmd := exec.Command(s.Argv[0], s.Argv[1:]...) //nolint:gosec,noctx // G204: argv from the registry; noctx: see above
	cmd.Dir, cmd.Env = s.Dir, s.Env
	cmd.Stdout, cmd.Stderr = outW, errW // *os.File: no copy goroutines inside exec, Wait returns on exit
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	if err := cmd.Start(); err != nil {
		for _, f := range []*os.File{outR, outW, errR, errW} {
			_ = f.Close()
		}
		res.StartErr = err
		return res
	}
	res.Start = s.Clock()
	_ = outW.Close() // the child holds its own copies
	_ = errW.Close()
	pid := cmd.Process.Pid
	if s.OnStart != nil {
		s.OnStart(pid)
	}

	// Reader goroutines write only into col (under mu); res is filled from a snapshot at the end,
	// so a reader that is still running after the drain timeout cannot race with the caller.
	var mu sync.Mutex
	var col struct {
		markers []Marker
		perrs   []string
		result  *zjmark.Result
		lines   int
	}
	logLine := func(prefix, line string) {
		if s.Log == nil {
			return
		}
		mu.Lock()
		_, _ = fmt.Fprintf(s.Log, "%s %s\n", prefix, line)
		mu.Unlock()
	}
	var readers sync.WaitGroup
	readers.Add(2)
	go func() { // stderr: markers
		defer readers.Done()
		sc := bufio.NewScanner(errR)
		sc.Buffer(make([]byte, 0, 64<<10), 16<<20)
		for sc.Scan() {
			at := s.Clock()
			line := sc.Text()
			m, ok, err := zjmark.ParseMarker(line)
			switch {
			case ok && err == nil:
				mu.Lock()
				col.markers = append(col.markers, Marker{Marker: m, At: at})
				mu.Unlock()
			case ok:
				mu.Lock()
				col.perrs = append(col.perrs, err.Error())
				mu.Unlock()
				logLine("[stderr]", line)
			default:
				logLine("[stderr]", line)
			}
		}
	}()
	go func() { // stdout: result line
		defer readers.Done()
		sc := bufio.NewScanner(outR)
		sc.Buffer(make([]byte, 0, 64<<10), 16<<20)
		for sc.Scan() {
			line := sc.Text()
			r, ok, err := zjmark.ParseResult(line)
			switch {
			case ok && err == nil:
				mu.Lock()
				col.result, col.lines = &r, col.lines+1
				mu.Unlock()
			case ok:
				mu.Lock()
				col.perrs = append(col.perrs, err.Error())
				mu.Unlock()
			}
			logLine("[stdout]", line)
		}
	}()

	stopPoll := make(chan struct{})
	pollDone := make(chan struct{})
	go func() {
		defer close(pollDone)
		if s.TreePoll <= 0 || s.Tree.ReadFile == nil {
			return
		}
		t := time.NewTicker(s.TreePoll)
		defer t.Stop()
		for {
			if b, _ := s.Tree.TreeRSS(pid); b > res.PeakTreeRSS {
				res.PeakTreeRSS = b // only this goroutine writes until pollDone
			}
			select {
			case <-stopPoll:
				return
			case <-t.C:
			}
		}
	}()

	killGroup := func() { _ = syscall.Kill(-pid, syscall.SIGKILL) }
	waitErr := make(chan error, 1)
	go func() { waitErr <- cmd.Wait() }()
	var timeout <-chan time.Time
	if s.Timeout > 0 {
		timer := time.NewTimer(s.Timeout)
		defer timer.Stop()
		timeout = timer.C
	}
	var werr error
	select {
	case werr = <-waitErr:
	case <-timeout:
		res.TimedOut = true
		killGroup()
		werr = <-waitErr
	case <-ctx.Done():
		res.Interrupted = true
		killGroup()
		werr = <-waitErr
	}
	// The exit status itself is read from ProcessState below; anything else is unexpected.
	var exitErr *exec.ExitError
	if werr != nil && !errors.As(werr, &exitErr) {
		mu.Lock()
		col.perrs = append(col.perrs, "wait: "+werr.Error())
		mu.Unlock()
	}
	res.End = s.Clock()
	close(stopPoll)
	<-pollDone

	// Anything left in the process group (daemonized helpers, orphans) is killed so it cannot
	// pollute the next run; this also closes pipe ends they still hold.
	if syscall.Kill(-pid, 0) == nil {
		res.LeftoverKilled = !res.TimedOut && !res.Interrupted
		killGroup()
	}
	readersDone := make(chan struct{})
	go func() { readers.Wait(); close(readersDone) }()
	drainTimeout := false
	select {
	case <-readersDone:
	case <-time.After(5 * time.Second):
		drainTimeout = true
	}
	_ = outR.Close()
	_ = errR.Close()
	mu.Lock()
	res.Markers = slices.Clone(col.markers)
	res.ProtocolErrors = slices.Clone(col.perrs)
	res.Result, res.ResultLines = col.result, col.lines
	mu.Unlock()
	if drainTimeout {
		res.ProtocolErrors = append(res.ProtocolErrors, "output pipes still open 5 s after exit (a process outside the group holds them)")
	}

	if ps := cmd.ProcessState; ps != nil {
		if ru, ok := ps.SysUsage().(*syscall.Rusage); ok {
			res.Rusage = ru
		}
		if ws, ok := ps.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			res.ExitCode, res.Signal = -1, ws.Signal().String()
		} else {
			res.ExitCode = ps.ExitCode()
		}
	}
	if res.ResultLines > 1 {
		res.ProtocolErrors = append(res.ProtocolErrors, fmt.Sprintf("%d ZJRESULT lines (exactly one allowed)", res.ResultLines))
	}
	return res
}

// Substitute replaces {name} placeholders in each argument.
func Substitute(args []string, vals map[string]string) []string {
	out := make([]string, len(args))
	for i, a := range args {
		for k, v := range vals {
			a = strings.ReplaceAll(a, "{"+k+"}", v)
		}
		out[i] = a
	}
	return out
}
