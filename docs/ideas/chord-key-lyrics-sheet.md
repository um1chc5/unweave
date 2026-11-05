# Idea — Chord, key and lyric chord-sheet detection

Status: Graduated → [16 — Chord, key and lyric sheet](../specs/16-chord-key-lyrics-sheet.md)

What this covers: producing a Chordify-style chord sheet from an already-separated song — chord recognition, key/tonality, beat grid, and optionally lyrics aligned under the chords. Library and model options, what each can realistically deliver, and where it would hook into the existing pipeline.

**Related:** [01 — App specification](../specs/01-app.md), [Architecture](../architecture.md), [Tech stack](../tech-stack.md), [11 — GPU acceleration](../specs/11-gpu-acceleration.md)

> This is an idea document, not a spec. Nothing here is committed. If it graduates, it becomes a numbered spec under `docs/specs/` and gets a row on that status board.

---

## 1. Why this fits Unweave specifically

We already run Demucs and keep per-stem FLAC. A Chordify-style pipeline wants exactly those stems, and separation is the expensive half of the problem. Concretely:

| What the feature needs | What we already have |
|---|---|
| Harmony audio without drums/vocals smearing the chroma | `other` + `bass` stems from [pipeline.py:303](../../app/unweave/audio/pipeline.py) |
| A root/bass-note signal | the `bass` stem |
| Clean vocals for lyric transcription | the `vocals` stem |
| Somewhere to put derived per-song data | project store + metadata ([projects/metadata.py](../../app/unweave/projects/metadata.py)) |
| A job runner with progress and cancel | [05 — Job queue](../specs/05-job-queue.md), [09 — Job progress](../specs/09-job-progress.md) |

Feeding chord detection the mixed `other` + `bass` stems rather than the full mix is a free accuracy win that competitors without separation cannot take.

## 2. Shape of the pipeline

Three independent analysis flows over stems we already produce, merged on a shared beat grid.

```
Demucs (existing)
  ├─ other + bass ────→ chord recognition → chords [(start, end, "G")]
  ├─ full mix ────────→ beat/downbeat      → beats  [(time, beat_no, is_downbeat)]
  └─ vocals ──────────→ lyrics + align     → words  [(start, end, "yes")]
                                                ↓
                      snap chords to beats, interval-join words
                                                ↓
                                     ChordPro / internal JSON
```

Three properties worth keeping:

- **Beats are the shared coordinate system.** Without them you join two independently-noisy float timelines and chords land tens-to-hundreds of ms off where a player expects them. Snapping chord onsets to the nearest beat is what makes output look hand-made instead of machine-guessed.
- **The flows are independent**, so they parallelize, and each can fail alone.
- **Lyrics are an optional overlay.** A failed lyric lookup or a bad transcription must still leave a valid chord-only sheet. This is the single most important structural decision in the whole idea — see §6.

## 3. Library and model options

Nothing in the repo does any of this today (no chord, key, beat, or ASR code). Everything below is new dependency surface. Note the distinction between *libraries you install* and *models you vendor*, because it drives packaging and installer size.

### 3.1 Chord recognition

| Option | What it is | Pros | Cons |
|---|---|---|---|
| `autochord` | lib bundling a pretrained CNN + HMM; exports `.lab` / MIDI | pip-installable, simple API, permissive license | maj/min only; no extensions at all |
| BTC-ISMIR19 | **model + research repo**, no pip package | best published accuracy; large vocabulary head | vendor the code, host a checkpoint, no upstream maintenance |
| `madmom` (`CNNChordFeatureProcessor` + CRF) | lib with weights shipped inside | long-standing reference quality | stale; numpy pinning pain; **non-commercial license clause** |
| Essentia `ChordsDetectionBeats` | C++ lib w/ Python bindings; classic chroma + template matching, no NN | mature, fast, beat-synchronous by design | **AGPL-3.0** |
| `librosa` + own Viterbi/template matching | pure DSP, zero models | no new deps beyond what `guitars` already pulls; fully ours | we own the accuracy problem; clearly worse than a trained model |

### 3.2 Beat and downbeat tracking

