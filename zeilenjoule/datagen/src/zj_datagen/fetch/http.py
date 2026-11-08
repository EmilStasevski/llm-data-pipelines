"""Polite, cached HTTP client (CLAUDE.md rule 8): ≤ 1 request/s, generic User-Agent, raw responses
cached under data/raw/<source>/cache/ so that re-runs are offline and reproducible."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "zeilenjoule-research/0.1"  # generic on purpose: no personal contact data in requests


@dataclass
class PoliteClient:
    cache_dir: Path
    min_interval_s: float = 1.0
    retries: int = 4
    timeout_s: float = 60.0
    transport: httpx.BaseTransport | None = None  # tests inject httpx.MockTransport
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    network_requests: int = 0
    cache_hits: int = 0
    _last: float = field(default=-1e18, repr=False)
    _client: httpx.Client | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": USER_AGENT},
                timeout=self.timeout_s,
                transport=self.transport,
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _key(self, method: str, url: str, params: dict[str, Any] | None, body: Any) -> str:
        raw = json.dumps([method, url, params or {}, body], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _wait_turn(self) -> None:
        delta = self.clock() - self._last
        if delta < self.min_interval_s:
            self.sleep(self.min_interval_s - delta)
        self._last = self.clock()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Response body (from cache if present). Raises httpx.HTTPStatusError after retries."""
        key = self._key(method, url, params, json_body)
        body_path, meta_path = self.cache_dir / f"{key}.bin", self.cache_dir / f"{key}.meta.json"
        if body_path.exists():
            self.cache_hits += 1
            return body_path.read_bytes()
        attempt = 0
        while True:
            self._wait_turn()
            self.network_requests += 1
            r = self._http().request(method, url, params=params, json=json_body, headers=headers)
            if r.status_code == 429 or r.status_code >= 500:
                attempt += 1
                if attempt > self.retries:
                    r.raise_for_status()
                retry_after = r.headers.get("Retry-After", "")
                self.sleep(float(retry_after) if retry_after.isdigit() else 2.0**attempt)
                continue
            r.raise_for_status()
            break
        body_path.write_bytes(r.content)
        meta = {"method": method, "url": url, "params": params, "json": json_body, "status": r.status_code,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}  # fmt: skip
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return r.content

    def get_json(self, url: str, **kw: Any) -> Any:
        return json.loads(self.request("GET", url, **kw))

    def post_json(self, url: str, body: Any, **kw: Any) -> Any:
        return json.loads(self.request("POST", url, json_body=body, **kw))
