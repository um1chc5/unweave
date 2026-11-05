"""Chord / key / beat analysis over separated stems.

Writes `analysis/chords.json`. Prefer beat_this + autochord when installed;
fall back to librosa so the job and tests still run without those packages.
Phases B (bass root) and C (extension heuristic) refine labels in-process.
Optional Phase D lyrics use faster-whisper on the vocals stem.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import tempfile
import time
from pathlib import Path

from ..projects.store import atomic_json, stem_file, valid_trim
from ..tools import ensure_ffmpeg_on_path, MISSING_FFMPEG

SCHEMA = 1
ANALYSIS_DIR = "analysis"
CHORDS_FILE = "chords.json"
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Schmuckler key profiles (major / minor), C-rooted.
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def chords_path(project: Path) -> Path:
    return project / ANALYSIS_DIR / CHORDS_FILE


def chords_available() -> bool:
    """True when either the preferred stack or the librosa fallback can run."""
    if importlib.util.find_spec("librosa") is None:
        return False
    if importlib.util.find_spec("numpy") is None:
        return False
    if importlib.util.find_spec("soundfile") is None:
        return False
    return True


def lyrics_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def read_chords(project: Path) -> dict | None:
    path = chords_path(project)
    if not path.is_file() or path.is_symlink():
        return None
    if path.stat().st_size > 5_000_000:
        raise ValueError("Chord analysis file is too large.")
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    # Cheap staleness: mtimes + trim (full checksums are stored for diagnostics only).
    source = data.get("source") or {}
    mtimes = source.get("mtimes") or {}
    stems_dir = project / "stems"
    if isinstance(mtimes, dict) and mtimes:
        for name, stamp in mtimes.items():
            file_path = stems_dir / name
            if not file_path.is_file() or file_path.stat().st_mtime_ns != stamp:
                data["stale"] = True
                data["stale_reason"] = "stems_changed"
                break
    trim_now = None
    settings = stems_dir / "separation.json"
    if settings.is_file():
        try:
            separation = json.loads(settings.read_text(encoding="utf-8"))
            trim_now = valid_trim(separation.get("trim")) if isinstance(separation, dict) else None
        except (ValueError, OSError):
            trim_now = None
    if source.get("trim") != trim_now:
        data["stale"] = True
        data["stale_reason"] = "trim_changed"
    return data


def invalidate_chords(project: Path) -> None:
    """Remove derived chord analysis (re-separation / trim change)."""
    path = chords_path(project)
    if path.is_file() and not path.is_symlink():
        path.unlink(missing_ok=True)
    parent = project / ANALYSIS_DIR
    if parent.is_dir() and not path.exists() and not any(parent.iterdir()):
        parent.rmdir()


def mark_chords_stale(project: Path, reason: str) -> dict | None:
    data = read_chords(project)
    if not data:
        return None
    data["stale"] = True
    data["stale_reason"] = reason
    atomic_json(chords_path(project), data)
    return data


def save_chord_overrides(project: Path, overrides: list) -> dict:
    """Apply user edits. Each item: {index, override} where override is str|null."""
    data = read_chords(project)
    if not data:
        raise ValueError("No chord analysis to edit. Run Analyze chords first.")
    chords = data.get("chords")
    if not isinstance(chords, list):
        raise ValueError("Invalid chord analysis.")
    if not isinstance(overrides, list):
        raise ValueError("Overrides must be a list.")
    for item in overrides:
        if not isinstance(item, dict):
            raise ValueError("Each override must be an object.")
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(chords):
            raise ValueError("Invalid chord index.")
        override = item.get("override", ...)
        if override is ...:
            raise ValueError("Missing override value.")
        if override is None:
            chords[index].pop("override", None)
        elif isinstance(override, str) and 0 < len(override.strip()) <= 32:
            chords[index]["override"] = override.strip()
        else:
            raise ValueError("Chord override must be a short string or null.")
    data["chords"] = chords
    data["updated_at"] = time.time()
    atomic_json(chords_path(project), data)
    return data


def save_ui_prefs(project: Path, ui: dict) -> dict:
    data = read_chords(project)
    if not data:
        raise ValueError("No chord analysis yet.")
    if not isinstance(ui, dict):
        raise ValueError("UI prefs must be an object.")
    current = data.setdefault("ui", {})
    mode = ui.get("mode")
    if mode in {"simple", "extended"}:
        current["mode"] = mode
    lyrics_mode = ui.get("lyrics_mode")
    if lyrics_mode in {"with_lyrics", "chords_only"}:
        current["lyrics_mode"] = lyrics_mode
    data["ui"] = current
    atomic_json(chords_path(project), data)
    return data


def _stem_checksum(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _mix_harmony(project: Path, work: Path, report=None) -> tuple[Path, dict]:
    """Mix other+bass (fallback: other, then original) to a mono-compatible stereo WAV."""
    import numpy as np
    import soundfile as sf

    stems_dir = project / "stems"
    names = []
    for stem in ("other", "bass"):
        name = stem_file(stems_dir, stem)
        if name:
            names.append(name)
    if not names:
        # Guitar-heavy separations may fold harmony into other only.
        for stem in ("guitar", "piano"):
            name = stem_file(stems_dir, stem)
            if name:
                names.append(name)
                break
    if not names:
        raise ValueError("Need at least an other or bass stem to analyze chords.")

    arrays = []
    sr = None
    for name in names:
        path = stems_dir / name
        audio, rate = sf.read(str(path), always_2d=True)
        if sr is None:
            sr = rate
        elif rate != sr:
            raise ValueError("Stem sample rates do not match.")
        arrays.append(audio.astype(np.float64))
        if report:
            report("prepare", 10 + 10 * len(arrays))
    mixed = sum(arrays) / max(len(arrays), 1)
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 1.0:
        mixed = mixed / peak
    out = work / "harmony.wav"
    sf.write(str(out), mixed.astype(np.float32), sr, subtype="PCM_16")
    source = {
        "stems": names,
        "checksums": {name: _stem_checksum(stems_dir / name) for name in names},
        "mtimes": {name: (stems_dir / name).stat().st_mtime_ns for name in names},
        "sample_rate": sr,
    }
    return out, source


def _clamp_beats_per_bar(value: int) -> int:
    """Map a raw estimate onto the meters we support: 3/4, 4/4, 6/4."""
    if value <= 3:
        return 3
    if value >= 6:
        return 6
    return 4


def _meter_from_downbeats(beat_times: list[float], downbeat_times: list[float]) -> tuple[int, int]:
    """Infer beats-per-bar + phase from beat_this downbeats."""
    import numpy as np
    if len(beat_times) < 4 or len(downbeat_times) < 2:
        return 4, 0
    beat_iv = float(np.median(np.diff(beat_times)))
    down_iv = float(np.median(np.diff(downbeat_times)))
    if beat_iv <= 1e-6:
        return 4, 0
    bpp = _clamp_beats_per_bar(int(round(down_iv / beat_iv)))
    # Phase = beat index of the first downbeat
    first = downbeat_times[0]
    phase = int(min(range(len(beat_times)), key=lambda i: abs(beat_times[i] - first))) % bpp
    return bpp, phase


def _estimate_meter(y, sr, beat_times: list[float]) -> tuple[int, int]:
    """Pick 3, 4, or 6 beats/bar by which grid puts strong onsets on downbeats."""
    import librosa
    import numpy as np
    if len(beat_times) < 8:
        return 4, 0
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    frames = librosa.time_to_frames(np.asarray(beat_times), sr=sr)
    frames = np.clip(frames, 0, len(onset) - 1)
    strengths = onset[frames].astype(float)
    best = (-1e9, 4, 0)  # score, bpp, phase
    for bpp in (3, 4, 6):
        for phase in range(bpp):
            idx = np.arange(len(strengths))
            down = ((idx - phase) % bpp) == 0
            if down.sum() < 2 or (~down).sum() < 2:
                continue
            score = float(strengths[down].mean() - strengths[~down].mean())
            # Mild preference for 4/4 when scores are close (most pop material).
            if bpp == 4:
                score += 0.02
            if score > best[0]:
                best = (score, bpp, phase)
    return best[1], best[2]


def _beats_with_meter(beat_times: list[float], bpp: int, phase: int) -> list[dict]:
    rows = []
    for i, t in enumerate(beat_times):
        rows.append({
            "t": float(t),
            "beat": i + 1,
            "downbeat": ((i - phase) % bpp) == 0,
            "bar_beat": ((i - phase) % bpp) + 1,
        })
    return rows


def _detect_beats(audio_path: Path, report=None) -> tuple[list[dict], dict]:
    """Return (beat rows, meter). Meter is one of 3/4, 4/4, 6/4."""
    import librosa
    import numpy as np

    meter = {"numerator": 4, "denominator": 4, "beats_per_bar": 4, "phase": 0}
    if importlib.util.find_spec("beat_this") is not None:
        try:
            from beat_this.inference import File2Beats
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            file2beats = File2Beats(checkpoint_path="final0", device=device, dbn=False)
            beats, downbeats = file2beats(str(audio_path))
            beat_times = [float(t) for t in beats]
            down_times = [float(t) for t in downbeats]
            bpp, phase = _meter_from_downbeats(beat_times, down_times)
            # Re-check with onset scoring when downbeat list is thin.
            if len(down_times) < max(3, len(beat_times) // 12):
                y, sr = librosa.load(str(audio_path), sr=None, mono=True)
                bpp, phase = _estimate_meter(y, sr, beat_times)
            meter = {
                "numerator": bpp, "denominator": 4,
                "beats_per_bar": bpp, "phase": phase,
            }
            rows = _beats_with_meter(beat_times, bpp, phase)
            if report:
                report("beats", 35)
            return rows, meter
        except Exception:
            pass

    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    _tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    times = [float(t) for t in librosa.frames_to_time(beat_frames, sr=sr)]
    bpp, phase = _estimate_meter(y, sr, times)
    meter = {
        "numerator": bpp, "denominator": 4,
        "beats_per_bar": bpp, "phase": phase,
    }
    rows = _beats_with_meter(times, bpp, phase)
    if report:
        report("beats", 35)
    return rows, meter


def _normalize_label(raw: str) -> tuple[str, str]:
    """Return (simple, display) from autochord-style 'D:maj' / 'N' / 'Am'."""
    text = (raw or "N").strip()
    if text in {"N", "X", ""}:
        return "N", "N"
    if ":" in text:
        root, quality = text.split(":", 1)
        quality = quality.lower()
        if quality in {"maj", "major"}:
            return root, root
        if quality in {"min", "minor", "m"}:
            return f"{root}m", f"{root}m"
        return root, f"{root}:{quality}"
    return text, text


def _detect_chords_raw(audio_path: Path, report=None) -> list[tuple[float, float, str]]:
    if importlib.util.find_spec("autochord") is not None:
        try:
            import autochord
            rows = autochord.recognize(str(audio_path))
            if report:
                report("chords", 55)
            return [(float(a), float(b), str(c)) for a, b, c in rows]
        except Exception:
            pass
    # Template-matching fallback over chroma.
    import librosa
    import numpy as np
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    hop = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    templates = []
    labels = []
    for root in range(12):
        maj = np.zeros(12)
        maj[[root, (root + 4) % 12, (root + 7) % 12]] = 1
        mi = np.zeros(12)
        mi[[root, (root + 3) % 12, (root + 7) % 12]] = 1
        templates.extend([maj, mi])
        labels.extend([f"{NOTE_NAMES[root]}:maj", f"{NOTE_NAMES[root]}:min"])
    templates = np.stack(templates, axis=0)
    templates = templates / (np.linalg.norm(templates, axis=1, keepdims=True) + 1e-9)
    norms = chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-9)
    scores = templates @ norms
    best = scores.argmax(axis=0)
    conf = scores.max(axis=0)
    times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr, hop_length=hop)
    segments = []
    start_i = 0
    for i in range(1, len(best) + 1):
        if i == len(best) or best[i] != best[start_i]:
            label = labels[int(best[start_i])]
            if float(conf[start_i:i].mean()) < 0.35:
                label = "N"
            segments.append((float(times[start_i]), float(times[min(i, len(times) - 1)]), label))
            start_i = i
    if report:
        report("chords", 55)
    return segments


def _estimate_key(audio_path: Path, report=None) -> dict:
    import librosa
    import numpy as np
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    chroma = chroma / (np.linalg.norm(chroma) + 1e-9)
    best_name, best_score = "C", -1.0
    for shift in range(12):
        rolled = np.roll(chroma, -shift)
        for profile, mode in ((_MAJOR_PROFILE, ""), (_MINOR_PROFILE, "m")):
            p = np.asarray(profile, dtype=float)
            p = p / (np.linalg.norm(p) + 1e-9)
            score = float(np.dot(rolled, p))
            name = f"{NOTE_NAMES[shift]}{mode}"
            if score > best_score:
                best_score, best_name = score, name
    if report:
        report("key", 65)
    return {"name": best_name, "confidence": round(max(0.0, min(1.0, best_score)), 3)}


def _snap_to_beats(chords: list[tuple[float, float, str]], beats: list[dict]) -> list[dict]:
    """Quantize raw chord segments onto the beat grid.

    One label per beat (majority overlap), then merge consecutive identical
    labels so a bar reads like a chart (~1–4 changes), not a chroma flicker.
    """
    times = [float(b["t"]) for b in beats] if beats else []
    if len(times) < 2:
        # No usable grid — keep only segments that last ~a beat or more.
        rows = []
        for start, end, raw in chords:
            simple, display = _normalize_label(raw)
            if simple == "N" or (end - start) < 0.35:
                continue
            rows.append({
                "start": round(float(start), 3),
                "end": round(float(end), 3),
                "label": display,
                "simple": simple,
                "extended": simple,
                "confidence": 0.55,
                "alternatives": _alternatives(simple),
            })
        return _merge_adjacent(rows)

    step = times[1] - times[0] if len(times) > 1 else 0.5
    edges = times + [times[-1] + max(step, 0.25)]
    per_beat: list[tuple[float, float, str, str]] = []
    for i, start in enumerate(times):
        end = edges[i + 1]
        best_label, best_overlap = "N", 0.0
        for seg_start, seg_end, raw in chords:
            overlap = max(0.0, min(seg_end, end) - max(seg_start, start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_label = raw
        # Require covering a meaningful slice of the beat; otherwise keep previous.
        if best_overlap < (end - start) * 0.25 and per_beat:
            simple, display = per_beat[-1][2], per_beat[-1][3]
        else:
            simple, display = _normalize_label(best_label)
        per_beat.append((start, end, simple, display))

    rows = []
    for start, end, simple, display in per_beat:
        if simple == "N":
            continue
        rows.append({
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "label": display,
            "simple": simple,
            "extended": simple,
            "confidence": 0.7,
            "alternatives": _alternatives(simple),
        })
    return _merge_adjacent(rows)


def _merge_adjacent(rows: list[dict]) -> list[dict]:
    if not rows:
        return rows
    out = [dict(rows[0])]
    for row in rows[1:]:
        prev = out[-1]
        # Allow a small timing gap between beat slots when merging holds.
        if prev["simple"] == row["simple"] and row["start"] - prev["end"] <= 0.15:
            prev["end"] = row["end"]
            prev["confidence"] = min(prev.get("confidence", 1.0), row.get("confidence", 1.0))
        else:
            out.append(dict(row))
    return out


def _alternatives(simple: str) -> list[str]:
    if simple == "N" or not simple:
        return ["N"]
    root = simple.rstrip("m")
    if simple.endswith("m"):
        return [simple, root, f"{root}7", f"{simple}7", "N"]
    return [simple, f"{simple}m", f"{simple}7", f"{simple}maj7", "N"]


def _bass_root_pc(project: Path, start: float, end: float) -> int | None:
    import librosa
    import numpy as np
    name = stem_file(project / "stems", "bass")
    if not name:
        return None
    path = project / "stems" / name
    y, sr = librosa.load(str(path), sr=22050, mono=True, offset=max(0, start), duration=max(0.05, end - start))
    if y.size < 256:
        return None
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    return int(np.argmax(chroma))


def _note_pc(name: str) -> int | None:
    base = name.rstrip("m").replace("maj7", "").replace("7", "").replace("maj", "")
    # Map enharmonics lightly
    aliases = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
    base = aliases.get(base, base)
    try:
        return NOTE_NAMES.index(base)
    except ValueError:
        return None


def refine_bass_roots(project: Path, chords: list[dict]) -> list[dict]:
    """Phase B — prefer the chord whose root matches the bass pitch class."""
    for chord in chords:
        if chord.get("simple") == "N":
            continue
        pc = _bass_root_pc(project, chord["start"], chord["end"])
        if pc is None:
            continue
        simple = chord["simple"]
        root_pc = _note_pc(simple)
        # Am7 vs C6 style: if bass matches relative major/minor partner, flip.
        if simple.endswith("m"):
            relative = NOTE_NAMES[(root_pc + 3) % 12] if root_pc is not None else None
            if relative and pc == _note_pc(relative):
                chord["simple"] = relative
                chord["label"] = relative
                chord["extended"] = relative
                chord["confidence"] = min(chord.get("confidence", 0.7), 0.55)
                chord["alternatives"] = _alternatives(relative)
            elif root_pc is not None and pc == root_pc:
                chord["confidence"] = min(1.0, chord.get("confidence", 0.7) + 0.1)
        else:
            relative = f"{NOTE_NAMES[(root_pc - 3) % 12]}m" if root_pc is not None else None
            if relative and pc == _note_pc(relative):
                chord["simple"] = relative
                chord["label"] = relative
                chord["extended"] = relative
                chord["confidence"] = min(chord.get("confidence", 0.7), 0.55)
                chord["alternatives"] = _alternatives(relative)
            elif root_pc is not None and pc == root_pc:
                chord["confidence"] = min(1.0, chord.get("confidence", 0.7) + 0.1)
    return chords


def refine_extensions(audio_path: Path, chords: list[dict], key: dict, report=None) -> list[dict]:
    """Phase C — add 7th extensions from chroma energy above the triad."""
    import librosa
    import numpy as np
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    hop = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    for chord in chords:
        simple = chord.get("simple") or "N"
        if simple == "N":
            chord["extended"] = "N"
            continue
        root_pc = _note_pc(simple)
        if root_pc is None:
            chord["extended"] = simple
            continue
        start_f = librosa.time_to_frames(chord["start"], sr=sr, hop_length=hop)
        end_f = librosa.time_to_frames(chord["end"], sr=sr, hop_length=hop)
        start_f = max(0, int(start_f))
        end_f = max(start_f + 1, int(end_f))
        slice_ = chroma[:, start_f:end_f].mean(axis=1)
        seventh = (root_pc + (10 if simple.endswith("m") else 11)) % 12  # m7 vs maj7 tendency; prefer dom7 for major
        if not simple.endswith("m"):
            seventh = (root_pc + 10) % 12  # dominant 7th
        triad = [root_pc, (root_pc + (3 if simple.endswith("m") else 4)) % 12, (root_pc + 7) % 12]
        triad_e = float(np.mean(slice_[triad])) + 1e-9
        seventh_e = float(slice_[seventh])
        ratio = seventh_e / triad_e
        extended = simple
        conf = chord.get("confidence", 0.7)
        if ratio > 0.55:
            extended = f"{simple}7"
            conf = min(1.0, 0.4 + ratio * 0.5)
        chord["extended"] = extended
        chord["label"] = extended if extended != simple else simple
        chord["confidence"] = round(conf, 3)
        alts = _alternatives(simple)
        if extended not in alts:
            alts = [extended, *alts]
        chord["alternatives"] = alts
    if report:
        report("chords", 75)
    return chords


def _transcribe_lyrics(project: Path, report=None) -> list[dict]:
    if not lyrics_available():
        raise ValueError("Lyrics analysis needs faster-whisper. Install the chords extras.")
    name = stem_file(project / "stems", "vocals")
    if not name:
        raise ValueError("No vocals stem to transcribe. Separate with Vocals selected, then re-analyze.")
    import numpy as np
    import soundfile as sf
    from faster_whisper import WhisperModel
    if report:
        report("lyrics", 78)
    # Load via soundfile — avoids PyAV `metadata_errors` breakage on some av builds
    # when faster-whisper opens FLAC paths directly.
    path = project / "stems" / name
    audio, sr = sf.read(str(path), always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = np.mean(audio, axis=1)
    audio = np.asarray(audio, dtype=np.float32)
    if sr != 16000:
        # faster-whisper expects 16 kHz when given a raw array.
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000).astype(np.float32)
    # First run downloads the model; keep it small/CPU-friendly for desktop.
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        audio, word_timestamps=True, vad_filter=True, beam_size=1,
    )
    words = []
    for segment in segments:
        for word in (segment.words or []):
            text = (word.word or "").strip()
            if not text:
                continue
            words.append({
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                "text": text,
            })
    if report:
        report("lyrics", 90)
    if not words:
        raise ValueError("Whisper returned no words from the vocals stem.")
    return words


def analyze_project(project: Path, *, lyrics: bool = False, report=None) -> dict:
    """Run chord/key/beat analysis and write analysis/chords.json.

    Lyrics are intentionally unused (Chordify-style chords-only). The ``lyrics``
    flag is accepted for API compatibility but ignored.
    """
    del lyrics  # product decision: no lyric transcription in the sheet
    if not chords_available():
        raise ValueError("Chord analysis needs librosa, numpy, and soundfile.")
    if not ensure_ffmpeg_on_path():
        # FFmpeg not strictly required for wav stems via soundfile, but keep parity.
        pass

    def progress(phase: str, pct: float):
        if report:
            report(phase, pct)

    progress("prepare", 5)
    with tempfile.TemporaryDirectory(prefix="unweave-chords-") as temporary:
        work = Path(temporary)
        harmony, source = _mix_harmony(project, work, report=progress)
        progress("prepare", 20)
        beats, meter = _detect_beats(harmony, report=progress)
        raw = _detect_chords_raw(harmony, report=progress)
        key = _estimate_key(harmony, report=progress)
        chords = _snap_to_beats(raw, beats)
        progress("chords", 70)
        chords = refine_bass_roots(project, chords)
        chords = refine_extensions(harmony, chords, key, report=progress)
        progress("finalize", 95)

        separation = {}
        settings = project / "stems" / "separation.json"
        if settings.is_file():
            import json
            try:
                separation = json.loads(settings.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                separation = {}
        trim = valid_trim(separation.get("trim")) if isinstance(separation, dict) else None
        data = {
            "schema": SCHEMA,
            "created_at": time.time(),
            "stale": False,
            "source": {
                **source,
                "trim": trim,
                "backend": {
                    "beats": "beat_this" if importlib.util.find_spec("beat_this") else "librosa",
                    "chords": "autochord" if importlib.util.find_spec("autochord") else "librosa",
                },
            },
            "key": key,
            "meter": meter,
            "beats": beats,
            "chords": chords,
            "words": [],
            "ui": {"mode": "simple", "lyrics_mode": "chords_only"},
        }
        out_dir = project / ANALYSIS_DIR
        out_dir.mkdir(exist_ok=True)
        atomic_json(chords_path(project), data)
        progress("done", 100)
        return data
