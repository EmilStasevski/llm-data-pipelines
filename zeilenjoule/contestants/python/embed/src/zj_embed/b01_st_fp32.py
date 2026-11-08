"""Track B contestant b01_st_fp32: sentence-transformers fp32, library defaults (batch 32), the reference."""

from __future__ import annotations

import sys

from zj_embed.track_b import st


def main(argv: list[str] | None = None) -> int:
    return st.main(
        argv, "b01_st_fp32", half=False, default_batch=32, notes="sentence-transformers fp32, batch 32"
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
