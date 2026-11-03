"""esef.py against the real filings.xbrl.org response shape (copied from a live reply)."""

import httpx

from ruhrbench.ingest import esef

PAGE1 = {
    "data": [
        {
            "type": "filing",
            "id": "1",
            "attributes": {
                "period_end": "2021-12-31",
                "country": "SE",
                "fxo_id": "549300CSLHPO6Y1AZN37-2021-12-31-ESEF-SE-0",
                "package_url": "/549300CSLHPO6Y1AZN37/2021-12-31/ESEF/SE/0/Cloetta-2021-12-31-en.zip",
                "report_url": "/549300CSLHPO6Y1AZN37/2021-12-31/ESEF/SE/0/Cloetta-2021-12-31-en/reports/Cloetta-2021-12-31-en.xhtml",
                "json_url": "/549300CSLHPO6Y1AZN37/2021-12-31/ESEF/SE/0/Cloetta-2021-12-31-en.json",
                "error_count": 0,
            },
        },
        {
            "type": "filing",
            "id": "2",
            "attributes": {
                "period_end": "2021-12-31",
                "country": "SE",
                "fxo_id": "549300CSLHPO6Y1AZN37-2021-12-31-ESEF-SE-1",
                "package_url": "/549300CSLHPO6Y1AZN37/2021-12-31/ESEF/SE/1/x.zip",
                "report_url": None,
                "json_url": None,
                "error_count": 1,
            },
        },
    ]
}


def _client(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/filings":
            seen.append(dict(request.url.params))
            page = int(request.url.params["page[number]"])
            return httpx.Response(200, json=PAGE1 if page == 1 else {"data": []})
        return httpx.Response(200, content=b"<html/>")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_filings_skips_missing_report_and_builds_absolute_urls():
    seen: list = []
    rows = esef.list_filings("SE", limit=10, client=_client(seen))
    assert seen[0]["filter[country]"] == "SE"
    assert [r["id"] for r in rows] == ["1"]  # filing 2 has report_url = null
    assert rows[0]["report_url"].startswith("https://filings.xbrl.org/549300CSLHPO6Y1AZN37/")
    assert rows[0]["report_url"].endswith(".xhtml")


def test_download_report_writes_xhtml(tmp_path):
    row = esef.list_filings("SE", limit=1, client=_client([]))[0]
    out = esef.download_report(row, tmp_path, _client([]))
    assert out[0].name == "549300CSLHPO6Y1AZN37-2021-12-31-ESEF-SE-0.xhtml"
    assert out[0].read_bytes() == b"<html/>"
