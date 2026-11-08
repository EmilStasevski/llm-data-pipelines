"""Fetchers, fully offline: httpx.MockTransport + fake clock; tiny synthetic SQLite/zip fixtures."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from zj_datagen.fetch import ba, ted, thru
from zj_datagen.fetch.http import USER_AGENT, PoliteClient
from zj_datagen.fetch.redact import MARK, redact
from zj_datagen.writer import read_table


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def client(tmp_path: Path, handler: Any) -> tuple[PoliteClient, FakeClock]:
    clk = FakeClock()
    c = PoliteClient(
        tmp_path / "cache", transport=httpx.MockTransport(handler), clock=clk.now, sleep=clk.sleep
    )
    return c, clk


def test_rate_limit_cache_and_user_agent(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"n": len(seen)})

    c, clk = client(tmp_path, handler)
    assert c.get_json("https://x.test/a") == {"n": 1}
    assert c.get_json("https://x.test/b") == {"n": 2}
    assert c.get_json("https://x.test/a") == {"n": 1}  # cached: no third network request
    assert (c.network_requests, c.cache_hits) == (2, 1)
    assert clk.slept == [1.0]  # second request waited one full interval
    assert all(r.headers["User-Agent"] == USER_AGENT for r in seen)
    assert len(list((tmp_path / "cache").glob("*.meta.json"))) == 2


def test_retry_after_on_429(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    c, clk = client(tmp_path, handler)
    assert c.get_json("https://x.test/a") == {"ok": True}
    assert 7.0 in clk.slept


def test_gives_up_after_retries(tmp_path: Path) -> None:
    def unavailable(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    c, _ = client(tmp_path, unavailable)
    with pytest.raises(httpx.HTTPStatusError):
        c.get_json("https://x.test/a")
    assert c.network_requests == c.retries + 1


@pytest.mark.parametrize(
    ("text", "n"),
    [
        ("Kontakt: max.muster@stadt-bonn.de oder 0228/77 12 34", 2),
        ("Tel. +49 211 1234567, Fax (0211) 123 4568", 2),
        ("PLZ 32257 Bünde, CPV 45000000, ca. 2.820,00 m², 0,5 mm", 0),  # no false positives
    ],
)
def test_redact(text: str, n: int) -> None:
    out, k = redact(text)
    assert k == n
    assert (MARK in (out or "")) == (n > 0)
    assert "@" not in (out or "")


def _prtr_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE betriebe (id INT, kennnummer TEXT, jahr INT, name TEXT, strasse TEXT, ort TEXT,
            postleitzahl TEXT, bundesland TEXT, nace_code TEXT, nace_text TEXT, betriebsstunden INT);
        CREATE TABLE taetigkeiten (id INT, betriebe_id INT, haupttaetigkeit INT, prtr_schluessel TEXT,
            prtr_beschreibung TEXT, ivu_schluessel TEXT, ivu_beschreibung TEXT, branchengruppe TEXT);
        INSERT INTO betriebe VALUES
            (1, 'A', 2023, 'Hof Schulte', 'Weg 1', 'Hamm', '59063', 'nw', '01.46', 'Schweinehaltung', 8760),
            (2, 'A', 2024, 'Hof Schulte', 'Weg 1', 'Hamm', '59063', 'nw', '01.46', 'Schweinehaltung', 8760),
            (3, 'B', 2024, 'Werk X', 'Str 2', 'München', '80331', 'by', '24.10', 'Stahl', 8000);
        INSERT INTO taetigkeiten VALUES
            (1, 1, 1, '7(a)(ii)', 'alt', NULL, NULL, 'Landwirtschaft'),
            (2, 2, 1, '7(a)(ii)', 'Intensivhaltung Schweine', NULL, NULL, 'Landwirtschaft'),
            (3, 2, 0, '5(d)', 'Nebentätigkeit', NULL, NULL, 'Abfall'),
            (4, 3, 1, '2(b)', 'Stahl', NULL, NULL, 'Metall');
    """)
    con.commit()
    con.close()


def test_thru_latest_year_main_activity_no_names(tmp_path: Path) -> None:
    db = tmp_path / "prtr_2024.db"
    _prtr_db(db)
    rows = thru.nrw_facilities(db)
    assert len(rows) == 1
    (r,) = rows
    assert (r["kennnummer"], r["jahr"], r["prtr_beschreibung"]) == ("A", 2024, "Intensivhaltung Schweine")
    assert "name" not in r
    assert "strasse" not in r


def test_thru_rejects_unsafe_zip(tmp_path: Path) -> None:
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../evil.db", b"x")
    with pytest.raises(ValueError, match="unsafe"):
        thru.safe_extract(z, "../evil.db", tmp_path / "out")


def test_ted_paging_normalize_redaction(tmp_path: Path) -> None:
    notice = {
        "publication-number": "1-2026", "publication-date": "2026-05-04+02:00",
        "title-proc": {"deu": "Pumpenwartung"}, "description-proc": {"deu": "Rückfragen an a.b@stadt.de"},
        "buyer-name": {"deu": ["Stadtwerke X"]}, "buyer-city": {"mul": ["Essen"]},
        "place-of-performance": ["DEA13", "DEU", "DEA13"], "classification-cpv": ["50000000", "50000000"],
        "deadline-receipt-tender-date-lot": ["2026-06-01+02:00"],
    }  # fmt: skip
    pages: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        pages.append(body)
        return httpx.Response(
            200, json={"totalNoticeCount": 1, "notices": [notice] if body["page"] == 1 else []}
        )

    c, _ = client(tmp_path, handler)
    info = ted.fetch(tmp_path / "out", c, "20260101", "20261031", 100)
    assert info["rows"] == 1
    assert info["redactions"] == 1
    assert [p["page"] for p in pages] == [1, 2]
    assert "DEA1" in pages[0]["query"]
    (row,) = read_table(tmp_path / "out" / "ted_nrw.parquet").to_pylist()
    assert row["description"] == f"Rückfragen an {MARK}"
    assert (row["nuts"], row["cpv"], row["buyer_city"], row["deadline"]) == (
        "DEA13",
        "50000000",
        "Essen",
        "2026-06-01",
    )


def test_ba_dedupe_region_and_company_filter(tmp_path: Path) -> None:
    def job(ref: str, firma: str, region: str) -> dict[str, Any]:
        return {"referenznummer": ref, "stellenangebotsTitel": "Werkstudent Data (m/w/d)", "firma": firma,
                "stellenlokationen": [{"adresse": {"ort": "Essen", "region": region}, "breite": 51.4}],
                "arbeitgeberKundennummerHash": "secret"}  # fmt: skip

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-API-Key"] == "jobboerse-jobsuche"
        return httpx.Response(200, json={"maxErgebnisse": 3, "ergebnisliste": [
            job("1", "Muster GmbH", "NORDRHEIN_WESTFALEN"),
            job("2", "Erika Mustermann", "NORDRHEIN_WESTFALEN"),
            job("3", "Grenz AG", "NIEDERSACHSEN"),
        ]})  # fmt: skip

    c, _ = client(tmp_path, handler)
    info = ba.fetch(tmp_path / "out", c, ["Essen", "Bochum"], 25, 100)
    rows = read_table(tmp_path / "out" / "ba_werkstudent_nrw.parquet").to_pylist()
    assert [r["referenznummer"] for r in rows] == ["1", "2"]  # deduped across cities, NRW only
    assert [r["firma"] for r in rows] == ["Muster GmbH", None]  # person-like name dropped
    assert "breite" not in rows[0]
    assert "arbeitgeberKundennummerHash" not in rows[0]
    assert info["rows"] == 2
