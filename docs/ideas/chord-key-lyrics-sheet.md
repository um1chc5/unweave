# Idea — Chord, key and bar-aligned chord sheet

Status: Graduated → [16 — Chord sheet (Chordify-style)](../specs/16-chord-key-lyrics-sheet.md)

What this covers: producing a **Chordify-style** chord sheet from an already-separated song — chord recognition, key/tonality, beat grid with meter (3/4, 4/4, 6/4), bar-aligned UI, and ChordPro export.

**Product decision (2026-11):** **No lyrics.** Local Whisper / fetch-align quality was not good enough for the sheet. Spec 16 ships chords-only.

**Related:** [01 — App specification](../specs/01-app.md), [Architecture](../architecture.md), [Tech stack](../tech-stack.md), [11 — GPU acceleration](../specs/11-gpu-acceleration.md), [UI mockup](chord-sheet-mockup.html)

> Historical idea notes below. Authoritative behavior is [spec 16](../specs/16-chord-key-lyrics-sheet.md).

---

## 1. Why this fits Unweave specifically

We already run Demucs and keep per-stem FLAC. A Chordify-style pipeline wants exactly those stems, and separation is the expensive half of the problem. Concretely:

| What the feature needs | What we already have |
|---|---|
| Harmony audio without drums/vocals smearing the chroma | `other` + `bass` stems from [pipeline.py](../../app/unweave/audio/pipeline.py) |
| A root/bass-note signal | the `bass` stem |
| Somewhere to put derived per-song data | project store (`analysis/chords.json`) |
| A job runner with progress and cancel | [05 — Job queue](../specs/05-job-queue.md), [09 — Job progress](../specs/09-job-progress.md) |

Feeding chord detection the mixed `other` + `bass` stems rather than the full mix is a free accuracy win that competitors without separation cannot take.

## 2. Shape of the pipeline (shipped)

```
Demucs (existing)
  ├─ other + bass ────→ chord recognition → chords
  └─ other + bass ────→ beats + meter (3/4|4/4|6/4) → downbeats
                              ↓
                    snap one chord per beat, merge holds
                              ↓
                    analysis/chords.json → Song "Chord sheet" tab
                              ↓
                         ChordPro export
```

Properties:

- **Beats + meter are the coordinate system.** UI is a Chordify-style compact grid: wrapping bars, beat cells, chord text only when it changes (holds leave empty cells).
- **Meter candidates:** 3/4, 4/4, 6/4 (numerator = beats per bar, denominator fixed at 4 for these product meters).
- **No lyrics overlay** in product. Vocals stem is unused by this feature.

## 3. Library and model options

Prefer `beat_this` + `autochord` when installed; fall back to librosa beat track + chroma templates + onset-based meter scoring. Key via Krumhansl–Schmuckler. Bass-root + extension heuristics refine labels. Deliberately exclude Essentia (AGPL) and madmom (non-commercial).

Optional install surface: `.[chords]` extras — still needs first-run Setup polish before end-user ship.

## 4. Recommended build (as shipped in phases)

- **Phase A** — beats, meter, chords, key, Song tab, ChordPro, edits  
- **Phase B** — bass-root disambiguation  
- **Phase C** — extension heuristic + Simple/Extended + confidence  
- **Phase D — lyrics** — **abandoned** after product trial (accuracy / UX)

## 5. Caveats (still true for chords)

- Chord vocabulary accuracy drops sharply beyond maj/min; editable UI matters more than chasing offline %.  
- Some chords are pitch-set ambiguous without bass/key (Am7 vs C6, etc.).  
- Meter detection can mis-label odd meters or tempo changes; 4/4 is the soft default when scores are close.

## 6. Design consequences

1. Chords must be editable (tap-to-correct).  
2. Simple / Extended toggle.  
3. Render confidence (grey low-confidence).  
4. One chord assignment per bar start time — no duplicate labels across adjacent bar lines.  
5. Store as derived data; invalidate / stale on re-separate or trim change.  
6. ChordPro is export-only over internal JSON.

## 7. Open / remaining

- First-run Setup stage for `.[chords]` (beat_this / autochord checkpoints) so end users never run pip.  
- BTC-ISMIR19 swap behind the chord interface (optional later).  
- Lyrics: closed — do not reopen without a new product brief.
