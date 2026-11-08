// Package calib evaluates the measurement calibration (PLAN §5.7) and renders docs/CALIBRATION.md.
// Every number in the report comes from the session's idle and run records (rule 3).
package calib

import (
	"bytes"
	"fmt"
	"math"
	"slices"
	"strings"
	"text/template"

	"zeilenjoule/harness/internal/results"
	"zeilenjoule/harness/internal/runner"
)

// Calibration contestant ids (config/contestants.yaml).
const (
	IDOverhead = "x04_sleep30_rs"
	IDCPU      = "x02_stressng_cpu"
	IDGPU      = "x03_gpuburn_torch"
)

// Stat is mean/stdev/CV over repeated idle measurements of one domain.
type Stat struct {
	Domain string  `json:"domain"`
	N      int     `json:"n"`
	MeanW  float64 `json:"mean_w"`
	SDW    float64 `json:"sd_w"`
	CVPct  float64 `json:"cv_pct"`
	Stable bool    `json:"stable"` // CV <= 5 % (same threshold as the rep CV flag, PLAN §5.2)
}

// Check is one pass/fail line. Info lines are reported but not graded.
type Check struct {
	Name   string `json:"name"`
	Info   bool   `json:"info,omitempty"`
	Pass   *bool  `json:"pass"` // nil = could not be evaluated (source not measured)
	Value  string `json:"value"`
	Expect string `json:"expect"`
}

// Report is results/calibration/<session>.json.
type Report struct {
	SessionID string          `json:"session_id"`
	TS        string          `json:"ts"`
	Env       results.Env     `json:"env"`
	Idle      []Stat          `json:"idle"`
	IdleRuns  int             `json:"idle_runs"`
	PL1W      *float64        `json:"pl1_w"`
	PL2W      *float64        `json:"pl2_w"`
	Checks    []Check         `json:"checks"`
	Runs      map[string]Run  `json:"runs"`
	Baseline  results.Domains `json:"baseline_w"`
}

// Run is the subset of a calibration run shown in the report.
type Run struct {
	Status       string   `json:"status"`
	WallS        float64  `json:"wall_s"`
	StageS       *float64 `json:"stage_s"`
	GPUMeanW     *float64 `json:"gpu_mean_w"`
	CPUPkgMeanW  *float64 `json:"cpu_pkg_mean_w"`
	NetTotalJ    *float64 `json:"net_total_j"`
	DeviationPct *float64 `json:"gpu_counter_vs_integrated_pct"`
	PeakTempC    *float64 `json:"gpu_temp_start_c"`
}

func ptr[T any](v T) *T { return &v }

func meanSD(xs []float64) (mean, sd float64) {
	for _, x := range xs {
		mean += x
	}
	mean /= float64(len(xs))
	if len(xs) > 1 {
		for _, x := range xs {
			sd += (x - mean) * (x - mean)
		}
		sd = math.Sqrt(sd / float64(len(xs)-1))
	}
	return mean, sd
}

// IdleStats aggregates idle baselines per domain (domains missing in any run are skipped).
func IdleStats(idles []runner.IdleRecord) ([]Stat, results.Domains) {
	type acc struct {
		name string
		get  func(results.Domains) *float64
		set  func(*results.Domains, *float64)
	}
	accs := []acc{
		{"gpu", func(d results.Domains) *float64 { return d.GPU }, func(d *results.Domains, v *float64) { d.GPU = v }},
		{"cpu_pkg", func(d results.Domains) *float64 { return d.CPUPkg }, func(d *results.Domains, v *float64) { d.CPUPkg = v }},
		{"cpu_core", func(d results.Domains) *float64 { return d.CPUCore }, func(d *results.Domains, v *float64) { d.CPUCore = v }},
		{"dram", func(d results.Domains) *float64 { return d.DRAM }, func(d *results.Domains, v *float64) { d.DRAM = v }},
	}
	var stats []Stat
	var base results.Domains
	for _, a := range accs {
		var xs []float64
		for _, r := range idles {
			if v := a.get(r.PowerW); v != nil {
				xs = append(xs, *v)
			}
		}
		if len(xs) == 0 || len(xs) != len(idles) {
			continue
		}
		m, sd := meanSD(xs)
		cv := 0.0
		if m > 0 {
			cv = sd / m * 100
		}
		stats = append(stats, Stat{Domain: a.name, N: len(xs), MeanW: m, SDW: sd, CVPct: cv, Stable: cv <= 5})
		a.set(&base, ptr(m))
	}
	return stats, base
}

func stage(rec results.Record, name string) *results.Stage {
	for i := range rec.Stages {
		if rec.Stages[i].Name == name {
			return &rec.Stages[i]
		}
	}
	return nil
}

func perSecond(j *float64, s float64) *float64 {
	if j == nil || s <= 0 {
		return nil
	}
	return ptr(*j / s)
}

