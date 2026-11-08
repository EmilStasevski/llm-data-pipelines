"""Version fingerprint of a Python env (rule 9: versions are facts). Stdlib only."""

from __future__ import annotations

import hashlib
import platform
import sys
from collections.abc import Iterable
from importlib import metadata
from typing import TypedDict

from zjmark import canonical_json


def versions(dists: Iterable[str]) -> dict[str, str | None]:
    """Installed version per distribution name; ``None`` if not installed."""
    out: dict[str, str | None] = {}
    for d in dists:
        try:
            out[d] = metadata.version(d)
        except metadata.PackageNotFoundError:
            out[d] = None
    return out


class Fingerprint(TypedDict):
    extra: dict[str, object]
    implementation: str
    packages: dict[str, str | None]
    python: str
    sha256: str


def fingerprint(dists: Iterable[str], extra: dict[str, object] | None = None) -> Fingerprint:
    """Python + package versions (+ env-specific ``extra``) and a stable sha256 over all of it."""
    body: dict[str, object] = {
        "extra": dict(extra or {}),
        "implementation": sys.implementation.name,
        "packages": versions(sorted(set(dists))),
        "python": platform.python_version(),
    }
    digest = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return Fingerprint(
        extra=dict(extra or {}),
        implementation=sys.implementation.name,
        packages=versions(sorted(set(dists))),
        python=platform.python_version(),
        sha256=digest,
    )


def main(argv: list[str]) -> int:
    """`python -m zjmark.envinfo dist [dist …]` prints the canonical fingerprint (used as versions_cmd)."""
    print(canonical_json(fingerprint(argv or ["zjmark"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
