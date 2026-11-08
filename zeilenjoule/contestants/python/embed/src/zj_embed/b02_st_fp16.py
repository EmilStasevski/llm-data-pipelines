"""Track B contestant b02_st_fp16: sentence-transformers with fp16 weights, otherwise library defaults."""

from __future__ import annotations

import sys

from zj_embed.track_b import st


def main(argv: list[str] | None = None) -> int:
    return st.main(
        argv, "b02_st_fp16", half=True, default_batch=32, notes="sentence-transformers fp16, batch 32"
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
