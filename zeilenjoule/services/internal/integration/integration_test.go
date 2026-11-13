//go:build integration

// Package integration tests the services against a real PostgreSQL + pgvector (`task test:services:int` starts a
// throw-away container and sets ZJ_TEST_DATABASE_URL to its superuser). TEI and vLLM are replaced by fakes:
// deterministic vectors and canned router answers, so the test checks our SQL, roles and fusion, not the models.
package integration

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"log/slog"
	"math"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"zeilenjoule/services/internal/db"
	"zeilenjoule/services/internal/embed"
	"zeilenjoule/services/internal/ingest"
	"zeilenjoule/services/internal/llm"
	"zeilenjoule/services/internal/prompts"
	"zeilenjoule/services/internal/rag"
)

// fakeEmb maps a text to a unit vector from the hashes of its words: shared words → high inner product.
type fakeEmb struct{}

func (fakeEmb) Embed(_ context.Context, _ string, texts []string) ([][]float32, error) {
	out := make([][]float32, len(texts))
	for i, t := range texts {
		v := make([]float32, embed.Dim)
		for _, w := range strings.Fields(strings.ToLower(t)) {
			h := fnv.New32a()
			_, _ = h.Write([]byte(w))
			v[h.Sum32()%embed.Dim]++
		}
		var n float64
		for _, x := range v {
			n += float64(x * x)
		}
		for j := range v {
			v[j] /= float32(math.Sqrt(n) + 1e-9)
		}
		out[i] = v
	}
	return out, nil
}

// fakeLLM answers the router with the JSON stored for the question, the answer call with the first cited id.
type fakeLLM struct{ routes map[string]string }

func (f fakeLLM) JSON(_ context.Context, op string, msgs []prompts.Message, _ json.RawMessage, _ int) (llm.Result, error) {
	q := msgs[len(msgs)-1].Content
	switch op {
	case "router":
		if r, ok := f.routes[q]; ok {
			return llm.Result{Text: r}, nil
		}
		return llm.Result{Text: "not json"}, nil
	case "answer":
		id := q[strings.Index(q, "[")+1 : strings.Index(q, "]")]
		return llm.Result{Text: fmt.Sprintf(`{"antwort": "Lager ersetzt", "zitiert": [%q]}`, id)}, nil
	}
	return llm.Result{}, errors.New("unexpected op " + op)
}

func ptr[T any](v T) *T { return &v }

func setup(t *testing.T) (*pgxpool.Pool, *pgxpool.Pool, *prompts.Set) {
	t.Helper()
	url := os.Getenv("ZJ_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("ZJ_TEST_DATABASE_URL not set (task test:services:int)")
	}
	ctx := context.Background()
	owner, err := db.Open(ctx, url, 4)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(owner.Close)
	for _, stmt := range []string{"DROP SCHEMA public CASCADE", "CREATE SCHEMA public", "DROP ROLE IF EXISTS zj_test_ask"} {
		if _, err := owner.Exec(ctx, stmt); err != nil {
			t.Fatal(stmt, err)
		}
	}
	if _, err := db.Migrate(ctx, owner); err != nil {
		t.Fatal(err)
	}
	again, err := db.Migrate(ctx, owner)
	if err != nil || len(again) != 0 {
		t.Fatalf("second migrate not a no-op: %v %v", again, err)
	}
	if _, err := owner.Exec(ctx, "CREATE ROLE zj_test_ask LOGIN PASSWORD 'test' IN ROLE zj_reader"); err != nil {
		t.Fatal(err)
	}
	cfg := owner.Config().ConnConfig
	reader, err := db.Open(ctx, fmt.Sprintf("postgres://zj_test_ask:test@%s:%d/%s", cfg.Host, cfg.Port, cfg.Database), 2)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(reader.Close)
	p, err := prompts.Load()
	if err != nil {
		t.Fatal(err)
	}
	return owner, reader, p
}

func rows() []ingest.Row {
	d := func(s string) *time.Time { t, _ := time.Parse(time.DateOnly, s); return &t }
	return []ingest.Row{
		{MeldungID: "2000000001", Werk: "Hamm", Anlage: "Walzstraße 1", EquipmentNr: ptr("P-1234-B"), Datum: d("2025-02-03"),
			Komponente: ptr("Wälzlager"), Meldungstext: "Lager an Pumpe läuft heiß, Lager ersetzt", KostenEUR: ptr(1200.5),
			DocText: "Lager an Pumpe läuft heiß, Lager ersetzt", Regierungsbezirk: ptr("Arnsberg")},
		{MeldungID: "2000000002", Werk: "Dortmund", Anlage: "Getriebe Linie 3", EquipmentNr: ptr("M-0815-A"), Datum: d("2025-08-10"),
			Komponente: ptr("Getriebe"), Meldungstext: "Getriebe läuft heiß, Öl gewechselt", KostenEUR: ptr(300.0),
			DocText: "Getriebe läuft heiß, Öl gewechselt", Regierungsbezirk: ptr("Arnsberg")},
		{MeldungID: "2000000003", Werk: "Köln", Anlage: "Kühlung", Datum: d("2025-09-01"), Komponente: ptr("Kühlwasserpumpe"),
			Meldungstext: "Leckage an Kühlwasserpumpe, Dichtung getauscht", DocText: "Leckage an Kühlwasserpumpe, Dichtung getauscht",
			Regierungsbezirk: ptr("Köln"), Sicherheitsrelevant: true},
	}
}

