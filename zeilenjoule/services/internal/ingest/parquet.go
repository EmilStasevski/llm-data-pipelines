package ingest

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/apache/arrow-go/v18/arrow"
	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/arrow-go/v18/arrow/memory"
	"github.com/apache/arrow-go/v18/parquet/pqarrow"
)

// Row is one parsed fault notification (Track A output, spec/track_a.md, renamed as in zj_e2e.pipeline).
type Row struct {
	MeldungID, Werk, Anlage, Meldungstext, DocText string
	Regierungsbezirk, EquipmentNr, Komponente      *string
	Datum                                          *time.Time
	Prioritaet                                     *int16
	StillstandMin                                  *int32
	KostenEUR                                      *float64
	Sicherheitsrelevant                            bool
}

type stringArr interface {
	IsNull(i int) bool
	Value(i int) string
}

// ReadRows loads the Track A parquet file written by the Rust parser.
func ReadRows(ctx context.Context, path string) ([]Row, error) {
	f, err := os.Open(path) //nolint:gosec // path is the worker's own temp file
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()
	tbl, err := pqarrow.ReadTable(ctx, f, nil, pqarrow.ArrowReadProperties{}, memory.DefaultAllocator)
	if err != nil {
		return nil, fmt.Errorf("parquet: %w", err)
	}
	defer tbl.Release()
	tr := array.NewTableReader(tbl, 65536)
	defer tr.Release()
	rows := make([]Row, 0, tbl.NumRows())
	for tr.Next() {
		rec := tr.RecordBatch()
		col := func(name string) (arrow.Array, error) {
			idx := rec.Schema().FieldIndices(name)
			if len(idx) != 1 {
				return nil, fmt.Errorf("parquet: column %q missing", name)
			}
			return rec.Column(idx[0]), nil
		}
		get := map[string]arrow.Array{}
		for _, n := range []string{"meldung_id", "werk", "anlage", "text_norm", "doc_text", "regierungsbezirk",
			"equipment_nr", "komponente_rule", "datum", "prioritaet", "stillstand_min", "kosten_eur",
			"sicherheitsrelevant_rule"} {
			a, err := col(n)
			if err != nil {
				return nil, err
			}
			get[n] = a
		}
		strs := map[string]stringArr{}
		for _, n := range []string{"meldung_id", "werk", "anlage", "text_norm", "doc_text", "regierungsbezirk",
			"equipment_nr", "komponente_rule"} {
			s, ok := get[n].(stringArr)
			if !ok {
				return nil, fmt.Errorf("parquet: column %q is %s, want string", n, get[n].DataType())
			}
			strs[n] = s
		}
		datum, ok1 := get["datum"].(*array.Date32)
		prio, ok2 := get["prioritaet"].(*array.Int8)
		still, ok3 := get["stillstand_min"].(*array.Int32)
		kosten, ok4 := get["kosten_eur"].(*array.Float64)
		safe, ok5 := get["sicherheitsrelevant_rule"].(*array.Boolean)
		if !ok1 || !ok2 || !ok3 || !ok4 || !ok5 {
			return nil, fmt.Errorf("parquet: unexpected column types (spec/track_a.md)")
		}
		opt := func(n string, i int) *string {
			if strs[n].IsNull(i) {
				return nil
			}
			v := strs[n].Value(i)
			return &v
		}
		for i := range int(rec.NumRows()) {
			r := Row{MeldungID: strs["meldung_id"].Value(i), Werk: strs["werk"].Value(i),
				Anlage: strs["anlage"].Value(i), Meldungstext: strs["text_norm"].Value(i),
				DocText: strs["doc_text"].Value(i), Regierungsbezirk: opt("regierungsbezirk", i),
				EquipmentNr: opt("equipment_nr", i), Komponente: opt("komponente_rule", i),
				Sicherheitsrelevant: safe.Value(i)}
			if datum.IsValid(i) {
				t := datum.Value(i).ToTime()
				r.Datum = &t
			}
			if prio.IsValid(i) {
				v := int16(prio.Value(i))
				r.Prioritaet = &v
			}
			if still.IsValid(i) {
				v := still.Value(i)
				r.StillstandMin = &v
			}
			if kosten.IsValid(i) {
				v := kosten.Value(i)
				r.KostenEUR = &v
			}
			rows = append(rows, r)
		}
	}
	return rows, tr.Err()
}
