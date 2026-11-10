"""Router SQL safety: only one SELECT on the view, transpiled per store dialect."""

from __future__ import annotations

import pytest

from zj_e2e import sqlsafe

OK = "SELECT werk, count(*) AS n FROM meldungen WHERE sicherheitsrelevant GROUP BY werk ORDER BY werk"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM meldungen; DROP TABLE meldungen",
        "DELETE FROM meldungen",
        "UPDATE meldungen SET werk = 'x'",
        "COPY meldungen TO '/tmp/x'",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM docs",
        "SELECT pg_sleep(100) FROM meldungen",
        "SELECT * INTO t FROM meldungen",
        "ATTACH 'x.db'",
        "SELEC nonsense",
    ],
)
def test_rejected(sql: str) -> None:
    assert not sqlsafe.check(sql).ok


def test_accepted_and_transpiled() -> None:
    assert sqlsafe.check(OK).ok
    assert sqlsafe.check("WITH t AS (SELECT werk FROM meldungen) SELECT * FROM t").ok
    pg = sqlsafe.check("SELECT count(*) FROM meldungen WHERE year(datum) = 2025", "postgres")
    assert pg.ok
    assert "EXTRACT(YEAR FROM datum)" in pg.sql