func summarize(rec results.Record, stageName string) Run {
	run := Run{Status: rec.Status, WallS: rec.WallS, DeviationPct: rec.EnergyCheck.DeviationPct, PeakTempC: rec.Env.GPUTempStartC}
	gpuJ, cpuJ, secs := rec.EnergyJ.GPU, rec.EnergyJ.CPUPkg, rec.WallS
	if st := stage(rec, stageName); st != nil {
		gpuJ, cpuJ, secs = st.GPUJ, st.CPUJ, st.TS
		run.StageS = ptr(st.TS)
	}
	run.GPUMeanW, run.CPUPkgMeanW = perSecond(gpuJ, secs), perSecond(cpuJ, secs)
	run.NetTotalJ = rec.EnergyNetJ.Total()
	return run
}

func verdict(ok bool) *bool { return &ok }

func fmtP(v *float64, unit string) string {
	if v == nil {
		return "nicht gemessen / not measured"
	}
	return fmt.Sprintf("%.2f %s", *v, unit)
}

// Evaluate computes the report from the session's records.
func Evaluate(sessionID, ts string, idles []runner.IdleRecord, recs []results.Record, pl1, pl2, gpuLimitW *float64) Report {
	rep := Report{SessionID: sessionID, TS: ts, IdleRuns: len(idles), PL1W: pl1, PL2W: pl2, Runs: map[string]Run{}}
	rep.Idle, rep.Baseline = IdleStats(idles)
	byID := map[string]results.Record{}
	var overhead []results.Record
	for _, r := range recs {
		if r.Warmup {
			continue
		}
		byID[r.Contestant] = r
		if r.Contestant == IDOverhead {
			overhead = append(overhead, r)
		}
	}
	if len(recs) > 0 {
		rep.Env = recs[0].Env
	}
	for _, s := range rep.Idle {
		rep.Checks = append(rep.Checks, Check{
			Name: "idle stability " + s.Domain, Pass: verdict(s.Stable),
			Value: fmt.Sprintf("%.2f ± %.2f W (CV %.1f %%, n=%d)", s.MeanW, s.SDW, s.CVPct, s.N), Expect: "CV ≤ 5 %",
		})
	}
	if r, ok := byID[IDCPU]; ok {
		run := summarize(r, "work")
		rep.Runs[IDCPU] = run
		c := Check{Name: "CPU load: RAPL package power plausible", Value: fmtP(run.CPUPkgMeanW, "W"),
			Expect: "idle < P ≤ PL2 + 5 %"}
		if run.CPUPkgMeanW != nil && pl2 != nil && rep.Baseline.CPUPkg != nil {
			c.Pass = verdict(r.Status == results.StatusOK && *run.CPUPkgMeanW > *rep.Baseline.CPUPkg && *run.CPUPkgMeanW <= *pl2*1.05)
			c.Expect = fmt.Sprintf("%.1f W < P ≤ %.1f W (PL2 %.0f W, PL1 %s)", *rep.Baseline.CPUPkg, *pl2*1.05, *pl2, fmtP(pl1, "W"))
		}
		rep.Checks = append(rep.Checks, c)
	}
	if r, ok := byID[IDGPU]; ok {
		run := summarize(r, "burn")
		rep.Runs[IDGPU] = run
		c := Check{Name: "GPU load: steady power vs enforced limit", Value: fmtP(run.GPUMeanW, "W"), Expect: "80–105 % of the power limit"}
		if run.GPUMeanW != nil && gpuLimitW != nil {
			c.Pass = verdict(r.Status == results.StatusOK && *run.GPUMeanW >= 0.8**gpuLimitW && *run.GPUMeanW <= 1.05**gpuLimitW)
			c.Expect = fmt.Sprintf("%.0f–%.0f W (80–105 %% of %.0f W)", 0.8**gpuLimitW, 1.05**gpuLimitW, *gpuLimitW)
		}
		rep.Checks = append(rep.Checks, c)
		rep.Checks = append(rep.Checks, Check{
			Name: "GPU: NVML energy counter vs power samples", Info: true, Value: fmtP(run.DeviationPct, "%"),
			Expect: "informational: counter reads low under load on this card (ADR 0006)",
		})
	}
	if len(overhead) > 0 {
		// Median over reps: a single 30 s window is fragile against short background bursts
		// (calibration 2: desktop activity added ~3.7 W to one window).
		walls, nets := make([]float64, 0, len(overhead)), []float64{}
		allOK, netAll := true, true
		for i, r := range overhead {
			run := summarize(r, "work")
			rep.Runs[fmt.Sprintf("%s #%d", IDOverhead, i+1)] = run
			walls = append(walls, r.WallS)
			allOK = allOK && r.Status == results.StatusOK
			if run.NetTotalJ == nil {
				netAll = false
			} else {
				nets = append(nets, *run.NetTotalJ)
			}
		}
		mw := median(walls)
		rep.Checks = append(rep.Checks, Check{
			Name: "harness overhead: wall time of `sleep 30`", Value: fmt.Sprintf("median %.3f s (%s)", mw, list(walls, "s")),
			Pass: verdict(allOK && mw-30 < 0.5), Expect: "30 s + < 0.5 s",
		})
		c := Check{Name: "harness overhead: E_net of `sleep 30`", Value: "nicht gemessen / not measured", Expect: "|median E_net| ≤ 2 W × 30 s = 60 J"}
		if netAll {
			mn := median(nets)
			c.Value = fmt.Sprintf("median %.1f J (%s)", mn, list(nets, "J"))
			c.Pass = verdict(math.Abs(mn) <= 60)
		}
		rep.Checks = append(rep.Checks, c)
	}
	return rep
}

