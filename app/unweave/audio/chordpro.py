"""Export analysis/chords.json to ChordPro text."""

from __future__ import annotations

from pathlib import Path


def display_label(chord: dict, *, mode: str = "simple") -> str:
    override = chord.get("override")
    if isinstance(override, str) and override.strip():
        return override.strip()
    if mode == "extended":
        return chord.get("extended") or chord.get("simple") or "N"
    return chord.get("simple") or chord.get("label") or "N"


def to_chordpro(data: dict, *, title: str = "", artist: str = "", mode: str = "simple") -> str:
    if not isinstance(data, dict):
        raise ValueError("Invalid chord analysis.")
    key = (data.get("key") or {}).get("name") or ""
    chords = data.get("chords") or []
    words = data.get("words") or []
    lines = []
    if title:
        lines.append(f"{{title: {title}}}")
    if artist:
        lines.append(f"{{artist: {artist}}}")
    if key:
        lines.append(f"{{key: {key}}}")
    lines.append("")

    if not words:
        # Chord-only: group roughly by 4 beats / consecutive spans.
        buf = []
        for chord in chords:
            label = display_label(chord, mode=mode)
            if label == "N":
                continue
            buf.append(f"[{label}]")
            if len(buf) >= 4:
                lines.append(" ".join(buf))
                buf = []
        if buf:
            lines.append(" ".join(buf))
        return "\n".join(lines).rstrip() + "\n"

    # With lyrics: insert chord brackets at word starts when a chord onset lands in the word.
    chord_i = 0
    line_words = []
    line_start = None
    for word in words:
        if not isinstance(word, dict):
            continue
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        w_start = float(word.get("start") or 0)
        w_end = float(word.get("end") or w_start)
        if line_start is None:
            line_start = w_start
        inserts = []
        while chord_i < len(chords):
            chord = chords[chord_i]
            c_start = float(chord.get("start") or 0)
            if c_start > w_end + 0.05:
                break
            if c_start >= w_start - 0.05:
                label = display_label(chord, mode=mode)
                if label != "N":
                    inserts.append((max(0.0, c_start - w_start), label))
            chord_i += 1
        if inserts:
            # Place first chord at start of word; extras mid-word by fraction of length.
            length = max(len(text), 1)
            pieces = []
            cursor = 0
            for offset, label in inserts:
                frac = 0 if offset <= 0 else min(0.9, offset / max(w_end - w_start, 0.01))
                cut = max(cursor, int(round(frac * length)))
                pieces.append(text[cursor:cut])
                pieces.append(f"[{label}]")
                cursor = cut
            pieces.append(text[cursor:])
            line_words.append("".join(pieces))
        else:
            line_words.append(text)
        # Break lines roughly every ~8 words or on punctuation.
        if len(line_words) >= 8 or text.endswith((".", "?", "!")):
            lines.append(" ".join(line_words))
            line_words = []
            line_start = None
    if line_words:
        lines.append(" ".join(line_words))

    # Trailing chord-only gaps after last word
    trailing = []
    while chord_i < len(chords):
        label = display_label(chords[chord_i], mode=mode)
        chord_i += 1
        if label != "N":
            trailing.append(f"[{label}]")
            if len(trailing) >= 4:
                lines.append(" ".join(trailing))
                trailing = []
    if trailing:
        lines.append(" ".join(trailing))
    return "\n".join(lines).rstrip() + "\n"


def write_chordpro_export(project: Path, data: dict, *, title: str = "", artist: str = "",
                          mode: str = "simple") -> Path:
    exports = project / "exports"
    exports.mkdir(exist_ok=True)
    text = to_chordpro(data, title=title, artist=artist, mode=mode)
    path = exports / "chords.chopro"
    path.write_text(text, encoding="utf-8")
    return path
