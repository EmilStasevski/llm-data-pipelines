"""Classifier, RRF and filter SQL of the Track E pipelines (no servers)."""

from __future__ import annotations

import numpy as np
import yaml

from zj_e2e import pipeline, stores


def test_softmax_separates_clusters() -> None:
    rng = np.random.default_rng(0)
    centers = rng.standard_normal((3, 16)).astype(np.float32) * 3
    y = rng.integers(0, 3, 600)
    x = (centers[y] + rng.standard_normal((600, 16))).astype(np.float32)
    w = pipeline.softmax_fit(x[:500], y[:500], 3)
    assert (pipeline.predict(x[500:], w) == y[500:]).mean() > 0.95


def test_rrf_and_where() -> None:
    assert stores.rrf([5, 3], [3, 7], k=2) == [3, 5]
    w, params = stores.where({"werk": "Essen", "datum_von": "2025-01-01", "datum_bis": "kaputt"}, "?")
    assert w == "TRUE AND werk = ? AND datum >= ?"
    assert len(params) == 2


def test_pipelines_reference_known_contestants() -> None:
    ids = {
        c["id"]
        for c in yaml.safe_load((pipeline.REPO / "config" / "contestants.yaml").read_text())["contestants"]
    }
    for p in pipeline.PIPELINES.values():
        assert p.track_a in ids
        assert p.track_b in ids


def test_services_per_pipeline() -> None:
    assert pipeline.services(pipeline.PIPELINES["e01_classic_python"]) == ["Ollama"]
    assert pipeline.services(pipeline.PIPELINES["e03_postgres_only"]) == ["PostgreSQL", "TEI", "vLLM"]
