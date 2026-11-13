package sqlguard

import "testing"

func TestAccepted(t *testing.T) {
	for _, sql := range []string{
		"SELECT werk, count(*) AS n FROM meldungen WHERE sicherheitsrelevant GROUP BY werk ORDER BY werk",
		"SELECT round(coalesce(sum(kosten_eur), 0), 2) FROM meldungen WHERE datum BETWEEN '2025-01-01' AND '2025-12-31'",
		"WITH t AS (SELECT werk FROM meldungen) SELECT * FROM t",
		"SELECT meldung_id FROM meldungen UNION SELECT meldung_id FROM meldungen",
		"SELECT EXTRACT(YEAR FROM datum) AS y, count(*) FROM meldungen GROUP BY 1",
	} {
		if err := Check(sql); err != nil {
			t.Errorf("%q rejected: %v", sql, err)
		}
	}
}

func TestRejected(t *testing.T) {
	for _, sql := range []string{
		"SELECT * FROM meldungen; DROP TABLE docs",
		"DELETE FROM meldungen",
		"UPDATE meldungen SET werk = 'x'",
		"INSERT INTO meldungen VALUES (1)",
		"COPY meldungen TO '/tmp/x'",
		"SELECT * FROM docs",
		"SELECT * FROM pg_catalog.pg_tables",
		"SELECT * FROM public.meldungen",
		"SELECT pg_sleep(100)",
		"SELECT pg_read_file('/etc/passwd')",
		"SELECT * FROM generate_series(1, 10)",
		"SELECT * INTO t FROM meldungen",
		"SELECT * FROM meldungen FOR UPDATE",
		"SELECT current_setting('is_superuser')",
		"SET statement_timeout = 0",
		"SELECT * FROM meldungen WHERE meldung_id IN (SELECT usename FROM pg_user)",
		"SELEC nonsense",
		"",
	} {
		if err := Check(sql); err == nil {
			t.Errorf("%q accepted", sql)
		}
	}
}

func FuzzCheckNeverPanics(f *testing.F) {
	f.Add("SELECT werk FROM meldungen")
	f.Add("DROP TABLE x")
	f.Fuzz(func(_ *testing.T, sql string) { _ = Check(sql) })
}
