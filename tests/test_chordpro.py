"""ChordPro export and chord override helpers."""

import json
import tempfile
import unittest
from pathlib import Path

from unweave.audio.chordpro import display_label, to_chordpro
from unweave.audio.chords import save_chord_overrides, SCHEMA
from unweave.projects.store import atomic_json


def sample_analysis(**extra):
    data = {
        "schema": SCHEMA,
        "created_at": 1,
        "stale": False,
        "key": {"name": "F", "confidence": 0.8},
        "beats": [{"t": 0.0, "beat": 1, "downbeat": True}],
        "chords": [
            {"start": 0.0, "end": 2.0, "label": "F", "simple": "F", "extended": "Fmaj7",
             "confidence": 0.9, "alternatives": ["F", "Fmaj7"]},
            {"start": 2.0, "end": 4.0, "label": "Em", "simple": "Em", "extended": "Em7",
             "confidence": 0.5, "alternatives": ["Em", "Em7"]},
        ],
        "words": [],
        "ui": {"mode": "simple", "lyrics_mode": "chords_only"},
    }
    data.update(extra)
    return data


class ChordProTests(unittest.TestCase):
    def test_chord_only_export(self):
        text = to_chordpro(sample_analysis(), title="Yesterday", artist="Beatles", mode="simple")
        self.assertIn("{title: Yesterday}", text)
        self.assertIn("{key: F}", text)
        self.assertIn("[F]", text)
        self.assertIn("[Em]", text)

    def test_extended_mode_and_override(self):
        data = sample_analysis()
        data["chords"][0]["override"] = "Bb"
        self.assertEqual(display_label(data["chords"][0], mode="extended"), "Bb")
        text = to_chordpro(data, mode="extended")
        self.assertIn("[Bb]", text)
        self.assertIn("[Em7]", text)

    def test_lyrics_inline_chords(self):
        data = sample_analysis(words=[
            {"start": 0.0, "end": 0.5, "text": "Yesterday"},
            {"start": 2.0, "end": 2.4, "text": "all"},
        ])
        text = to_chordpro(data, mode="simple")
        self.assertIn("[F]", text)
        self.assertIn("Yesterday", text)

    def test_overrides_persist(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "Song"
            project.mkdir()
            analysis = project / "analysis"
            analysis.mkdir()
            atomic_json(analysis / "chords.json", sample_analysis())
            save_chord_overrides(project, [{"index": 1, "override": "A7"}])
            saved = json.loads((analysis / "chords.json").read_text())
            self.assertEqual(saved["chords"][1]["override"], "A7")
            save_chord_overrides(project, [{"index": 1, "override": None}])
            saved = json.loads((analysis / "chords.json").read_text())
            self.assertNotIn("override", saved["chords"][1])


if __name__ == "__main__":
    unittest.main()