func TestStack(t *testing.T) {
	owner, reader, p := setup(t)
	ctx := context.Background()
	rs := rows()
	vecs, _ := fakeEmb{}.Embed(ctx, "", []string{rs[0].DocText, rs[1].DocText, rs[2].DocText})
	sa := []*string{ptr("Verschleiß"), ptr("Überhitzung"), ptr("Leckage")}
	src := []*string{ptr("llm"), ptr("klassifikator"), ptr("klassifikator")}
	w := &ingest.Worker{Pool: owner, Log: slog.New(slog.DiscardHandler)}
	for range 2 { // upsert: a retried batch must not duplicate rows
		if err := w.WriteBatch(ctx, 0, rs, vecs, sa, src); err != nil {
			t.Fatal(err)
		}
	}
	var n int
	if err := owner.QueryRow(ctx, "SELECT count(*) FROM docs").Scan(&n); err != nil || n != 3 {
		t.Fatalf("docs: %d rows (%v), want 3", n, err)
	}
	// re-ingest with a classifier guess: the LLM label of the unchanged row 1 survives, row 2 takes the new guess
	if err := w.WriteBatch(ctx, 0, rs[:2], vecs[:2], []*string{ptr("Bruch"), ptr("Bruch")},
		[]*string{ptr("klassifikator"), ptr("klassifikator")}); err != nil {
		t.Fatal(err)
	}
	var sa1, q1, sa2 string
	if err := owner.QueryRow(ctx, `SELECT a.schadensart, a.schadensart_quelle, b.schadensart FROM docs a, docs b
		WHERE a.meldung_id = '2000000001' AND b.meldung_id = '2000000002'`).Scan(&sa1, &q1, &sa2); err != nil ||
		sa1 != "Verschleiß" || q1 != "llm" || sa2 != "Bruch" {
		t.Fatalf("label precedence: %s/%s, %s (%v)", sa1, q1, sa2, err)
	}

	if err := db.RefreshLexDF(ctx, owner); err != nil {
		t.Fatal(err)
	}
	t.Run("common-terms match query", func(t *testing.T) {
		// 3 rows: "lag" (Lager) and "leckag" in one row each (df 1/3), "lauft" in two (2/3), "xyzzy" in none
		for _, c := range []struct {
			cutoff float64
			text   string
			want   string
		}{
			{0.5, "Lager läuft, Leckage xyzzy", "'lag' | 'leckag'"}, // rare terms that occur, OR
			{0.01, "Lager läuft, Leckage xyzzy", ""},                // nothing rare: no lexical candidates
		} {
			var got string
			if err := owner.QueryRow(ctx, rag.MatchQuery, c.text, c.cutoff).Scan(&got); err != nil || got != c.want {
				t.Errorf("%q cutoff %v: %q (%v), want %q", c.text, c.cutoff, got, err, c.want)
			}
		}
	})

	e := &rag.Engine{Owner: owner, Reader: reader, Emb: fakeEmb{}, P: p, SQLTimeout: 300 * time.Millisecond,
		LLM: fakeLLM{routes: map[string]string{
			"count": `{"route": "sql", "antwort_aus": "tabelle", "sql": "SELECT werk, count(*) AS n, round(coalesce(sum(kosten_eur), 0), 2) AS k FROM meldungen WHERE datum BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY werk ORDER BY werk"}`,
			"text":  `{"route": "sql", "antwort_aus": "text", "sql": "SELECT meldung_id, meldungstext FROM meldungen WHERE equipment_nr = 'P-1234-B' AND datum = '2025-02-03'"}`,
			"evil":  `{"route": "sql", "antwort_aus": "tabelle", "sql": "SELECT * FROM docs; DROP TABLE docs"}`,
			"est":   `{"route": "sql", "antwort_aus": "tabelle", "sql": "SELECT schadensart, count(*) FROM meldungen GROUP BY 1"}`,
			"search": `{"route": "search", "suchtext": "Getriebe läuft heiß", "werk": "Dortmund", "komponente": "Getriebe",
				"schadensart": "Unbekannt", "datum_von": "2025-07-01", "datum_bis": "2025-12-31"}`,
		}}}

	t.Run("sql table", func(t *testing.T) {
		a, err := e.Ask(ctx, "count", nil)
		if err != nil || a.Rejected != "" {
			t.Fatal(err, a.Rejected)
		}
		tbl := a.Value.([][]any)
		if fmt.Sprint(a.Columns) != "[werk n k]" || len(tbl) != 3 || tbl[0][0] != "Dortmund" || tbl[1][2] != 1200.5 || a.Estimated {
			t.Fatalf("table %v %v estimated %v", a.Columns, tbl, a.Estimated)
		}
	})
	t.Run("sql text → answer", func(t *testing.T) {
		a, err := e.Ask(ctx, "text", nil)
		if err != nil || a.Value != "Lager ersetzt" || len(a.CitedIDs) != 1 || a.CitedIDs[0] != "2000000001" {
			t.Fatalf("%+v %v", a, err)
		}
	})
	t.Run("guard rejects", func(t *testing.T) {
		a, err := e.Ask(ctx, "evil", nil)
		if err != nil || a.Rejected == "" || a.SQL != "" {
			t.Fatalf("%+v %v", a, err)
		}
	})
	t.Run("estimated column flagged", func(t *testing.T) {
		a, err := e.Ask(ctx, "est", nil)
		if err != nil || !a.Estimated {
			t.Fatalf("%+v %v", a, err)
		}
	})
	t.Run("unparsable route", func(t *testing.T) {
		a, err := e.Ask(ctx, "???", nil)
		if err != nil || a.Route != "invalid" {
			t.Fatalf("%+v %v", a, err)
		}
	})
	t.Run("search with self-query filters", func(t *testing.T) {
		a, err := e.Ask(ctx, "search", nil)
		// schadensart "Unbekannt" is not a catalog value → dropped; werk/komponente/dates kept → only row 2
		if err != nil || a.Route != "search" || len(a.CitedIDs) != 1 || a.CitedIDs[0] != "2000000002" || a.Estimated {
			t.Fatalf("%+v %v", a, err)
		}
	})
	t.Run("search fuses dense and full text", func(t *testing.T) {
		hits, err := e.Search(ctx, "Leckage Dichtung", rag.Filters{}, 10)
		if err != nil || len(hits) == 0 || hits[0].MeldungID != "2000000003" || hits[0].Werk != "Köln" || hits[0].Datum == nil {
			t.Fatalf("%+v %v", hits, err)
		}
	})
	// defense in depth below the guard: the reader login itself cannot touch anything but the view
	t.Run("reader cannot read docs", func(t *testing.T) {
		_, _, err := e.RunSQL(ctx, "SELECT meldung_id FROM docs")
		var pe *pgconn.PgError
		if !errors.As(err, &pe) || pe.Code != "42501" {
			t.Fatalf("want insufficient_privilege, got %v", err)
		}
	})
	t.Run("reader cannot write", func(t *testing.T) {
		_, _, err := e.RunSQL(ctx, "DELETE FROM meldungen")
		var pe *pgconn.PgError
		if !errors.As(err, &pe) || (pe.Code != "25006" && pe.Code != "42501") {
			t.Fatalf("want read-only/privilege error, got %v", err)
		}
	})
	t.Run("statement timeout", func(t *testing.T) {
		_, _, err := e.RunSQL(ctx, "SELECT pg_sleep(2)")
		var pe *pgconn.PgError
		if !errors.As(err, &pe) || pe.Code != "57014" {
			t.Fatalf("want query_canceled, got %v", err)
		}
	})
	t.Run("classifier round trip", func(t *testing.T) {
		m, err := ingest.LoadClassifier(ctx, owner)
		if err != nil || m != nil {
			t.Fatalf("empty table: %v %v", m, err)
		}
		w := make([]float32, (embed.Dim+1)*len(p.Classes()))
		if _, err := owner.Exec(ctx, "INSERT INTO classifier (classes, dim, weights, train_rows) VALUES ($1, $2, $3, 0)",
			p.Classes(), embed.Dim, w); err != nil {
			t.Fatal(err)
		}
		if m, err = ingest.LoadClassifier(ctx, owner); err != nil || m == nil || len(m.Classes) != len(p.Classes()) {
			t.Fatalf("%v %v", m, err)
		}
	})
}

// The bulk-load path (drop, load, rebuild) must produce the same indexes as migration 0001.
func TestSearchIndexesRoundTrip(t *testing.T) {
	owner, _, _ := setup(t)
	ctx := context.Background()
	defs := func() map[string]string {
		m := map[string]string{}
		r, err := owner.Query(ctx, "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'docs' AND indexname IN ('docs_hnsw', 'docs_tsv')")
		if err != nil {
			t.Fatal(err)
		}
		for r.Next() {
			var n, d string
			if err := r.Scan(&n, &d); err != nil {
				t.Fatal(err)
			}
			m[n] = d
		}
		return m
	}
	want := defs()
	if len(want) != len(db.SearchIndexes) {
		t.Fatalf("migration created %v", want)
	}
	if err := db.DropSearchIndexes(ctx, owner); err != nil {
		t.Fatal(err)
	}
	if got := defs(); len(got) != 0 {
		t.Fatalf("still there after drop: %v", got)
	}
	for range 2 { // second call is a no-op
		if err := db.EnsureSearchIndexes(ctx, owner); err != nil {
			t.Fatal(err)
		}
	}
	if got := defs(); fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("rebuilt indexes differ:\n got %v\nwant %v", got, want)
	}
}
