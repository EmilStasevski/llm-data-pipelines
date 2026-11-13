// Package ingest is the River job behind POST /v1/ingest: Rust parser (a16) → parquet → TEI embeddings →
// schadensart (stored classifier, or LLM labels + a new classifier, ADR 0020) → COPY + upsert into docs.
//
// Embedding the next batch overlaps with writing the current one; upserts make retries idempotent.
package ingest

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/rand/v2"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/riverqueue/river"
	"golang.org/x/sync/errgroup"

	"zeilenjoule/services/internal/classifier"
	"zeilenjoule/services/internal/db"
	"zeilenjoule/services/internal/embed"
	"zeilenjoule/services/internal/metrics"
	"zeilenjoule/services/internal/prompts"
	"zeilenjoule/services/internal/rag"
)

// Queue is the River queue for ingest jobs (one worker: the GPU is the bottleneck).
const Queue = "ingest"

// Batch is the number of rows embedded and written per transaction.
const Batch = 2048

// Args of an ingest job.
type Args struct {
	JobID int64  `json:"job_id"`
	Path  string `json:"path"`
}

// Kind implements river.JobArgs.
func (Args) Kind() string { return "ingest" }

// InsertOpts implements river.JobArgsWithInsertOpts.
func (Args) InsertOpts() river.InsertOpts { return river.InsertOpts{Queue: Queue, MaxAttempts: 3} }

// Worker runs ingest jobs.
type Worker struct {
	river.WorkerDefaults[Args]
	Pool          *pgxpool.Pool
	Emb           rag.Embedder
	LLM           rag.LLM
	P             *prompts.Set
	ParserBin     string
	Lexicon       string
	ParserThreads int
	Log           *slog.Logger
}

// Timeout implements river.Worker: 1 M rows take ≈ 30 min of embedding (ARCHITEKTUR §2).
func (w *Worker) Timeout(*river.Job[Args]) time.Duration { return 6 * time.Hour }

// Work implements river.Worker.
func (w *Worker) Work(ctx context.Context, job *river.Job[Args]) error {
	err := w.run(ctx, job.Args)
	state, msg := "done", ""
	if err != nil {
		if job.Attempt < job.MaxAttempts && !errors.Is(err, errPermanent) {
			state = "queued"
		} else {
			state = "failed"
		}
		msg = err.Error()
	}
	if _, uerr := w.Pool.Exec(context.WithoutCancel(ctx), `UPDATE ingest_jobs SET state = $2, error = nullif($3, ''),
		finished_at = CASE WHEN $2 IN ('done', 'failed') THEN now() END WHERE id = $1`, job.Args.JobID, state, msg); uerr != nil {
		w.Log.Error("job state", "job", job.Args.JobID, "err", uerr)
	}
	if state == "done" || state == "failed" {
		_ = os.Remove(job.Args.Path)
	}
	if errors.Is(err, errPermanent) {
		return river.JobCancel(err)
	}
	return err
}

var errPermanent = errors.New("permanent")

