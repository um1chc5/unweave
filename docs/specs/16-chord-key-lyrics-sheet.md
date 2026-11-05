# 16 — Chord sheet (Chordify-style)

Status: Doing

What this covers: a Song-view **Chord sheet** tab that runs a separate queued analysis job over existing stems, stores results in `analysis/chords.json`, supports tap-to-correct edits, Simple/Extended labels, meter detection (**3/4, 4/4, 6/4**), bar-aligned beat slots, and ChordPro export.

**No lyrics** in product — Chordify-style chords on a meter grid only. (Lyrics were explored in the idea draft and abandoned after trial quality was too weak.)

**Related:** [01 — App specification](01-app.md), [05 — Job queue](05-job-queue.md), [09 — Job progress](09-job-progress.md), [idea draft](../ideas/chord-key-lyrics-sheet.md), [UI mockup](../ideas/chord-sheet-mockup.html)

## Product

After stems exist, the user opens **Chord sheet** and clicks **Analyze chords**. Analysis does not run automatically after separation. Output is a bar-aligned chord chart.

## Behavior

- Job `kind: "chords"` via `POST /api/projects/{id}/analyze-chords` (lyrics flag ignored if present).
- Detect beats and meter among **3/4, 4/4, 6/4**; mark downbeats; snap **one chord label per beat**, merge holds; render a **Chordify-style compact grid** (wrapping bars, beat cells, label only on chord change).
- Prefer `beat_this` + `autochord` when installed; fall back to librosa beat track + chroma templates + onset meter scoring.
- Refine with bass-root disambiguation and extension heuristics; expose Simple / Extended in the UI.
- Persist user overrides without re-running models (`POST .../chords` with `overrides` or `ui`).
- Export ChordPro to `exports/chords.chopro` (`POST .../export-chordpro`).
- Invalidate or mark stale when stems are replaced or trim changes; show a re-analyze hint.
- Playhead follow highlights the active chord while transport plays.

## Data (sketch)

`analysis/chords.json`:

- `meter`: `{ numerator, denominator, beats_per_bar, phase }` — numerator ∈ {3,4,6}, denominator 4 for shipped meters
- `beats`: `[{ t, beat, downbeat, bar_beat }]`
- `key`, `chords` (with `simple` / `extended` / `confidence` / `override?`), `ui.mode`
- `words` remains empty (legacy field; unused)

## Out of scope

- Lyric transcription / overlay (abandoned)
- Network lyric providers
- Practice focus view / mixer timeline as primary UI
- BTC-ISMIR19 as the primary chord model
- Essentia / madmom

## Checklist

- [x] Analysis module + JSON store + ChordPro export
- [x] Queue job, routes, progress steps, i18n
- [x] Song Mixer | Chord sheet tabs, empty/analyzing/sheet, tap-to-correct
- [x] Bass root + extensions + confidence greying + Simple/Extended
- [x] Follow playhead + stale UX
- [x] Meter detection 3/4 · 4/4 · 6/4 + bar-aligned sheet
- [x] Lyrics removed from product UI / analyze path
- [x] Idea doc + status board updated for Chordify-only decision
- [ ] Optional setup-stage install for `.[chords]` extras (installer polish)