| Option | Pros | Cons |
|---|---|---|
| `beat_this` (2024) | current state of the art; pip-installable; torch, which we already ship | checkpoint download at runtime; another model in the cache |
| `madmom` `RNNDownBeatProcessor` | the long-time standard; good downbeats | same staleness / license caveat as above |
| `librosa.beat.beat_track` | already available, no new dep | no downbeats, weaker on tempo changes and rubato |

### 3.3 Key / tonality

| Option | Pros | Cons |
|---|---|---|
| Krumhansl-Schmuckler over librosa CQT-chroma | **an algorithm, not a dependency** — ~50 lines, correlate chroma against 24 key profiles | plain accuracy; weak on modulation and modal material |
| Essentia `KeyExtractor` | several tuned profiles, well validated | AGPL-3.0 |
| `madmom` `CNNKeyRecognition` | trained model, good | license + staleness |

### 3.4 Lyrics

| Option | Pros | Cons |
|---|---|---|
| Whisper / `faster-whisper` on the vocals stem | multilingual (~99 languages); separation materially helps; `faster-whisper` is fast on CPU | sung-lyric accuracy is far below speech; native word timestamps are attention-derived and sloppy on sustained notes |
| WhisperX | phoneme-level forced alignment → much better word timings | default align models cover a subset of languages; **verify its license before committing** |
| `whisper-timestamped` | DTW-refined timings | **verify license** |
| Montreal Forced Aligner | strong alignment, pretrained models for ~30 languages incl. Vietnamese | conda-heavy, awkward to ship in an installer |
| Fetch known lyrics, then force-align (LRCLIB free/open; Musixmatch commercial) | far easier and more accurate than open-vocabulary ASR — only solves *when*, not *what* | needs a match; coverage varies by market; network dependency |

## 4. Recommended build

**Phase A — chords, key, beats (no lyrics).** Language-agnostic, no network, no lyrics licensing questions, and useful on its own.

- Beats: `beat_this` (reuses the torch dependency we already carry).
- Chords: `autochord` for v1 behind a narrow interface, so BTC can be swapped in later without touching callers.
- Key: Krumhansl-Schmuckler over librosa chroma. No dependency, and we can tune it.
- Run on mixed `other` + `bass`, not the full mix.

Rationale in one line: this combination adds no native dependencies, no AGPL, no non-commercial clause, and no new installer components beyond model checkpoints.

**Phase B — root/inversion from the bass stem.** Cheapest accuracy win available (see §5), and it directly attacks the ambiguity table below.

**Phase C — extension heuristic.** Given a detected triad plus the key, test chroma energy at the 7th / 9th / 11th above the root and add the extension past a threshold. Preferable to a 170-class model head because it is interpretable, tunable, and yields a confidence value the UI can render differently.

**Phase D — lyrics overlay.** Fetch-then-align as the primary path, transcription as fallback. Strictly optional, strictly non-blocking.

### Deliberately excluded

- **Essentia** — AGPL-3.0 is incompatible with how we ship a desktop installer, regardless of quality.
- **madmom** — the non-commercial clause plus numpy pinning against our existing `>=1.26` floor.
- A large-vocabulary chord model as the primary output — §5 explains why the headline accuracy number is misleading.

## 5. Caveats — what this can and cannot deliver

### 5.1 Chord vocabulary is the binding constraint

Rough, directional figures from MIREX-style evaluation:

| Vocabulary | Ballpark accuracy |
|---|---|
| maj/min (24 classes) | ~80–85% |
| + sevenths (~60 classes) | ~55–70% |
| large vocab w/ inversions, sus, dim, aug, m7b5 (~170 classes) | ~50–60% overall, **much worse on the rare classes** |

That last caveat matters more than the number. Training sets (Isophonics, Billboard, JAAH) are overwhelmingly maj/min; half-diminished and dim7 can be well under 1% of frames. Class imbalance means per-class recall on exactly the interesting chords is poor even when the aggregate looks acceptable.

### 5.2 Some chords are genuinely unresolvable from pitch content

| Chord | Shares its exact pitch set with |
|---|---|
| Am7 (A C E G) | C6 (C E G A) — identical |
| Bm7b5 (B D F A) | Dm6 (D F A B) — identical |
| Cdim7 | Ebdim7, Gbdim7, Adim7 — symmetric under inversion |

