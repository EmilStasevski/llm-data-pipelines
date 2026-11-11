// Package runner executes contestants under measurement (PLAN §5): load guard, cooldown, energy
// sampling, ZJMARK stage integration, timeout/OOM classification, verification and records.
package runner

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"zeilenjoule/harness/internal/config"
	"zeilenjoule/harness/internal/cpustat"
	"zeilenjoule/harness/internal/energy"
	"zeilenjoule/harness/internal/procs"
	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/internal/verify"
)

// ErrInterrupted means the user stopped the run; no record is written (resume repeats it).
var ErrInterrupted = errors.New("interrupted")

// ErrBusy means the load guard waited max_wait_s without the machine becoming quiet.
var ErrBusy = errors.New("machine busy: load guard timed out")

// Runner holds everything needed to measure runs. GPU may be nil (then GPU energy is null).
type Runner struct {
	Repo     string
	Bench    *config.Bench
	Tasks    map[string]config.Task
	GPU      energy.GPU
	Zones    []energy.RAPLZone
	ReadFile func(string) ([]byte, error)
	Tree     procs.FS
	Store    *results.Store // nil = do not persist (zj verify)
	Log      *slog.Logger
	Host     results.Env // static part of the fingerprint, filled by CollectEnv
	Idle     *IdleRecord // latest idle baseline (nil = E_net not available)

	// SkipGuards disables load guard and cooldown (zj verify, tests).
	SkipGuards bool
}

// RunSpec is one run request.
type RunSpec struct {
	Contestant config.Contestant
	Scale      string
	Rep        int
	Warmup     bool
	SessionID  string
	Threads    int
	Versions   any // output of versions_cmd for this contestant
}

