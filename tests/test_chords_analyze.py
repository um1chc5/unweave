"""Lightweight chord analysis helpers (no heavy model downloads)."""

import tempfile
import unittest
from pathlib import Path

from unweave.audio.chords import (
    SCHEMA, _clamp_beats_per_bar, _meter_from_downbeats, _normalize_label,
    _snap_to_beats, invalidate_chords, read_chords,
)
from unweave.projects.store import atomic_json


class ChordHelperTests(unittest.TestCase):
    def test_normalize_labels(self):
        self.assertEqual(_normalize_label("D:maj"), ("D", "D"))
        self.assertEqual(_normalize_label("A:min"), ("Am", "Am"))
        self.assertEqual(_normalize_label("N"), ("N", "N"))

    def test_meter_helpers(self):
        self.assertEqual(_clamp_beats_per_bar(3), 3)
        self.assertEqual(_clamp_beats_per_bar(4), 4)
        self.assertEqual(_clamp_beats_per_bar(6), 6)
        self.assertEqual(_clamp_beats_per_bar(5), 4)
        # 4/4: downbeats every 4 beats at 0.5s
        beats = [i * 0.5 for i in range(16)]
        downs = [i * 2.0 for i in range(4)]
        bpp, phase = _meter_from_downbeats(beats, downs)
        self.assertEqual(bpp, 4)
        self.assertEqual(phase, 0)
        # 3/4
        downs3 = [i * 1.5 for i in range(4)]
        bpp3, _ = _meter_from_downbeats(beats, downs3)
        self.assertEqual(bpp3, 3)

    def test_snap_merge(self):
        beats = [{"t": 0.0, "beat": 1, "downbeat": True},
                 {"t": 1.0, "beat": 2, "downbeat": False},
                 {"t": 2.0, "beat": 3, "downbeat": False},
                 {"t": 3.0, "beat": 4, "downbeat": False}]
        # Flickery frame labels collapse to one held C across beats.
        rows = _snap_to_beats([
            (0.0, 0.4, "C:maj"), (0.4, 0.9, "C:maj"),
            (0.9, 1.5, "C:maj"), (1.5, 2.5, "C:maj"),
            (2.5, 3.5, "G:maj"),
        ], beats)
        self.assertEqual([r["simple"] for r in rows], ["C", "G"])
        self.assertLessEqual(len(rows), 2)

    def test_invalidate_and_stale_mtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "Song"
            stems = project / "stems"
            analysis = project / "analysis"
            stems.mkdir(parents=True)
            analysis.mkdir()
            (stems / "other.wav").write_bytes(b"RIFF....")
            data = {
                "schema": SCHEMA,
                "created_at": 1,
                "stale": False,
                "source": {"mtimes": {"other.wav": 1}, "trim": None},
                "key": {"name": "C", "confidence": 0.5},
                "beats": [],
                "chords": [],
                "words": [],
                "ui": {},
            }
            atomic_json(analysis / "chords.json", data)
            loaded = read_chords(project)
            self.assertTrue(loaded["stale"])
            invalidate_chords(project)
            self.assertIsNone(read_chords(project))


if __name__ == "__main__":
    unittest.main()
