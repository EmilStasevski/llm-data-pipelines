"""CLI: `uv run python -m zj_datagen gen --scale S [--table all]` (see `--help`)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from zj_datagen import cards, gen, lexicon, questions, real_corpus
from zj_datagen.fetch import ba, ted, thru
from zj_datagen.fetch.http import PoliteClient


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_datagen")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="generate synthetic tables")
    g.add_argument("--scale", choices=list(gen.SCALES), required=True)
    g.add_argument("--table", choices=[*gen.TABLES, "all"], default="all")
    g.add_argument("--repo", type=Path, default=Path.cwd())
    g.add_argument("--out", type=Path, default=None, help="default: <repo>/data/synth")
    g.add_argument("--workers", type=int, default=None)
    d = sub.add_parser("derived", help="D2k sample + Track C queries + Track E questions from generated data")
    d.add_argument("--scale", choices=list(gen.SCALES), required=True)
    d.add_argument("--repo", type=Path, default=Path.cwd())
    f = sub.add_parser("fetch", help="real open data: thru.de PRTR, TED tenders, BA Werkstudent ads (cached)")
    f.add_argument("--source", choices=["thru", "ted", "ba", "all"], default="all")
    f.add_argument("--repo", type=Path, default=Path.cwd())
    r = sub.add_parser("real-corpus", help="Track C/E corpus + known-item queries from the fetched real data")
    r.add_argument("--repo", type=Path, default=Path.cwd())
    c = sub.add_parser("cards", help="regenerate dataset cards data/cards/*.md")
    c.add_argument("--repo", type=Path, default=Path.cwd())
    a = p.parse_args(argv)
    if a.cmd == "cards":
        for path in cards.write_all(a.repo):
            print(path.relative_to(a.repo))
        return 0
    if a.cmd == "real-corpus":
        info = real_corpus.build(a.repo, gen.load_config(a.repo)["seed"])
        print(json.dumps(info, ensure_ascii=False))
        return 0
    if a.cmd == "fetch":
        return _fetch(a.repo, a.source)
    if a.cmd == "derived":
        return _derived(a.repo, a.scale)
    out = a.out or a.repo / "data" / "synth"
    for table in gen.TABLES if a.table == "all" else [a.table]:
        t0 = time.perf_counter()
        m = gen.generate(a.repo, a.scale, out, a.workers, table)
        secs = time.perf_counter() - t0
        print(f"{table} {a.scale}: {m['rows']} rows in {secs:.1f} s → {out / table / a.scale}")
    return 0


def _fetch(repo: Path, source: str) -> int:
    cfg = gen.load_config(repo)["fetch"]
    lex = lexicon.load(repo / "spec" / "lexicon")
    for src in ("thru", "ted", "ba") if source == "all" else (source,):
        raw, out = repo / "data" / "raw" / src, repo / "data" / "real" / src
        client = PoliteClient(cache_dir=raw / "cache")
        try:
            if src == "thru":
                info = thru.fetch(raw, out, client)
            elif src == "ted":
                tc = cfg["ted"]
                info = ted.fetch(out, client, tc["date_from"], tc["date_to"], tc["max_notices"])
            else:
                cities = sorted({w.stadt for w in lex.werke})
                info = ba.fetch(out, client, cities, cfg["ba"]["umkreis_km"], cfg["ba"]["max_ads"])
        finally:
            client.close()
        info |= {"network_requests": client.network_requests, "cache_hits": client.cache_hits}
        (out / "manifest.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        stats = f"{client.network_requests} requests, {client.cache_hits} cached"
        print(f"{src}: {info['rows']} rows ({stats}) → {out}")
    return 0


def _derived(repo: Path, scale: str) -> int:
    cfg = gen.load_config(repo)
    lex = lexicon.load(repo / "spec" / "lexicon")
    synth = repo / "data" / "synth"
    scale_dir = synth / "stoermeldungen" / scale
    if not (scale_dir / "manifest.json").exists():
        print(f"run `gen --scale {scale}` first", file=sys.stderr)
        return 2
    if scale == cfg["d2k"]["from_scale"]:
        info = questions.d2k(
            scale_dir, synth / "d2k", cfg["seed"], cfg["d2k"]["rows"], cfg["d2k"]["dev_rows"]
        )
        print(f"d2k: {info['rows']} + {info['dev_rows']} dev rows from {scale} → {synth / 'd2k'}")
    qc = questions.queries_c(scale_dir, lex, cfg["seed"], scale)
    questions.write_jsonl(repo / "spec" / f"qrels_c.{scale}.jsonl", qc)
    qe = questions.questions_e(scale_dir, lex, cfg["seed"], scale)
    questions.write_jsonl(repo / "spec" / f"questions_e.{scale}.jsonl", qe)
    print(
        f"spec/qrels_c.{scale}.jsonl: {len(qc)} queries; spec/questions_e.{scale}.jsonl: {len(qe)} questions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
