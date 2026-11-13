# Lokale CI/CD (ZeilenJoule)

Kein GitHub (CLAUDE.md Regel 1): `task` ist der einzige Einstiegspunkt, alle Scanner laufen als digest-gepinnte
Container (Versionen im Kopf von `Taskfile.yml`, Begründung ADR 0024). Caches liegen in `.cache/` (gitignored),
SBOMs in `dist/sbom/`.

## Stufen
| Befehl | Inhalt | braucht |
|---|---|---|
| `task ci` | Lint Python/Rust/Go, hadolint, helm lint + kubeconform, Tests (pytest, cargo test, `go test -race`, Postgres-Integration), gitleaks, trivy config, trivy fs | Docker, Netz für trivy-DB/Schemas beim ersten Lauf |
| `task ci:release` | `task ci` + Images bauen (`VERSION=…`), trivy image, syft SBOM | Docker |
| `task k3s:up` | Images in die lokale Registry, Digests → Helm `upgrade --install --wait` | k3s, einmalig `task k3s:cluster-setup` |
| `task k3s:test` / `task k3s:smoke` | `helm test` bzw. kompletter Smoke-Test über Port-Forward | laufendes Release |

**Nie während einer gemessenen Benchmark-Session** (Regel 7): CI baut und testet, der Stack belegt die GPU.

## Prüfungen im Einzelnen
| Task | Werkzeug | schlägt fehl bei |
|---|---|---|
| `lint:go` | gofmt, go vet, golangci-lint, govulncheck | Befund, erreichbarer CVE |
| `lint:rs` / `lint:py` | rustfmt, clippy `-D warnings` / ruff, pyright | Befund |
| `lint:docker` | hadolint 2.15.1 (`.hadolint.yaml`, Schwelle `info`) | jedem Befund; Ausnahmen inline mit Grund |
| `lint:helm` | helm 4.3.1 lint `--strict` (dev + prod values), kubeconform 0.8.0 `-strict` gegen 1.36.5 | Schema-Fehler, unbekannte Felder |
| `scan:secrets` | gitleaks 8.30.1 `dir` (auch ungetrackte Dateien; `.env`, `data/`, `models/` ausgenommen) | jedem Fund |
| `scan:config` | trivy 0.76.1 `config` auf `deploy/` (Dockerfiles, Compose, gerendertes Chart, k3s-Manifeste) | MEDIUM+ ohne Eintrag in `.trivyignore.yaml` |
| `scan:deps` | trivy `fs` auf alle `uv.lock`, `Cargo.lock`, `go.mod` (ersetzt pip-audit/cargo audit) | HIGH/CRITICAL mit verfügbarem Fix |
| `scan:images` | trivy `image` auf gateway, worker, energy-exporter (`docker save` → tar, kein Docker-Socket im Scanner) | HIGH/CRITICAL mit verfügbarem Fix |
| `sbom` | syft 1.55.0 → SPDX-JSON | – |

Abhängigkeits-Updates folgen CLAUDE.md Regel 9: nur Versionen, die mindestens zwei Wochen alt sind (auch
Sicherheitsfixes); Datum per `proxy.golang.org/<modul>/@v/<version>.info` bzw. Registry prüfen.

## Abbildung auf GitHub Actions (später, durch den Nutzer – hier wird kein `.github/` angelegt)
```yaml
# .github/workflows/ci.yml (Skizze)
on: [push, pull_request]
jobs:
  ci:
    runs-on: ubuntu-24.04            # ohne GPU: alle Tests der CI brauchen keine
    steps:
      - uses: actions/checkout@<sha>   # Actions per Commit-SHA pinnen wie hier die Images per Digest
      - uses: arduino/setup-task@<sha>
      - uses: astral-sh/setup-uv@<sha>
      - uses: actions/setup-go@<sha>   # go-version-file: services/go.mod
      - uses: dtolnay/rust-toolchain@<sha>
      - run: task setup
      - run: task ci
  release:
    needs: ci
    if: startsWith(github.ref, 'refs/tags/v')
    permissions: {contents: read, packages: write, id-token: write}
    steps:
      - run: task ci:release VERSION=${GITHUB_REF_NAME}
      # dann: docker push nach ghcr.io, cosign sign (keyless, id-token), SBOM als Attestation anhängen
```
- `actions/cache` für `.cache/trivy`, `.cache/kubeconform`, Go-/Cargo-/uv-Caches.
- Deploy (CD) bleibt bewusst manuell (`task k3s:up` auf dem Zielrechner): der Rechner ist zugleich Messplatz, ein
  automatischer Rollout könnte eine laufende Messung stören.

## English summary
`task ci` runs every linter and test plus hadolint, helm lint + kubeconform, gitleaks and trivy (config + lockfile
vulnerabilities); `task ci:release` additionally builds the images, scans them with trivy and writes SPDX SBOMs with
syft. All tools are digest-pinned containers; exceptions are documented in `.trivyignore.yaml` and `.gitleaks.toml`.
The sketch above shows the later GitHub Actions mapping; no `.github/` directory exists in this repo.