// AllPass reports whether every evaluable check passed (unevaluable checks count as not passed).
func (r Report) AllPass() bool {
	for _, c := range r.Checks {
		if c.Info {
			continue
		}
		if c.Pass == nil || !*c.Pass {
			return false
		}
	}
	return len(r.Checks) > 0
}

var mdTmpl = template.Must(template.New("md").Funcs(template.FuncMap{
	"p": fmtP,
	"verdict": func(b *bool) string {
		switch {
		case b == nil:
			return "– (nicht gemessen)"
		case *b:
			return "✅"
		default:
			return "❌"
		}
	},
	"f2": func(v float64) string { return fmt.Sprintf("%.2f", v) },
	"f1": func(v float64) string { return fmt.Sprintf("%.1f", v) },
}).Parse(`# Kalibrierung / Calibration

> **Generiert von ` + "`zj calibrate`" + ` – nicht von Hand bearbeiten. / Generated by ` + "`zj calibrate`" + ` — do not edit.**
> Session ` + "`{{.SessionID}}`" + ` · {{.TS}} · Rohdaten: ` + "`results/calibration/{{.SessionID}}.json`" + `, ` + "`results/runs.jsonl`" + `, ` + "`results/idle.jsonl`" + `

Umgebung / environment: driver {{.Env.Driver}} · CUDA {{.Env.CUDA}} · governor ` + "`{{.Env.Governor}}`" + ` · fingerprint ` + "`{{.Env.Fingerprint}}`" + `

## Ergebnis / Verdict

| Prüfung / check | Wert / value | Erwartung / expected | |
|---|---|---|---|
{{range .Checks}}| {{.Name}} | {{.Value}} | {{.Expect}} | {{if .Info}}ℹ️{{else}}{{verdict .Pass}}{{end}} |
{{end}}
## Leerlauf / Idle baseline ({{.IdleRuns}} × Messung)

| Domäne / domain | Mittel / mean W | SD W | CV % | stabil / stable |
|---|---|---|---|---|
{{range .Idle}}| {{.Domain}} | {{f2 .MeanW}} | {{f2 .SDW}} | {{f1 .CVPct}} | {{if .Stable}}✅{{else}}❌{{end}} |
{{end}}
## Lastläufe / Load runs

| Lauf / run | Status | Wall s | Stufe / stage s | GPU Ø | CPU-Paket Ø | E_net gesamt | NVML-Zähler vs. Leistungsintegral |
|---|---|---|---|---|---|---|---|
{{range $id, $r := .Runs}}| ` + "`{{$id}}`" + ` | {{$r.Status}} | {{f2 $r.WallS}} | {{p $r.StageS "s"}} | {{p $r.GPUMeanW "W"}} | {{p $r.CPUPkgMeanW "W"}} | {{p $r.NetTotalJ "J"}} | {{p $r.DeviationPct "%"}} |
{{end}}
CPU-Leistungsgrenzen (RAPL) / power limits: PL1 {{p .PL1W "W"}}, PL2 {{p .PL2W "W"}}.

**Hinweis / note:** Durchschnittsleistungen beziehen sich auf die Lastphase (Stufe ` + "`work`" + ` bzw. ` + "`burn`" + `), ohne Prozessstart. Wall-Leistung (Steckdose) wurde nicht gemessen (kein Messgerät, Checkliste C11). / Mean powers refer to the load stage (` + "`work`" + ` / ` + "`burn`" + `), excluding process start-up. Wall power was not measured (no meter, checklist C11).
`))

// Markdown renders docs/CALIBRATION.md.
func (r Report) Markdown() (string, error) {
	var b bytes.Buffer
	if err := mdTmpl.Execute(&b, r); err != nil {
		return "", err
	}
	return strings.ReplaceAll(b.String(), "\n\n\n", "\n\n"), nil
}

func median(xs []float64) float64 {
	s := slices.Clone(xs)
	slices.Sort(s)
	n := len(s)
	if n%2 == 1 {
		return s[n/2]
	}
	return (s[n/2-1] + s[n/2]) / 2
}

func list(xs []float64, unit string) string {
	parts := make([]string, len(xs))
	for i, x := range xs {
		parts[i] = fmt.Sprintf("%.1f", x)
	}
	return strings.Join(parts, ", ") + " " + unit
}