func (r *Runner) logger() *slog.Logger {
	if r.Log != nil {
		return r.Log
	}
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func loadavg1(read func(string) ([]byte, error)) float64 {
	b, err := read("/proc/loadavg")
	if err != nil {
		return math.NaN()
	}
	f := strings.Fields(string(b))
	if len(f) == 0 {
		return math.NaN()
	}
	v, _ := strconv.ParseFloat(f[0], 64)
	return v
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// measureBusy returns the CPU busy % of all CPUs over window (NaN if unreadable).
func (r *Runner) measureBusy(ctx context.Context, window time.Duration) (float64, error) {
	a, err := cpustat.Read(r.ReadFile)
	if err != nil {
		return math.NaN(), nil //nolint:nilerr // unreadable /proc/stat → NaN → treated as not quiet
	}
	if err := sleepCtx(ctx, window); err != nil {
		return math.NaN(), ErrInterrupted
	}
	b, err := cpustat.Read(r.ReadFile)
	if err != nil {
		return math.NaN(), nil //nolint:nilerr // see above
	}
	return cpustat.BusyPct(a, b), nil
}

func (r *Runner) quiet(busyPct float64) bool {
	return !math.IsNaN(busyPct) && busyPct <= r.Bench.LoadGuard.MaxBusyPct
}

// waitForQuiet blocks until the CPU busy fraction over window_s is <= max_busy_pct (ADR 0007;
// replaces PLAN §5.2's loadavg1 check, which lagged ~3.5 min behind CPU-heavy runs).
// It returns the last measured busy %.
func (r *Runner) waitForQuiet(ctx context.Context) (float64, error) {
	window := time.Duration(r.Bench.LoadGuard.WindowS * float64(time.Second))
	deadline := time.Now().Add(time.Duration(r.Bench.LoadGuard.MaxWaitS) * time.Second)
	for logged := false; ; {
		busy, err := r.measureBusy(ctx, window)
		if err != nil {
			return busy, err
		}
		if r.quiet(busy) {
			return busy, nil
		}
		if time.Now().After(deadline) {
			return busy, fmt.Errorf("%w: CPU busy %.1f %% > %.1f %% for %d s", ErrBusy, busy, r.Bench.LoadGuard.MaxBusyPct, r.Bench.LoadGuard.MaxWaitS)
		}
		if !logged {
			r.logger().Info("load guard: waiting", "cpu_busy_pct", round3(busy), "max", r.Bench.LoadGuard.MaxBusyPct)
			logged = true
		}
	}
}

// coolDown waits until the GPU is below gpu_temp_c or max_wait_s passed (PLAN §5.2).
func (r *Runner) coolDown(ctx context.Context) (startTemp *float64, err error) {
	if r.GPU == nil {
		return nil, nil
	}
	deadline := time.Now().Add(time.Duration(r.Bench.Cooldown.MaxWaitS) * time.Second)
	for {
		t, terr := r.GPU.TempC()
		if terr != nil {
			return nil, nil
		}
		tf := float64(t)
		if tf < r.Bench.Cooldown.GPUTempC || time.Now().After(deadline) || r.SkipGuards {
			return &tf, nil
		}
		if err := sleepCtx(ctx, 2*time.Second); err != nil {
			return nil, ErrInterrupted
		}
	}
}

// Run measures one run and (if Store is set) appends its record.
func (r *Runner) Run(ctx context.Context, spec RunSpec) (results.Record, error) {
	c := spec.Contestant
	task, ok := r.Tasks[c.Task]
	if !ok {
		return results.Record{}, fmt.Errorf("task %q not defined in tasks.yaml", c.Task)
	}
	rows := int64(r.Bench.Scales[spec.Scale])
	if task.NoRows {
		rows = 0
	}
	threads := spec.Threads
	if threads <= 0 {
		threads = runtime.NumCPU()
	}
	var busyStart *float64
	if !r.SkipGuards {
		busy, err := r.waitForQuiet(ctx)
		if err != nil {
			return results.Record{}, err
		}
		busyStart = &busy
	}
	startTemp, err := r.coolDown(ctx)
	if err != nil {
		return results.Record{}, err
	}

	runID := results.NewID()
	outRoot, err := r.Bench.Runs.OutputDirAbs()
	if err != nil {
		return results.Record{}, err
	}
	outDir := filepath.Join(outRoot, spec.SessionID, runID)
	if err := os.MkdirAll(outDir, 0o750); err != nil {
		return results.Record{}, err
	}
	outPath := filepath.Join(outDir, task.Output)
	logDir := filepath.Join(r.Repo, "logs", "runs", spec.SessionID)
	if err := os.MkdirAll(logDir, 0o750); err != nil {
		return results.Record{}, err
	}
	logPath := filepath.Join(logDir, runID+".log")
	logFile, err := os.Create(logPath) //nolint:gosec // G304: harness-owned log path
	if err != nil {
		return results.Record{}, err
	}
	defer func() { _ = logFile.Close() }()

	input := ""
	if task.Input != "" {
		input = filepath.Join(r.Repo, strings.ReplaceAll(task.Input, "{scale}", spec.Scale))
	}
	vals := map[string]string{
		"input": input, "output": outPath, "threads": strconv.Itoa(threads),
		"rows": strconv.FormatInt(rows, 10), "scale": spec.Scale,
	}
	argv := Substitute(append(append([]string{}, c.Cmd...), c.Args...), vals)
	_, _ = fmt.Fprintf(logFile, "# zj run %s %s rep=%d warmup=%v\n# argv: %q\n", c.ID, spec.Scale, spec.Rep, spec.Warmup, argv)

	load := loadavg1(r.ReadFile)
	var memBefore uint64
	if r.GPU != nil {
		memBefore, _ = r.GPU.MemUsedBytes()
	}
	sampler := energy.NewSampler(r.GPU, r.Zones, r.ReadFile, r.Bench.SampleHz)
	sampler.Start(ctx)
	settle := time.Duration(r.Bench.Runs.SettleS * float64(time.Second))
	_ = sleepCtx(ctx, settle)
	ts := time.Now().UTC().Format(time.RFC3339Nano)

	ex := Execute(ctx, ExecSpec{
		Argv: argv, Dir: r.Repo,
		Env:      append(os.Environ(), "ZJ_RUN_ID="+runID, "ZJ_THREADS="+strconv.Itoa(threads)),
		Timeout:  time.Duration(c.Timeout(r.Bench.TimeoutS)) * time.Second,
		Log:      logFile,
		Clock:    sampler.Since,
		Tree:     r.Tree,
		TreePoll: time.Duration(float64(time.Second) / r.Bench.Runs.TreePollHz),
	})
	if !ex.Interrupted {
		_ = sleepCtx(ctx, settle)
	}
	samples, readErrs := sampler.Stop()
	if ex.Interrupted {
		return results.Record{}, ErrInterrupted
	}

	rec := results.Record{
		RunID: runID, SessionID: spec.SessionID, TS: ts,
		Contestant: c.ID, Track: c.Track, Task: c.Task, Scale: spec.Scale, Rows: rows,
		Rep: spec.Rep, Warmup: spec.Warmup, AntiPattern: c.AntiPattern,
		ExitCode: ex.ExitCode, Signal: ex.Signal, LogPath: relTo(r.Repo, logPath), OutputPath: outPath,
		Config:  results.Config{Threads: threads},
		Quality: map[string]any{"f1": nil, "recall_at_10": nil, "ndcg_at_10": nil},
		Stages:  []results.Stage{},
	}
	rec.Env = r.Host
	rec.Env.LoadAvgStart = load // informational only (ADR 0007)
	rec.Env.CPUBusyStart = busyStart
	rec.Env.GPUTempStartC = startTemp
	rec.Env.Governor = currentGovernor(r.ReadFile)
	rec.Env.Versions = map[string]any{c.ID: spec.Versions}
	rec.Env.Fingerprint = fingerprint(rec.Env)

	if ex.StartErr == nil {
		r.measure(&rec, ex, samples, readErrs, memBefore)
	}
	r.classify(ctx, &rec, ex, task, c.Timeout(r.Bench.TimeoutS))
	rec.Derive()

	if r.Bench.Runs.KeepOutputs == "none" || (r.Bench.Runs.KeepOutputs == "failed" && rec.Status == results.StatusOK) {
		_ = os.RemoveAll(outDir) // harness-owned scratch dir under runs.output_dir
		rec.OutputPath = ""
	}
	if r.Store != nil {
		if err := r.Store.Append(rec); err != nil {
			return rec, fmt.Errorf("writing record: %w", err)
		}
	}
	r.logger().Info("run", "id", c.ID, "scale", spec.Scale, "rep", spec.Rep, "warmup", spec.Warmup,
		"status", rec.Status, "wall_s", round3(rec.WallS), "detail", rec.StatusDetail)
	return rec, nil
}

func (r *Runner) measure(rec *results.Record, ex ExecResult, samples []energy.Sample, readErrs int, memBefore uint64) {
	rec.WallS = (ex.End - ex.Start).Seconds()
	if ex.Rusage != nil {
		rec.CPUUserS = tvSeconds(ex.Rusage.Utime)
		rec.CPUSysS = tvSeconds(ex.Rusage.Stime)
		rec.MaxRSSSingleMB = float64(ex.Rusage.Maxrss) / 1024 // KiB on Linux
	}
	rec.MaxRSSMB = math.Max(float64(ex.PeakTreeRSS)/(1<<20), rec.MaxRSSSingleMB)

	series := energy.BuildSeries(samples, r.Zones, r.GPU != nil)
	w := series.Between(ex.Start, ex.End)
	rec.EnergyJ = domainsFromWindow(w, r.Zones)
	if r.Idle != nil {
		rec.IdleW = r.Idle.PowerW
		rec.EnergyNetJ = results.Net(rec.EnergyJ, rec.IdleW, rec.WallS)
	}
	if w.GPUPeakMemB != nil {
		rec.VRAMPeakMB = math.Max(0, (*w.GPUPeakMemB-float64(memBefore))/(1<<20))
	}
	rec.EnergyCheck = results.EnergyCheck{GPUCounterJ: w.GPUCounterJ, GPUIntegratedJ: w.GPUJ, Samples: w.Samples, ReadErrors: readErrs}
	if w.GPUJ != nil && w.GPUCounterJ != nil && *w.GPUJ > 0 {
		dev := (*w.GPUCounterJ - *w.GPUJ) / *w.GPUJ * 100 // counter relative to the primary source
		rec.EnergyCheck.DeviationPct = &dev
	}
	// PLAN §5.1: windows < 5 s are low confidence (consumer-card power telemetry is averaged, ~2 Hz).
	// The counter deviation is NOT a criterion: it is a known systematic of this card (ADR 0006)
	// and would mark every GPU-heavy run "low", making the flag meaningless.
	rec.EnergyConfidence = "high"
	if rec.WallS < 5 || readErrs > 0 {
		rec.EnergyConfidence = "low"
	}
	spans, _ := PairStages(ex.Markers) // marker protocol errors are classified in classify()
	for _, sp := range spans {
		sw := series.Between(sp.Start, sp.End)
		d := domainsFromWindow(sw, r.Zones)
		rec.Stages = append(rec.Stages, results.Stage{
			Name: sp.Name, Depth: sp.Depth, StartS: (sp.Start - ex.Start).Seconds(), TS: sw.Seconds,
			GPUJ: d.GPU, CPUJ: d.CPUPkg, DRAMJ: d.DRAM,
		})
	}
}

func (r *Runner) classify(ctx context.Context, rec *results.Record, ex ExecResult, task config.Task, timeoutS int) {
	_, perrs := PairStages(ex.Markers)
	protocol := append(append([]string{}, ex.ProtocolErrors...), perrs...)
	if ex.Result == nil && ex.StartErr == nil && !ex.TimedOut && ex.Signal == "" && ex.ExitCode == 0 {
		protocol = append(protocol, "no ZJRESULT line on stdout")
	}
	if ex.Result != nil {
		rec.Result = &results.ContestantResult{Rows: ex.Result.Rows, Notes: ex.Result.Notes, Extra: ex.Result.Extra}
	}
	fail := func(status, detail string) {
		rec.Status, rec.StatusDetail = status, detail
		rec.Verify = results.Verify{Detail: "not verified: run did not complete"}
	}
	switch {
	case ex.StartErr != nil:
		fail(results.StatusFail, "start: "+ex.StartErr.Error())
	case ex.TimedOut:
		fail(results.StatusDNF, fmt.Sprintf("timeout after %d s", timeoutS))
	case ex.Signal == syscall.SIGKILL.String():
		fail(results.StatusOOM, "SIGKILL not sent by the harness (likely the kernel OOM killer); peak RSS recorded")
	case ex.Signal != "":
		fail(results.StatusFail, "terminated by signal "+ex.Signal)
	case ex.ExitCode != 0:
		fail(results.StatusFail, fmt.Sprintf("exit code %d (see %s)", ex.ExitCode, rec.LogPath))
	case len(protocol) > 0:
		fail(results.StatusFail, "protocol: "+strings.Join(protocol, "; "))
	default:
		var passed bool
		var detail string
		if task.Verifier == "cmd" {
			vals := map[string]string{
				"output": rec.OutputPath, "scale": rec.Scale,
				"reference": filepath.Join(r.Repo, strings.ReplaceAll(task.Reference, "{scale}", rec.Scale)),
			}
			// outside the measured window: the sampler has already stopped
			var quality map[string]any
			passed, detail, quality = verify.RunCmdQuality(ctx, r.Repo, Substitute(task.VerifyCmd, vals), 15*time.Minute)
			for k, v := range quality { // verifier-computed metrics (Track C recall/nDCG, Track B gate)
				rec.Quality[k] = v
			}
		} else {
			passed, detail = verify.Run(task.Verifier, verify.Input{
				OutputPath: rec.OutputPath, ExpectedRows: rec.Rows, ResultRows: ex.Result.Rows,
			})
		}
		rec.Verify = results.Verify{Passed: &passed, Detail: detail}
		rec.Status = results.StatusOK
		if !passed {
			rec.Status, rec.StatusDetail = results.StatusDisqualified, "verify: "+detail
		}
		if ex.LeftoverKilled {
			rec.StatusDetail = strings.TrimPrefix(rec.StatusDetail+"; leftover processes in the group were killed after exit", "; ")
		}
	}
}

func domainsFromWindow(w energy.Window, zones []energy.RAPLZone) results.Domains {
	d := results.Domains{GPU: w.GPUJ}
	has := map[string]bool{}
	for _, z := range zones {
		has[z.Domain] = true
	}
	get := func(name string) *float64 {
		if !has[name] {
			return nil
		}
		v := w.Domains[name]
		return &v
	}
	d.CPUPkg, d.CPUCore, d.DRAM = get("cpu_pkg"), get("cpu_core"), get("dram")
	return d
}

func tvSeconds(tv syscall.Timeval) float64 { return float64(tv.Sec) + float64(tv.Usec)/1e6 }

func round3(v float64) float64 { return math.Round(v*1000) / 1000 }

func relTo(base, p string) string {
	if rel, err := filepath.Rel(base, p); err == nil {
		return rel
	}
	return p
}
