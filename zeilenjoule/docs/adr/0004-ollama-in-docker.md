# ADR 0004: Ollama runs as a pinned Docker image (no sudo)

- Status: accepted (user, Gate 1, 2026-11-06: "without sudo for now").
- Context: the system Ollama is 0.13.5 (latest v0.40.15) and upgrading it needs sudo. It's also shared with other projects (open-webui uses it), so changing it has side effects.
- Decision: Track D's Ollama contestants (d01_*) use the official `ollama/ollama` image, **pinned by digest**, on a separate port (e.g. 11435) with its own model dir `models/ollama/`. The system Ollama stays untouched.
- Consequences:
  - Reproducible version via the image digest, recorded per run.
  - The system Ollama (port 11434) must be idle (no model loaded: `ollama ps` empty) during measured runs, so it doesn't hold VRAM. The harness checks this.
  - Models are pulled again into `models/ollama/`. The existing system models aren't reused, because they belong to the other setup.
  - Revisit if the user later upgrades the system Ollama.
