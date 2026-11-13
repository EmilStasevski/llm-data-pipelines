# ADR 0024: k3s + Helm und lokale CI/CD – GPU-Teilung, Pod Security, Registry, Scanner als Images

**Status:** angenommen (2026-11-13)

## Kontext
PLAN §12.2/§12.4 verlangt ein Helm-Chart für k3s (NetworkPolicies, PodSecurity „restricted“, PVCs) und eine lokale
CI ohne GitHub (hadolint, trivy, syft, helm lint, kubeconform, gitleaks, Abhängigkeits-Audits). Auf der Maschine läuft
k3s v1.36.5 mit dem statischen NVIDIA-Device-Plugin v0.17.0 (1 × `nvidia.com/gpu`) und `RuntimeClass nvidia`.

## Entscheidung
- **GPU-Teilung per Time-Slicing (2 Repliken):** TEI und vLLM fordern je ein `nvidia.com/gpu`. Time-Slicing teilt nur
  Rechenzeit, **keinen Speicher** – die Aufteilung macht wie in Compose `--gpu-memory-utilization 0.70`; vLLM wartet
  per Init-Container auf TEI. Alternative „ein Pod mit beiden Containern“ verworfen (getrennte Rollouts/Probes
  gehen verloren), MIG gibt es auf GeForce nicht. Änderung an `kube-system` ist einmalig (`task k3s:cluster-setup`).
- **Pod Security per Namespace:** `zeilenjoule` erzwingt „restricted“ (alle Pods nonroot, seccomp RuntimeDefault,
  keine Capabilities, read-only Root-FS). Modellgewichte kommen über ein **statisches hostPath-PV** auf `models/`
  (gleiche Bytes wie der Benchmark) – das PV nennt den Host-Pfad, nicht der Pod, daher bleibt „restricted“ gültig.
  Der Energie-Exporter braucht root (RAPL) und `/sys/…/powercap` und läuft deshalb allein in `zeilenjoule-monitoring`
  („privileged“), zusammen mit Prometheus und Grafana (gleiche Regeln und Dashboard-Dateien wie Compose, per Symlink
  im Chart, keine Kopie).
- **NetworkPolicies:** Default-Deny (Ingress + Egress) für alle Pods des Releases, dann nur die Kanten der Architektur:
  Gateway/Worker → Postgres/TEI/vLLM, Backup → Postgres, Ingress-Controller → Gateway, Prometheus → Metrik-Ports, DNS.
  Modellserver haben keinen Internet-Egress (Gewichte lokal, `HF_HUB_OFFLINE=1`). k3s setzt sie mit kube-router durch.
- **Migration als Init-Container** von Gateway und Worker statt Helm-Hook: idempotent mit Advisory-Lock (ADR 0023),
  keine Hook-Reihenfolge, funktioniert auch bei `kubectl rollout restart`.
- **Images über lokale Registry** `127.0.0.1:5000` (containerd zieht von `localhost` per HTTP ohne k3s-Konfiguration,
  also ohne sudo); `task k3s:push` schreibt die Digests, Helm deployt per Digest, nicht per Tag.
- **Secrets nie in Values:** `existingSecret`, erzeugt aus `.env` (`task k3s:secrets`, Datei mit Modus 600, kein
  Passwort auf der Kommandozeile); Passwörter gelangen per `$(VAR)`-Expansion in die DSNs, nicht ins Manifest.
- **Ratenbegrenzung pro Pod:** der Token-Bucket des Gateways liegt im Speicher; bei N Repliken gilt N × Limit.
  Für einen Rechner mit einem Gateway genügt das; ein gemeinsamer Zähler (Postgres/Redis) wäre erst bei HPA nötig.
- **CI-Werkzeuge als digest-gepinnte Container** (keine Host-Installation): hadolint, helm (lint + template),
  kubeconform (`-strict`, Schemas 1.36.5), gitleaks (dir-Modus, auch ungetrackte Dateien), trivy (config, fs, image),
  syft (SPDX-SBOM). **trivy fs ersetzt pip-audit und cargo audit**: ein Werkzeug liest `uv.lock`, `Cargo.lock` und
  `go.mod` mit derselben Datenbank. Ausnahmen stehen begründet in `.trivyignore.yaml`/`.gitleaks.toml`.
- **Zwei Stufen:** `task ci` (Lint, Tests inkl. Postgres-Integration, Scans – ohne Image-Build, ohne Secrets) und
  `task ci:release` (zusätzlich Images, trivy image, SBOM). Abbildung auf GitHub Actions: `docs/CI.md`.

## Konsequenzen
- Time-Slicing ist fair nur im Mittel: unter Last konkurrieren Einbettung und LLM um Rechenzeit (wie in Compose).
- Mehrknoten-Betrieb braucht RWX-Speicher für Uploads und ein Modell-Volume (`values-prod.yaml` zeigt die Stellen).
- Die Rust-Abhängigkeiten von `zj-parse` erscheinen nicht im Image-SBOM (gestrippte Binärdatei ohne
  `cargo auditable`); sie sind über den `Cargo.lock`-Scan abgedeckt.
