"""Per-project API: separate, export, metadata, audio streaming, reveal."""

import mimetypes
import os
import shutil
import subprocess
import sys

from ...projects import cover_path, describe_project, project_path, save_metadata


def register(router, library):
    @router.post("/api/projects/{id}/metadata")
    def metadata(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            root = library.require_matching_root(data.get("root"))
            project = project_path(root, id)
            enrichment = library.lookup.get(data["lookup_token"]) if data.get("lookup_token") else None
            return save_metadata(project, data, enrichment)

    @router.get("/api/projects/{id}/cover")
    def cover(req, query, id, **_):
        with library.lock:
            root = library.require_matching_root(query.get("root"))
            project = project_path(root, id)
            cover = cover_path(project)
            if not cover:
                raise ValueError("Cover not found.")
            body = cover.read_bytes()
            content_type = "image/png" if cover.suffix == ".png" else "image/jpeg"
        req.respond(200, body, content_type)
        return None

    @router.post("/api/projects/{id}/separate")
    def separate(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            library.require_matching_root(data.get("root"))
            return library.start(id, data.get("preset", "vocals"), data.get("stems"),
                                 group=data.get("group"),
                                 split_leaves=bool(data.get("split")),
                                 trim=data.get("trim"))

    @router.post("/api/projects/{id}/split-guitar")
    def split_guitar(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            library.require_matching_root(data.get("root"))
            return library.start(id, "guitar", guitar_split=True, trim=data.get("trim"))

    @router.post("/api/projects/{id}/export")
    def export(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            library.require_matching_root(data.get("root"))
            return library.start_export(id, data.get("formats"), data.get("stems"),
                                        prevent_clipping=data.get("prevent_clipping", True))

    @router.post("/api/projects/{id}/analyze-chords")
    def analyze_chords(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            library.require_matching_root(data.get("root"))
            return library.start_chords(id, lyrics=bool(data.get("lyrics")))

    @router.post("/api/projects/{id}/chords")
    def update_chords(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            library.require_matching_root(data.get("root"))
            if "overrides" in data:
                return library.update_chord_overrides(id, data.get("overrides"))
            if "ui" in data:
                return library.update_chord_ui(id, data.get("ui") or {})
            raise ValueError("Provide overrides or ui.")

    @router.post("/api/projects/{id}/export-chordpro")
    def export_chordpro(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            library.require_matching_root(data.get("root"))
            return library.export_chordpro(id, mode=data.get("mode") or "simple")

    @router.post("/api/projects/{id}/reveal")
    def reveal(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            root = library.require_matching_root(data.get("root"))
            project = project_path(root, id)
            name = data.get("target", "project")
            targets = {"project": project, "stems": project / "stems",
                       "exports": project / "exports"}
            if name not in targets:
                raise ValueError("Unknown folder target.")
            target = targets[name]
            if name in {"stems", "exports"} and (target.is_symlink() or not target.is_dir()):
                raise ValueError("Folder not found.")
            if os.name == "nt":
                os.startfile(target)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])
            return {"ok": True}

    @router.post("/api/projects/{id}/delete")
    def delete(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            return library.delete(id, data.get("root"))

    @router.post("/api/projects/{id}/clear-exports")
    def clear_exports(req, query, id, **_):
        with library.lock:
            data = req.json_body()
            return library.clear_exports(id, data.get("root"))

    @router.get("/api/projects/{id}/audio/{file}")
    def audio(req, query, id, file, **_):
        with library.lock:
            root = library.require_matching_root(query.get("root"))
            project = project_path(root, id)
            name = file
            allowed = {s["file"] for s in describe_project(project)["stems"]}
            if name not in allowed:
                raise ValueError("Stem not found.")
            path = project / "stems" / name
            content_type = "audio/flac" if name.endswith(".flac") else "audio/wav"
            # Open while holding the lock, before a completed job swaps directories.
            handle = path.open("rb")
        _stream_file(req, handle, content_type)
        return None

    @router.get("/api/projects/{id}/original")
    def original(req, query, id, **_):
        with library.lock:
            root = library.require_matching_root(query.get("root"))
            project = project_path(root, id)
            # The original file never moves once imported (only stems/ gets
            # swapped by a job), but opening it here too keeps one lock-then-
            # stream pattern for every audio route rather than two slightly
            # different ones.
            info = describe_project(project)
            if not info["original"]:
                raise ValueError("This project has no original recording.")
            path = project / info["original"]
            content_type = mimetypes.guess_type(info["original"])[0] or "application/octet-stream"
            handle = path.open("rb")
        _stream_file(req, handle, content_type)
        return None


def _stream_file(req, handle, content_type: str):
    with handle:
        req.send_response(200)
        req.send_header("Content-Type", content_type)
        req.send_header("Content-Length", str(os.fstat(handle.fileno()).st_size))
        req.send_header("Cache-Control", "no-store")
        req.send_header("X-Content-Type-Options", "nosniff")
        req.end_headers()
        shutil.copyfileobj(handle, req.wfile)
