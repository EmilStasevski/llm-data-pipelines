-- Document frequency of every full-text lexeme (ADR 0027). The lexical search leg matches only on lexemes rarer
-- than a cutoff and ranks the matches by the whole query (common-terms query); without it, a question with one
-- everyday word ranks ~40 % of the table. Refreshed (CONCURRENTLY, needs the unique index) after every ingest job.
CREATE MATERIALIZED VIEW lex_df AS
-- schema-qualified: since PostgreSQL 17 REFRESH runs with search_path = pg_catalog, pg_temp
SELECT word, ndoc, (SELECT count(*) FROM public.docs) AS nrows FROM ts_stat('SELECT tsv FROM public.docs');
CREATE UNIQUE INDEX lex_df_word ON lex_df (word);