func (w *Worker) run(ctx context.Context, a Args) error {
	if _, err := w.Pool.Exec(ctx, "UPDATE ingest_jobs SET state = 'running', rows = 0, error = NULL WHERE id = $1", a.JobID); err != nil {
		return err
	}
	t0 := time.Now()
	rows, err := w.parse(ctx, a.Path)
	if err != nil {
		return err
	}
	metrics.IngestStage.WithLabelValues("parse").Observe(time.Since(t0).Seconds())
	w.Log.Info("parsed", "job", a.JobID, "rows", len(rows), "s", time.Since(t0).Seconds())
	if len(rows) == 0 {
		return nil
	}
	lab, err := w.labeller(ctx, rows)
	if err != nil {
		return err
	}
	// First import into an empty table: load without the search indexes and build them once at the end (e03
	// procedure). The deferred rebuild also runs on failure, so the table never stays without them.
	var bulk bool
	if err := w.Pool.QueryRow(ctx, "SELECT NOT EXISTS (SELECT 1 FROM docs)").Scan(&bulk); err != nil {
		return err
	}
	if bulk {
		if err := db.DropSearchIndexes(ctx, w.Pool); err != nil {
			return err
		}
		w.Log.Info("bulk load: search indexes dropped, rebuilt after the load", "job", a.JobID)
		defer func() { // only on the error paths: the success path builds them itself and clears bulk
			if !bulk {
				return
			}
			if err := db.EnsureSearchIndexes(context.WithoutCancel(ctx), w.Pool); err != nil {
				w.Log.Error("rebuild search indexes after a failed bulk load", "job", a.JobID, "err", err)
			}
		}()
	}
	type batch struct {
		rows []Row
		vecs [][]float32
	}
	ch := make(chan batch, 1)
	g, gctx := errgroup.WithContext(ctx)
	g.Go(func() error { // embed stage
		defer close(ch)
		for s := 0; s < len(rows); s += Batch {
			b := rows[s:min(s+Batch, len(rows))]
			texts := make([]string, len(b))
			for i, r := range b {
				texts[i] = r.DocText
			}
			t := time.Now()
			v, err := w.Emb.Embed(gctx, "ingest", texts)
			if err != nil {
				return err
			}
			metrics.IngestStage.WithLabelValues("embed").Observe(time.Since(t).Seconds())
			select {
			case ch <- batch{b, v}:
			case <-gctx.Done():
				return gctx.Err()
			}
		}
		return nil
	})
	g.Go(func() error { // label + write stage
		for b := range ch {
			t := time.Now()
			sa, src := lab.label(b.rows, b.vecs)
			if err := w.WriteBatch(gctx, a.JobID, b.rows, b.vecs, sa, src); err != nil {
				return err
			}
			metrics.IngestStage.WithLabelValues("write").Observe(time.Since(t).Seconds())
		}
		return nil
	})
	if err := g.Wait(); err != nil {
		return err
	}
	if bulk { // build before ANALYZE and before the job is reported done
		t := time.Now()
		if err := db.EnsureSearchIndexes(ctx, w.Pool); err != nil {
			return err
		}
		metrics.IngestStage.WithLabelValues("index").Observe(time.Since(t).Seconds())
		w.Log.Info("search indexes built", "job", a.JobID, "s", time.Since(t).Seconds())
		bulk = false // the deferred safety net has nothing left to do
	}
	if _, err := w.Pool.Exec(ctx, "ANALYZE docs"); err != nil {
		return err
	}
	t := time.Now()
	if err := db.RefreshLexDF(ctx, w.Pool); err != nil {
		return err
	}
	metrics.IngestStage.WithLabelValues("lex_df").Observe(time.Since(t).Seconds())
	w.Log.Info("ingested", "job", a.JobID, "rows", len(rows), "s", time.Since(t0).Seconds())
	return nil
}

func (w *Worker) parse(ctx context.Context, path string) ([]Row, error) {
	if _, err := os.Stat(path); err != nil {
		return nil, fmt.Errorf("%w: upload: %w", errPermanent, err)
	}
	dir, err := os.MkdirTemp("", "zj-ingest-")
	if err != nil {
		return nil, err
	}
	defer func() { _ = os.RemoveAll(dir) }()
	out := filepath.Join(dir, "a.parquet")
	cmd := exec.CommandContext(ctx, w.ParserBin, "--input", path, "--output", out, //nolint:gosec // fixed binary from config
		"--threads", fmt.Sprint(w.ParserThreads), "--lexicon", w.Lexicon)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	cmd.Stdout = &bytes.Buffer{} // ZJRESULT line; not needed here
	if err := cmd.Run(); err != nil {
		var lines []string
		for _, l := range strings.Split(stderr.String(), "\n") {
			if l != "" && !strings.HasPrefix(l, "ZJMARK") {
				lines = append(lines, l)
			}
		}
		return nil, fmt.Errorf("%w: parser: %w: %s", errPermanent, err, strings.Join(lines[max(0, len(lines)-5):], " | "))
	}
	return ReadRows(ctx, out)
}

// labeller decides schadensart per batch.
type labeller struct {
	model   *classifier.Model
	llm     map[string]string // meldung_id → LLM label (sampled rows win, as in the benchmark)
	classes []string
}

