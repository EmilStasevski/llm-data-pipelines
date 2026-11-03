# Versions and machine

Checked on 2026-11-03. Update at the start of each milestone. Exact Python dependency pins live in `uv.lock`.

## Machine

| Item | Value |
|---|---|
| CPU | Intel Core i5-14400F |
| CPU governor | `powersave` (set explicitly before measured runs) |
| GPU | NVIDIA GeForce RTX 5060 Ti, 16311 MiB |
| GPU power limit | 180 W default and current; allowed 150 W to 216 W |
| GPU idle | about 6 W, P8, 382 MiB VRAM in use by the desktop |
| Driver / CUDA | 580.178.04 / 13.0 |
| Kernel | 6.17.0-1023-oem |
| RAPL | present, readable by root only |

## Tools

| Tool | Version | Status |
|---|---|---|
| uv | 0.12.21 | installed |
| Python | 3.13.13 | installed (`pyproject.toml` still allows >=3.11; the plan targets 3.13) |
| cargo / rustc | 1.99.1 | installed |
| docker | 29.9.2 | installed |
| oha | 1.16.0 | installed |
| ollama | 0.13.5 | installed; not a benchmark contender (plan section 10) |
| just | 1.59.0 | installed with `cargo install` |
| pre-commit | 4.7.0 | installed with `uv tool install` |
| hyperfine | | missing, needed for M4 |
| py-spy | | missing, needed for M4 |
| llama-server | | missing, needed for M3 |
| nsys | | missing, optional for M4 |

## Python packages in use (M1)

| Package | Locked | Newer on PyPI |
|---|---|---|
| polars | 2.1.0 | |
| pyarrow | 26.0.0 | 26.0.1 |
| lxml | 6.1.3 | |
| httpx | 0.28.1 | |
| pymupdf | 2.0 | 2.1 |
| typer | 0.27.3 | |
| rich | 15.0.0 | |
| huggingface-hub | 2.4.0 | |
| pytest | 9.2.0 | |
| pytest-benchmark | 5.3.0 | 5.3.1 |
| ruff | 0.17.3 | |
| mypy | see `uv.lock` | |

No upgrade was made. M1 parsing does not depend on the newer patch releases.
