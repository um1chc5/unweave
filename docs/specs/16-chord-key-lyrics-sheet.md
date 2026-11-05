# 16 — Chord, key and lyric chord sheet

Status: Doing

What this covers: a Song-view **Chord sheet** tab that runs a separate queued analysis job over existing stems, stores results in `analysis/chords.json`, supports tap-to-correct edits, Simple/Extended labels, optional local lyric transcription, and ChordPro export.

**Related:** [01 — App specification](01-app.md), [05 — Job queue](05-job-queue.md), [09 — Job progress](09-job-progress.md), [idea draft](../ideas/chord-key-lyrics-sheet.md), [UI mockup](../ideas/chord-sheet-mockup.html)

## Product

After stems exist, the user opens **Chord sheet** and clicks **Analyze chords**. Analysis does not run automatically after separation. Lyrics are an opt-in local Whisper pass and must not block a valid chord-only sheet.

## Behavior

- Job `kind: "chords"` via `POST /api/projects/{id}/analyze-chords` (`lyrics: bool`).
- Prefer `beat_this` + `autochord` when installed; fall back to librosa beat track + chroma templates.
- Refine with bass-root disambiguation and extension heuristics; expose Simple / Extended in the UI.
- Persist user overrides without re-running models (`POST .../chords` with `overrides` or `ui`).
- Export ChordPro to `exports/chords.chopro` (`POST .../export-chordpro`).
- Invalidate or mark stale when stems are replaced or trim changes; show a re-analyze hint.
- Playhead follow highlights the active chord while transport plays.

## Out of scope (this spec)

- Practice focus view / mixer timeline as primary UI
- Network lyric providers (LRCLIB, Musixmatch, …)
- BTC-ISMIR19 as the primary chord model
- Essentia / madmom

## Checklist

- [x] Analysis module + JSON store + ChordPro export
- [x] Queue job, routes, progress steps, i18n
- [x] Song Mixer | Chord sheet tabs, empty/analyzing/sheet, tap-to-correct
- [x] Bass root + extensions + confidence greying + Simple/Extended
- [x] Follow playhead + stale UX
- [x] Opt-in local faster-whisper lyrics path
- [ ] Optional setup-stage install for `.[chords]` extras (installer polish)
- [ ] Network fetch-then-align lyrics (follow-up)
