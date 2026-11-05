"""Portable song projects: an original recording, optional metadata, and stems."""

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import hashlib

from ..tools import MISSING_FFMPEG

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".opus", ".ogg", ".m4a", ".aac", ".aiff", ".aif"}
METADATA_FIELDS = ("title", "artist", "album", "year")


def metadata_fields(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Metadata must be a JSON object.")
    fields = {}
    for key in METADATA_FIELDS:
        value = data.get(key, "")
        if not isinstance(value, (str, int)) or len(str(value)) > 500:
            raise ValueError(f"Invalid metadata field: {key}")
        fields[key] = str(value).strip()
    return fields


def atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise ValueError("Metadata symlinks are not supported.")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".metadata-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_metadata(project: Path) -> dict:
    path = project / "metadata.json"
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 1_000_000:
        raise ValueError("metadata.json must be a regular JSON file under 1 MB.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("metadata.json must contain a JSON object.")
    return data


def valid_trim(value):
    if not isinstance(value, dict):
        return None
    start, end = value.get("start"), value.get("end")
    if (not isinstance(start, (int, float)) or isinstance(start, bool)
            or not isinstance(end, (int, float)) or isinstance(end, bool)
            or not math.isfinite(start) or not math.isfinite(end)):
        return None
    start, end = float(start), float(end)
    if start < 0 or end <= start:
        return None
    return {"start": start, "end": end}


STEM_EXTENSIONS = (".flac", ".wav")  # FLAC since 1.2.1; older projects keep WAV stems


def stem_file(stems_dir: Path, stem: str) -> str | None:
    """File name of a stem in either storage format, preferring FLAC; None if absent."""
    for extension in STEM_EXTENSIONS:
        path = stems_dir / f"{stem}{extension}"
        if path.is_file() and not path.is_symlink():
            return path.name
    return None


def is_guitar_split_stem(name: str) -> bool:
    return name in {"lead", "rhythm", "guitar-target", "guitar-remainder"} or name.startswith("guitar-target-")


def project_path(root: Path, identifier: str) -> Path:
    if not identifier or identifier.startswith(".") or Path(identifier).name != identifier or "\\" in identifier:
        raise ValueError("Invalid project identifier.")
    path = root / identifier
    if path.is_symlink() or not path.is_dir() or path.resolve().parent != root.resolve():
        raise ValueError("Project not found in the selected folder.")
    return path


def describe_project(path: Path) -> dict:
    warnings = []
    try:
        data = read_metadata(path)
        fields = metadata_fields(data)
    except (ValueError, OSError) as error:
        data, fields = {}, dict.fromkeys(METADATA_FIELDS, "")
        warnings.append(f"Metadata: {error}")
    originals = sorted(p.name for p in path.iterdir()
                       if p.is_file() and not p.is_symlink() and p.suffix.lower() in AUDIO_EXTENSIONS)
    requested = data.get("original_file")
    original = requested if isinstance(requested, str) and requested in originals else None
    if original is None and len(originals) == 1:
        original = originals[0]
    elif original is None and len(originals) > 1:
        warnings.append("Multiple original recordings: set original_file in metadata.json to choose one.")
    if not original:
        warnings.append("No unambiguous original recording in this project.")
    stems_dir = path / "stems"
    stems = []
    separation = {}
    settings = stems_dir / "separation.json"
    if not stems_dir.is_symlink() and settings.is_file() and not settings.is_symlink():
        try:
            if settings.stat().st_size > 1000000:
                raise ValueError("Separation settings are too large.")
            separation = json.loads(settings.read_text(encoding="utf-8"))
            if not isinstance(separation, dict):
                raise ValueError("Invalid separation settings.")
            saved = separation.get("guitar_split") or []
            saved = [saved] if isinstance(saved, dict) else saved
            if not isinstance(saved, list) or any(not isinstance(item, dict) or not isinstance(item.get("title"), str) for item in saved):
                raise ValueError("Invalid saved track labels.")
            separation["guitar_split"] = saved
        except (ValueError, OSError):
            separation = {}
            warnings.append("Could not read separation settings.")
    trim_warning = False
    separation_trim = None
    if "trim" in separation:
        separation_trim = valid_trim(separation.get("trim"))
        if separation_trim is None:
            separation.pop("trim", None)
            trim_warning = True
    split_trim = None
    lead_rhythm = separation.get("lead_rhythm_split")
    if isinstance(lead_rhythm, dict) and "trim" in lead_rhythm:
        split_trim = valid_trim(lead_rhythm.get("trim"))
        if split_trim is None:
            lead_rhythm.pop("trim", None)
            trim_warning = True
    if trim_warning:
        warnings.append("Separation settings: invalid trim range.")
    split = separation.get("guitar_split")
    labels = {f"guitar-target-{i}": item["title"] for i, item in enumerate(split or [], 1)}
    if split:
        labels.update({"guitar-target": split[0]["title"], "guitar-remainder": "Guitar remainder"})
    if stems_dir.is_dir() and not stems_dir.is_symlink():
        order = {name: i for i, name in enumerate(("vocals", "guitar", "bass", "drums", "piano", "other", "instrumental", "lead", "rhythm"))}
        order.update({f"guitar-target-{i}": 1 + i / (len(split) + 2) for i in range(1, len(split or []) + 1)})
        order["guitar-remainder"] = 1.99
        for p in sorted(stems_dir.iterdir(), key=lambda p: (order.get(p.stem, 99), p.name)):
            stem_name = p.stem
            ext = p.suffix.lower()
            if ext not in (".wav", ".flac") or not p.is_file() or p.is_symlink():
                continue
            # Skip guitar.wav/guitar.flac when superseded by lead+rhythm or by split targets.
            if stem_name == 'guitar':
                superseded = False
                if separation.get('lead_rhythm_split'):
                    lead_exists = any((stems_dir / f"lead{e}").is_file() for e in (".wav", ".flac"))
                    rhythm_exists = any((stems_dir / f"rhythm{e}").is_file() for e in (".wav", ".flac"))
                    if lead_exists and rhythm_exists:
                        superseded = True
                if not superseded and split:
                    target_exists = any((stems_dir / f"guitar-target-{i}{e}").is_file()
                                        for i in range(1, len(split) + 1) for e in (".wav", ".flac"))
                    if target_exists:
                        superseded = True
                if superseded:
                    continue
            trim = split_trim if is_guitar_split_stem(stem_name) else separation_trim
            name = labels.get(stem_name, stem_name)
            stems.append({"name": name[:1].upper() + name[1:], "file": p.name, "bytes": p.stat().st_size,
                          "version": p.stat().st_mtime_ns,
                          "offset": float(trim["start"]) if trim else 0.0})
    cover = cover_path(path, data)
    # Lets the player size the loading bar before any download starts.
    original_bytes = (path / original).stat().st_size if original else 0
    chords = None
    try:
        from ..audio.chords import read_chords
        chords = read_chords(path)
    except (ValueError, OSError) as error:
        warnings.append(f"Chords: {error}")
    return {"id": path.name, **fields, "cover": cover.name if cover else None,
            "title": fields["title"] or (Path(original).stem if original else path.name),
            "original": original, "original_bytes": original_bytes, "stems": stems,
            "separation": separation, "chords": chords, "warnings": warnings}


def list_projects(root: Path) -> list[dict]:
    results = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_dir() and not path.is_symlink() and not path.name.startswith("."):
            try:
                results.append(describe_project(path))
            except OSError:
                continue
    return results


def probe_audio(path: Path) -> None:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                                 "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                                capture_output=True, text=True, timeout=30, check=True)
    except FileNotFoundError:
        raise ValueError(MISSING_FFMPEG) from None
    except subprocess.CalledProcessError:
        raise ValueError("FFmpeg could not read this file. Is it a valid audio file?") from None
    if not json.loads(result.stdout).get("streams"):
        raise ValueError("The imported file does not contain a readable audio stream.")


def import_project(root: Path, source: Path, filename: str, fields: dict, enrichment=None, origin=None) -> dict:
    if Path(filename).name != filename or "\\" in filename or filename.startswith("."):
        raise ValueError("Invalid audio filename.")
    if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("Unsupported audio extension.")
    fields = metadata_fields(fields)
    probe_audio(source)
    title = fields["title"] or Path(filename).stem
    # Uploads are stored under the audio file's own name: <output>/<file name>/.
    label = Path(filename).stem if origin is None else " - ".join(part for part in (fields["artist"], title) if part)
    slug = re.sub(r'[^\w .()-]+', "-", label, flags=re.UNICODE).strip(" .-")[:120] or "Untitled"
    with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temporary:
        work = Path(temporary)
        shutil.copy2(source, work / filename)
        metadata = {"schema_version": 1, **fields, "title": title, "original_file": filename}
        if origin:
            metadata['source'] = origin
        apply_enrichment(work, metadata, enrichment)
        atomic_json(work / "metadata.json", metadata)
        # Reserve a destination without overwriting an existing project.
        index = 1
        while True:
            destination = root / (slug if index == 1 else f"{slug} ({index})")
            try:
                destination.mkdir()
                break
            except FileExistsError:
                index += 1
        try:
            for child in work.iterdir():
                shutil.move(str(child), destination / child.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return describe_project(destination)


def cover_path(project: Path, metadata=None):
    data = read_metadata(project) if metadata is None else metadata
    name = data.get('cover_file')
    if not isinstance(name, str) or Path(name).name != name or '\\' in name:
        return None
    path = project / name
    if path.suffix.lower() not in {'.jpg', '.png'} or path.is_symlink() or not path.is_file():
        return None
    return path if path.stat().st_size <= 5_000_000 else None


def apply_enrichment(project, data, enrichment):
    if enrichment is None:
        return
    if not enrichment.get('prepared'):
        raise ValueError('Select a metadata match before saving.')
    data['metadata_source'] = {key: enrichment.get(key) for key in ('release_id', 'group_id', 'recording_id')}
    data['metadata_source']['provider'] = 'MusicBrainz'
    data.pop('cover_file', None)
    cover = enrichment.get('cover')
    if cover:
        extension = '.png' if enrichment['cover_type'] == 'image/png' else '.jpg'
        name = 'cover-' + hashlib.sha256(cover).hexdigest()[:20] + extension
        destination = project/name
        if destination.is_symlink():
            raise ValueError('Cover symlinks are not supported.')
        with tempfile.NamedTemporaryFile(dir=project, prefix='.cover-', delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(cover)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        data['cover_file'] = name


def save_metadata(project: Path, fields: dict, enrichment=None) -> dict:
    data = read_metadata(project)  # Don't silently discard malformed or custom data.
    data.update(metadata_fields(fields))
    apply_enrichment(project, data, enrichment)
    atomic_json(project / "metadata.json", data)
    return describe_project(project)