Only the bass note and harmonic function in key resolve these. A perfect pitch transcriber still gets them wrong without reasoning about function — this is an ambiguity in the problem, not a model deficiency. Hence Phase B.

Practical expectations per chord type: **dominant 7** works reasonably often but frequently collapses to plain major, since the 7th is a single voice. **m7** is often detected but confused per the table. **sus** usually reads as the resolved triad, because sus chords typically resolve inside a beat. **dim / dim7 / m7b5** — do not count on these.

### 5.3 Ground truth is partly subjective

Annotators disagree on whether a passing 7th "counts," and players voice extensions absent from the chart. Some of the missing accuracy is disagreement, not error. This argues against chasing offline metrics and in favour of a good correction UI.

### 5.4 Lyrics are weaker than people expect, and unevenly so

Sung-lyric ASR is materially worse than speech even on isolated vocals. For **tonal languages — Vietnamese, Mandarin, Cantonese, Thai — there is an extra structural problem**: these languages distinguish words by pitch contour, and when a melody dictates pitch, that cue is largely destroyed. Expect sung Vietnamese to trail the already-mediocre sung-English baseline.

Forced alignment degrades far more gracefully than transcription here, because matching phonemes to known text does not depend on the tone cue. This is the main reason fetch-then-align is the recommended primary path rather than a nicety. For Vietnamese the weak link then becomes **lyric availability**, not the technology: LRCLIB's V-pop coverage is thinner than its English coverage, and Zing MP3 has good synced Vietnamese lyrics but no open API.

### 5.5 Lyrics touch publishing rights

Transcribing locally from a file the user already owns is a different legal posture from shipping, caching, or redistributing a lyrics database. That distinction may constrain the design more than any accuracy figure. It also sits awkwardly against spec 01's local-only / no-network posture, since fetch-then-align requires a network call.

### 5.6 Cost and footprint

| Concern | Note |
|---|---|
| Installer / cache size | three more model checkpoints; Whisper in particular is large. Should be opt-in components, consistent with [04 — First-run setup](../specs/04-first-run-setup.md). |
| Runtime | analysis is cheap next to Demucs, except Whisper. `faster-whisper` on CPU is tolerable; large models are not. |
| GPU | all three flows benefit, and all would ride on [11 — GPU acceleration](../specs/11-gpu-acceleration.md) if that lands. |
| Offline promise | Phases A–C are fully offline. Only Phase D's lyric lookup needs network. |

## 6. Design consequences to decide early

1. **Chords must be editable.** No product in this space ships unattended extended-chord accuracy — Chordify itself offers an editor and simplified/extended views. A tap-to-correct UI with ranked alternatives is worth more than another 5% offline, and the data model must assume user overrides from day one rather than bolting them on.
2. **Expose a simple/extended toggle** rather than one "true" answer. Guitarists mostly want the triad; jazz players want the extension and will correct it anyway.
3. **Render confidence.** The Phase C heuristic produces one naturally; greying out low-confidence extensions sets expectations better than any amount of accuracy work.
4. **Keep word end times.** Chord changes mid-word are constant with melisma, and ChordPro supports in-word insertion (`Yes[G]ter[D]day`). Do not collapse word spans to single timestamps.
5. **Chord-only rows are required.** Chord onsets landing in word-free gaps — intros, solos, turnarounds — need their own rows or the sheet is unplayable.
6. **Store analysis as derived data, invalidated on re-separation or trim change.** Trim offsets already shift project timelines ([02 — Pre-separation trim](../specs/02-pre-separation-trim.md)); a cached beat grid that silently survives a trim edit is a bug.

## 7. Open questions

- Does WhisperX (or `whisper-timestamped`) carry a license we can ship? Unverified, and it gates the Phase D design.
- Does WhisperX have a default Vietnamese alignment model, or do we point it at a wav2vec2 CTC checkpoint from HuggingFace?
- Is BTC's accuracy gain over `autochord` worth vendoring unmaintained research code and hosting a checkpoint?
- ChordPro as the internal representation, or ChordPro only as an export format over our own JSON?
- Does the "no network" posture of spec 01 permit an opt-in lyric lookup, or does Phase D need to stay transcription-only?
- Can this run as a separate queued job over an existing project, rather than extending the separation job? (Likely yes, and likely better — it lets users analyse already-separated songs without re-running Demucs.)
