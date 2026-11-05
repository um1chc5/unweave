# Docs Index

## Core

- [overview.md](overview.md) — What Unweave is, project structure, the two layers
- [architecture.md](architecture.md) — Data flow diagram, design rules, module responsibilities, models
- [tech-stack.md](tech-stack.md) — Technologies used and alternatives considered
- [desktop-app.md](desktop-app.md) — Packaging: NSIS installer, embedded runtime, pywebview, Linux/macOS scripts
- [linux-install.md](linux-install.md) — Linux install guide, launcher, uninstall, troubleshooting
- [macos-install.md](macos-install.md) — macOS install guide (unsigned), Gatekeeper, paths

## Specs

- [specs/README.md](specs/README.md) — Ordered app specification and status board
  - [01 — App specification](specs/01-app.md)
  - [02 — Pre-separation trim](specs/02-pre-separation-trim.md)
  - [03 — Output formats](specs/03-output-formats.md)
  - [04 — First-run setup](specs/04-first-run-setup.md)
  - [05 — Job queue and concurrency](specs/05-job-queue.md)
  - [06 — Close behavior](specs/06-close-behavior.md)
  - [07 — Disk usage and cache](specs/07-disk-usage.md)
  - [08 — Performance and hidden optimizations](specs/08-performance.md)
  - [09 — Job progress and ETA](specs/09-job-progress.md)
  - [10 — In-app update check](specs/10-updates.md)

## Ideas

Explorations, not commitments. Nothing here is scheduled; if an idea graduates it becomes a numbered spec.

- [specs/16-chord-key-lyrics-sheet.md](specs/16-chord-key-lyrics-sheet.md) — Chord sheet Song tab (graduated from ideas/)

## Learning

- [learning/notebooklm-pack.md](learning/notebooklm-pack.md) — **All-in-one** NotebookLM source (upload this only, then say `Start tutoring`)
- [learning/study-guide.md](learning/study-guide.md) — Short human cheat sheet

## Handoff

- [archives/known-issues-handoff.md](archives/known-issues-handoff.md) — Installer and first-run bugs for a fresh session

Upstream: Unweave is based on [Sound Razor](https://github.com/pedromtcosta/sound-razor) by
pedromtcosta. The Python package (`unweave`) and the model cache (`.sound-razor/`)
keep the upstream name on purpose, so existing installs keep working.
