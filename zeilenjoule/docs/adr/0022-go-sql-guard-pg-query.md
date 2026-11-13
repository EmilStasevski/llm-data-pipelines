# ADR 0022: SQL-Prüfung im Go-Gateway mit dem PostgreSQL-Parser (pg_query_go) statt sqlglot

**Status:** angenommen (2026-11-11)

## Kontext
Im Benchmark prüft `zj_e2e.sqlsafe` (Python, sqlglot) die Router-SQL. Das Gateway ist in Go (ADR 0021); PLAN §11
nennt „sqlglot oder das Gegenstück in Go“. Die Produktions-Datenbank ist PostgreSQL.

## Entscheidung
- `services/internal/sqlguard` parst mit **pg_query_go v6.2.5** (libpg_query = der Parser von PostgreSQL selbst):
  genau eine SELECT-Anweisung, gelesene Relationen nur die Sicht `meldungen` oder CTEs derselben Anweisung, kein
  Schema-Präfix, keine Tabellenfunktionen, kein `INTO`, kein `FOR UPDATE`, keine Funktionen aus einer Sperrliste
  (Dateien, Schlafen, Einstellungen, dblink, Sequenzen).
- Der Baum wird generisch (JSON-Form) durchlaufen, damit neue Knotentypen nicht an einem Besucher vorbeirutschen.
- Zweite Verteidigungslinie bleibt die Datenbank: Rolle mit nur `SELECT` auf die Sicht, `statement_timeout`,
  `default_transaction_read_only`.

## Konsequenzen
- cgo-Abhängigkeit (libpg_query wird mitgebaut, erster Build ≈ 1 min); Container-Image braucht libc (distroless/cc).
- Der Router schreibt weiter DuckDB-nahes SQL (Benchmark-Prompts); im Gateway wird PostgreSQL-SQL verlangt und
  der Prompt entsprechend angepasst (Datumsfunktionen über `EXTRACT`).
- Tests: 5 erlaubte, 18 abgelehnte Fälle, Fuzz-Test ohne Panik.