// label returns schadensart and its source (llm | klassifikator) per row; nil when neither is available.
func (l *labeller) label(rows []Row, vecs [][]float32) (labels, sources []*string) {
	labels, sources = make([]*string, len(rows)), make([]*string, len(rows))
	var pred []int
	if l.model != nil {
		pred = l.model.Predict(vecs)
	}
	fromLLM, fromModel := "llm", "klassifikator"
	for i, r := range rows {
		if v, ok := l.llm[r.MeldungID]; ok {
			labels[i], sources[i] = &v, &fromLLM
		} else if pred != nil {
			v := l.model.Classes[pred[i]]
			labels[i], sources[i] = &v, &fromModel
		}
	}
	return labels, sources
}

// labeller loads the newest classifier; without one it LLM-labels a sample (all rows of a small upload),
// trains a classifier on the sample's vectors and stores it.
func (w *Worker) labeller(ctx context.Context, rows []Row) (*labeller, error) {
	l := &labeller{llm: map[string]string{}, classes: w.P.Classes()}
	m, err := LoadClassifier(ctx, w.Pool)
	if err != nil || m != nil {
		l.model = m
		return l, err
	}
	t0 := time.Now()
	n := min(w.P.Label.Sample, len(rows))
	rng := rand.New(rand.NewPCG(uint64(w.P.Label.Seed), 0)) //nolint:gosec // reproducible sample, not security
	idx := rng.Perm(len(rows))[:n]
	labels := make([]int, n)
	g, gctx := errgroup.WithContext(ctx)
	for j, i := range idx {
		g.Go(func() error {
			res, err := w.LLM.JSON(gctx, "label", w.P.Label.Messages(rows[i].DocText), w.P.Label.Schema, w.P.Label.MaxTokens)
			if err != nil {
				return err
			}
			var o struct {
				Schadensart string `json:"schadensart"`
			}
			labels[j] = -1
			if json.Unmarshal([]byte(res.Text), &o) == nil {
				for c, name := range l.classes {
					if name == o.Schadensart {
						labels[j] = c
					}
				}
			}
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return nil, err
	}
	var texts []string
	var y []int
	for j, i := range idx {
		if labels[j] >= 0 {
			l.llm[rows[i].MeldungID] = l.classes[labels[j]]
			texts = append(texts, rows[i].DocText)
			y = append(y, labels[j])
		}
	}
	metrics.IngestStage.WithLabelValues("label_llm").Observe(time.Since(t0).Seconds())
	if n < w.P.Label.Sample || len(y) < 2*len(l.classes) {
		return l, nil // small upload: every row has its LLM label, no classifier yet
	}
	t1 := time.Now()
	x, err := w.Emb.Embed(ctx, "label", texts)
	if err != nil {
		return nil, err
	}
	hold := len(y) / 10
	probe, err := classifier.Fit(x[hold:], y[hold:], l.classes, classifier.Defaults)
	if err != nil {
		return nil, err
	}
	acc := probe.Accuracy(x[:hold], y[:hold])
	if l.model, err = classifier.Fit(x, y, l.classes, classifier.Defaults); err != nil {
		return nil, err
	}
	metrics.IngestStage.WithLabelValues("label_fit").Observe(time.Since(t1).Seconds())
	w.Log.Info("classifier trained", "rows", len(y), "holdout_acc", acc)
	_, err = w.Pool.Exec(ctx, "INSERT INTO classifier (classes, dim, weights, train_rows, holdout_acc) VALUES ($1, $2, $3, $4, $5)",
		l.classes, l.model.Dim, l.model.W, len(y), acc)
	return l, err
}

// LoadClassifier returns the newest stored classifier or nil.
func LoadClassifier(ctx context.Context, pool *pgxpool.Pool) (*classifier.Model, error) {
	var m classifier.Model
	err := pool.QueryRow(ctx, "SELECT classes, dim, weights FROM classifier ORDER BY id DESC LIMIT 1").Scan(&m.Classes, &m.Dim, &m.W)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if len(m.W) != (m.Dim+1)*len(m.Classes) || m.Dim != embed.Dim {
		return nil, fmt.Errorf("classifier: stored shape %d×%d does not fit %d weights", m.Dim+1, len(m.Classes), len(m.W))
	}
	return &m, nil
}

var stageCols = []string{"meldung_id", "werk", "regierungsbezirk", "anlage", "equipment_nr", "datum", "prioritaet",
	"stillstand_min", "kosten_eur", "komponente", "schadensart", "quelle", "sicherheitsrelevant", "meldungstext", "doc_text",
	"emb"}

// WriteBatch COPYs rows with their vectors and schadensart (+ source) into a staging table and upserts them into docs.
func (w *Worker) WriteBatch(ctx context.Context, jobID int64, rows []Row, vecs [][]float32, sa, src []*string) error {
	return pgx.BeginFunc(ctx, w.Pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `CREATE TEMP TABLE stage (meldung_id text, werk text, regierungsbezirk text, anlage text,
			equipment_nr text, datum date, prioritaet smallint, stillstand_min integer, kosten_eur double precision,
			komponente text, schadensart text, quelle text, sicherheitsrelevant boolean, meldungstext text, doc_text text, emb real[])
			ON COMMIT DROP`); err != nil {
			return err
		}
		i := 0
		if _, err := tx.CopyFrom(ctx, pgx.Identifier{"stage"}, stageCols, pgx.CopyFromFunc(func() ([]any, error) {
			if i == len(rows) {
				return nil, nil
			}
			r := rows[i]
			v := []any{r.MeldungID, r.Werk, r.Regierungsbezirk, r.Anlage, r.EquipmentNr, r.Datum, r.Prioritaet,
				r.StillstandMin, r.KostenEUR, r.Komponente, sa[i], src[i], r.Sicherheitsrelevant, r.Meldungstext, r.DocText, vecs[i]}
			i++
			return v, nil
		})); err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `INSERT INTO docs (meldung_id, werk, regierungsbezirk, anlage, equipment_nr, datum,
			prioritaet, stillstand_min, kosten_eur, komponente, schadensart, schadensart_quelle, sicherheitsrelevant,
			meldungstext, doc_text, emb, job_id)
			SELECT DISTINCT ON (meldung_id) meldung_id, werk, regierungsbezirk, anlage, equipment_nr, datum, prioritaet,
			       stillstand_min, kosten_eur, komponente, schadensart, quelle,
			       sicherheitsrelevant, meldungstext, doc_text, emb::vector(1024)::halfvec(1024), $1
			FROM stage ORDER BY meldung_id
			ON CONFLICT (meldung_id) DO UPDATE SET werk = EXCLUDED.werk, regierungsbezirk = EXCLUDED.regierungsbezirk,
			  anlage = EXCLUDED.anlage, equipment_nr = EXCLUDED.equipment_nr, datum = EXCLUDED.datum,
			  prioritaet = EXCLUDED.prioritaet, stillstand_min = EXCLUDED.stillstand_min, kosten_eur = EXCLUDED.kosten_eur,
			  komponente = EXCLUDED.komponente,
			  -- an LLM label beats a classifier guess for the same text (ADR 0020: sampled rows keep the LLM label)
			  schadensart = CASE WHEN docs.schadensart_quelle = 'llm' AND EXCLUDED.schadensart_quelle IS DISTINCT FROM 'llm'
			      AND docs.doc_text = EXCLUDED.doc_text THEN docs.schadensart ELSE EXCLUDED.schadensart END,
			  schadensart_quelle = CASE WHEN docs.schadensart_quelle = 'llm' AND EXCLUDED.schadensart_quelle IS DISTINCT FROM 'llm'
			      AND docs.doc_text = EXCLUDED.doc_text THEN 'llm' ELSE EXCLUDED.schadensart_quelle END,
			  sicherheitsrelevant = EXCLUDED.sicherheitsrelevant,
			  meldungstext = EXCLUDED.meldungstext, doc_text = EXCLUDED.doc_text, emb = EXCLUDED.emb,
			  job_id = EXCLUDED.job_id, ingested_at = now()`, jobID)
		if err != nil {
			return err
		}
		for _, q := range src {
			if q != nil {
				metrics.IngestRows.WithLabelValues(*q).Inc()
			} else {
				metrics.IngestRows.WithLabelValues("keine").Inc()
			}
		}
		_, err = tx.Exec(ctx, "UPDATE ingest_jobs SET rows = rows + $2 WHERE id = $1", jobID, tag.RowsAffected())
		return err
	})
}
