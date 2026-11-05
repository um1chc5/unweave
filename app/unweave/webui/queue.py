"""Job queue, workers, cancel, and per-job transcripts."""

from collections import deque
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from ..projects import describe_project, import_project, metadata_fields, project_path, stem_file
from ..audio.sources import youtube_url, youtube_command
from ..audio.pipeline import probe_duration, parse_trim
from ..audio.exports import FORMATS, export_stems
from ..audio.chords import (analyze_project, chords_available, invalidate_chords,
                            read_chords, save_chord_overrides, save_ui_prefs)
from ..audio.chordpro import to_chordpro, write_chordpro_export
from ..tools import (MISSING_FFMPEG, PCM_BYTES_PER_SECOND, app_version, cpu_threads, ensure_ffmpeg_on_path,
                     env_first, require_free_space, total_ram_gb)
from ..processes import popen, stop as stop_process
from ..setup_env import (FFMPEG_SIZE_MB, RUNTIME_SIZE_MB, SEPARATION_SIZE_MB, VCRUNTIME_SIZE_MB,
                         YOUTUBE_SIZE_MB, ffmpeg_ok, runtime_ok, separation_ok, vcruntime_ok,
                         youtube_ok, run_setup, torch_has_cuda)
from ..fsutil import dir_bytes, is_link_like
from .plans import plan_stems, selection_stems, validate_group
from .progress import YTDLP_PROGRESS, clock_seconds, track_progress

APP_VERSION = app_version()
# Export output size relative to the stored FLAC stem, per format (rounded up).
EXPORT_SIZE_FACTOR = {"flac": 1.0, "wav16": 2.0, "aiff": 2.0, "mp3": 0.3, "opus": 0.2, "m4a": 0.25}
# How many per-job log files to keep on disk.
JOB_LOG_KEEP = 40
# Windows STATUS_ACCESS_VIOLATION (0xC0000005) as unsigned wait status / signed int.
_WIN_ACCESS_VIOLATION = {0xC0000005, 3221225477, -1073741819}


def cli_failure_message(kind: str, code: int) -> str:
    """Human-readable subprocess failure; notes native Windows crashes when applicable."""
    if code in _WIN_ACCESS_VIOLATION:
        return (f"{kind} crashed (Windows access violation, code {code}). "
                "Most often an outdated Microsoft Visual C++ runtime — install the latest "
                "(aka.ms/vs/17/release/vc_redist.x64.exe) and restart Unweave. If it still "
                "crashes, try Settings → Components → Repair, then Storage → Clear models.")
    return f"{kind} exited with code {code}. See the process log."

class JobLog:
    """A per-job transcript on disk, so a run can be diagnosed after the fact.

    The UI's in-memory log is trimmed to 160 lines, drops the progress lines that
    drive the bar, and dies with the server process (a cancelled or crashed job
    leaves nothing behind). This file keeps every raw CLI line plus the command
    that produced it.
    """

    def __init__(self, directory: Path, job: dict, command):
        self.path = None
        self.handle = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(job.get("started") or time.time()))
            safe = re.sub(r"[^\w.-]+", "-", f"{stamp}-{job['kind']}-{job['id']}").strip("-")
            self.path = directory / f"{safe}.log"
            self.handle = self.path.open("w", encoding="utf-8", errors="replace", buffering=1)
            self.write(f"# job      {job['id']} ({job['kind']})")
            if job.get("project"):
                self.write(f"# project  {job['project']}")
            self.write(f"# started  {stamp}  app {APP_VERSION}")
            self.write("# command  " + " ".join(str(part) for part in command))
            self.write("")
            job["log"] = str(self.path)
        except OSError:
            self.handle = None

    def write(self, line: str):
        if self.handle is None:
            return
        try:
            stamp = time.strftime("%H:%M:%S")
            self.handle.write(f"{stamp} {line}\n")
        except OSError:
            self.handle = None

    def close(self, status: str, error=None):
        if self.handle is None:
            return
        try:
            self.write("")
            self.write(f"# status   {status}" + (f": {error}" if error else ""))
            self.handle.close()
        except OSError:
            pass
        finally:
            self.handle = None


def prune_job_logs(directory: Path, keep: int = JOB_LOG_KEEP):
    """Delete all but the newest `keep` job logs. Never raises."""
    try:
        logs = sorted(directory.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
        for stale in logs[keep:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass



def public_job(job: dict) -> dict:
    """A job record for the API: worker-only "_" fields hold Paths, which JSON can't encode."""
    return {key: value for key, value in job.items() if not key.startswith("_")}


class JobQueueMixin:
    """Enqueue, drain, run, and cancel jobs. Mixed into Library."""

    def _derive_max_concurrent_jobs(self) -> int:
        """Derive suggested default concurrency cap.

        - 1 on CPU-only machines
        - 2-3 when torch.cuda.is_available()
        - Never more than max(1, available_ram_gb // 3)
        Override with UNWEAVE_MAX_CONCURRENT_JOBS (or legacy SOUND_RAZOR_MAX_CONCURRENT_JOBS).
        """
        override = env_first("UNWEAVE_MAX_CONCURRENT_JOBS", "SOUND_RAZOR_MAX_CONCURRENT_JOBS")
        if override:
            try:
                return max(1, int(override))
            except ValueError:
                pass
        cap = 3 if torch_has_cuda() else 1
        ram_cap = max(1, int(total_ram_gb() // 3))
        return min(cap, ram_cap)

    def running(self):
        return bool(self.active)

    def _active_jobs(self) -> list[dict]:
        """Return the job records for all active job ids."""
        return [self.active_jobs[jid] for jid in self.active if jid in self.active_jobs]

    def _job_logs(self, job_id: str) -> deque:
        return self.job_logs.setdefault(job_id, deque(maxlen=160))

    def _track_progress(self, job: dict, line: str) -> bool:
        """Compatibility wrapper; progress parsing lives in `progress.py`."""
        return track_progress(job, line)

    def _enqueue(self, job: dict):
        """Append a queued job and start the drainer thread if it is not running."""
        self.queue.append(job)
        self._ensure_drainer()
        return public_job(job)

    def _ensure_drainer(self):
        if self._drainer is None or not self._drainer.is_alive():
            self._drainer = threading.Thread(target=self._drain, daemon=True)
            self._drainer.start()

    def _drain(self):
        """Start worker threads for queued jobs up to max_concurrent_jobs.

        Exits when the queue is empty and no active jobs remain.
        """
        while True:
            with self.lock:
                if not self.queue and not self.active:
                    self._drainer = None
                    self.process = None
                    self.logs = deque(maxlen=160)
                    return
                started = []
                while len(self.active) < self.max_concurrent_jobs:
                    job = self._next_startable()
                    if job is None:
                        break
                    self.queue.remove(job)
                    self.active.add(job["id"])
                    self.active_jobs[job["id"]] = job
                    job["status"] = "running"
                    job["started"] = time.time()
                    self.job_logs.setdefault(job["id"], deque(maxlen=160))
                    # Backward-compat shims point to the latest started job.
                    self.job = job
                    self.logs = self._job_logs(job["id"])
                    started.append(job)
            if not started:
                # All cap slots filled; wait for a worker to signal completion.
                self._job_done.wait(timeout=1.0)
                self._job_done.clear()
                continue
            for job in started:
                thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
                thread.start()

    def _next_startable(self):
        """First queued job that may start now, keeping FIFO order where it matters.

        Setup runs alone: it reinstalls packages the other jobs import, and
        setup_env reports through the single `self.job`/`self.logs`/`self.process`
        aliases. Two jobs on one project never overlap, because each one swaps
        the project's whole stems folder when it publishes.
        """
        active = self._active_jobs()
        if any(job["kind"] == "setup" for job in active):
            return None
        busy = {job.get("project") for job in active if job.get("project")}
        for job in self.queue:
            if job["kind"] == "setup":
                # Wait for running jobs to drain; nothing queued after setup jumps it.
                return None if active else job
            if job.get("project") and job["project"] in busy:
                continue
            return job
        return None

    def _finish(self, job: dict):
        """Mark a job finished once. Caller holds the lock. Idempotent."""
        if job.get("finished") is not None:
            return
        if job["status"] == "cancelling":
            job["status"] = "cancelled"
        job["finished"] = time.time()
        self.processes.pop(job["id"], None)
        self.finished_jobs.append(job)
        prune_job_logs(self.log_dir)
        # Point the UI's single-job shim at the finished job unless another is still running,
        # so its success/failure notice still surfaces.
        others = [other for other in self._active_jobs() if other is not job]
        if not others:
            self.job = job

    def _launch(self, job: dict, command, **options):
        """Start a job's subprocess unless it is being cancelled. Caller holds the lock."""
        if job["status"] == "cancelling":
            return None
        process = popen(command, **options)
        self.processes[job["id"]] = process
        self.process = process
        return process

    def _run_job(self, job: dict):
        """Run a single job's worker, then finish and clean up (single _finish path)."""
        job_id = job["id"]
        kind = job["kind"]
        try:
            if kind == "separate":
                self._separate(job, job["_project"], job["_original"], job["_stems"],
                               job.get("_group"), job.get("_split_leaves", False), job["_trim"])
            elif kind == "split-guitar":
                self._separate(job, job["_project"], job["_original"], job["_stems"],
                               None, False, job["_trim"])
            elif kind == "youtube":
                self._youtube(job, job["_root"], job["_url"], job["_fields"])
            elif kind == "setup":
                self._setup(job, job["_stages"])
            elif kind == "export":
                self._export(job, job["_project"], job["_formats"], job["_stems"],
                             job["_prevent_clipping"])
            elif kind == "chords":
                self._chords(job, job["_project"], job.get("_lyrics", False))
        finally:
            with self.lock:
                self._finish(job)
                self.active.discard(job_id)
                self.active_jobs.pop(job_id, None)
                if not self.active:
                    self.process = None
            self._job_done.set()

    def start(self, identifier: str, preset: str, stems=None, *, group=None,
              split_leaves=False, guitar_split=False, trim=None):
        with self.lock:
            chosen_group = validate_group(group)
            chosen = ['lead', 'rhythm'] if guitar_split else plan_stems(preset, stems, chosen_group)
            project = project_path(self.require_root(), identifier)
            info = describe_project(project)
            if not guitar_split and not info["original"]:
                raise ValueError("This project needs an unambiguous original audio file.")
            if (project / "stems").is_symlink():
                raise ValueError("The stems folder must not be a symlink.")
            if split_leaves and guitar_split:
                raise ValueError("A split-only job cannot also split the guitar.")
            return self._enqueue_separation(identifier, preset, chosen, chosen_group,
                                            split_leaves, guitar_split, info, project, trim)

    def _start_trim(self, project, info, guitar_split, trim):
        if trim is None:
            return None
        if guitar_split:
            guitar = stem_file(project / 'stems', 'guitar')
            return parse_trim(trim, probe_duration(project / 'stems' / guitar), 2.0)
        return parse_trim(trim, probe_duration(project / info["original"]), 1.0)

    def _validate_split_job(self, project, info):
        guitar = stem_file(project / 'stems', 'guitar')
        if guitar not in {item['file'] for item in info['stems']}:
            raise ValueError('Separate the guitar track first.')
        if any(p.is_symlink() or not p.is_file() for p in (project / 'stems').iterdir()):
            raise ValueError('The stems folder must contain regular files only.')
        if any((project / 'stems' / f'{name}{ext}').exists()
               for name in ('lead', 'rhythm') for ext in ('.flac', '.wav')):
            raise ValueError('This project already contains lead or rhythm tracks.')
        return project / 'stems' / guitar

    def _enqueue_separation(self, identifier, preset, chosen, chosen_group, split_leaves,
                            guitar_split, info, project, trim):
        if guitar_split:
            self._validate_split_job(project, info)
        saved_trim = self._start_trim(project, info, guitar_split, trim)
        disk_need = self._separation_disk_need(project, info, guitar_split, split_leaves, saved_trim)
        require_free_space(project, disk_need)
        if not guitar_split:
            # The CLI normalizes the input to a 16-bit WAV in the system temp folder.
            require_free_space(Path(tempfile.gettempdir()), self._seconds(project, info, False, saved_trim)
                               * PCM_BYTES_PER_SECOND)
        job = {"id": uuid.uuid4().hex, "kind": "split-guitar" if guitar_split else "separate",
               "project": identifier, "status": "queued",
               "started": time.time(), "finished": None, "error": None,
               "preset": preset, "stems": chosen, "group": chosen_group or [],
               "guitar_split": guitar_split, "split_leaves": bool(split_leaves),
               "trim": {"start": saved_trim[0], "end": saved_trim[1]} if saved_trim else None,
               "phase": "prepare", "progress": 0, "step_progress": 0, "eta": None,
               "step_eta": None, "step_eta_at": None, "eta_estimated": False,
               "pass_count": 2 if split_leaves else 1, "pass_index": 1,
               "_project": project, "_original": info["original"],
               "_stems": chosen, "_group": chosen_group,
               "_split_leaves": bool(split_leaves), "_trim": saved_trim, "_disk_need": disk_need}
        return self._enqueue(job)

    def _seconds(self, project, info, guitar_split, trim) -> float:
        if trim:
            return trim[1] - trim[0]
        source = project / 'stems' / stem_file(project / 'stems', 'guitar') if guitar_split else project / info["original"]
        return probe_duration(source)

    def _separation_disk_need(self, project, info, guitar_split, split_leaves, trim) -> float:
        """Bytes the project's drive must hold for this run, sized by 16-bit PCM.

        The model writes all six stems even when the plan keeps fewer, and the
        24-bit lead/rhythm pair counts as three. A split-only job also copies the
        current stems folder into its stage."""
        pcm = self._seconds(project, info, guitar_split, trim) * PCM_BYTES_PER_SECOND
        if guitar_split:
            return sum(item["bytes"] for item in info["stems"]) + pcm * 3
        return pcm * (6 + (3 if split_leaves else 0))

    def import_youtube(self, data):
        url = youtube_url(data.get('url'))
        fields = metadata_fields(data)
        with self.lock:
            root = self.require_root()
            if data.get('root') != str(root):
                raise ValueError('The projects folder changed. Reopen YouTube import.')
            if importlib.util.find_spec('yt_dlp') is None:
                raise ValueError('YouTube support is not installed. Click "Install YouTube support" above the link box.')
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                raise ValueError(MISSING_FFMPEG)
            job = {'id': uuid.uuid4().hex, 'kind': 'youtube', 'project': None,
                   'status': 'queued', 'started': time.time(), 'finished': None, 'error': None,
                   'phase': 'download', 'progress': 0, 'step_progress': 0, 'eta': None,
                   'step_eta': None, 'step_eta_at': None, 'eta_estimated': False,
                   'pass_count': 1, 'pass_index': 1,
                   '_root': root, '_url': url, '_fields': fields}
            return self._enqueue(job)

    def setup_status(self):
        stages = [
            {"id": "runtime", "label": "Python runtime & app packages", "size_mb": RUNTIME_SIZE_MB,
             "required": True, "missing": not runtime_ok(), "done": runtime_ok()},
            {"id": "ffmpeg", "label": "FFmpeg (audio conversion)", "size_mb": FFMPEG_SIZE_MB,
             "required": True, "missing": not ffmpeg_ok(), "done": ffmpeg_ok()},
            {"id": "separation", "label": "Separation engine (PyTorch, etc.)", "size_mb": SEPARATION_SIZE_MB,
             "required": True, "missing": not separation_ok(), "done": separation_ok()},
            {"id": "youtube", "label": "YouTube import support (yt-dlp)", "size_mb": YOUTUBE_SIZE_MB,
             "required": False, "missing": not youtube_ok(), "done": youtube_ok()},
        ]
        # A Windows prerequisite, not part of the app: shown only when it needs installing,
        # and kept for the rest of the setup job that installs it so the card doesn't vanish.
        vc_ok = vcruntime_ok()
        installing = any(job.get("kind") == "setup" and "vcruntime" in job.get("_stages", ())
                         and job.get("status") in {"queued", "running", "cancelling"}
                         for job in [*self.active_jobs.values(), *self.queue])
        if sys.platform == "win32" and (not vc_ok or installing):
            stages.insert(1, {"id": "vcruntime", "label": "Microsoft Visual C++ runtime",
                              "size_mb": VCRUNTIME_SIZE_MB, "required": True,
                              "missing": not vc_ok, "done": vc_ok})
        return {"required": any(s["required"] and s["missing"] for s in stages),
                "stages": stages,
                "total_mb": sum(s["size_mb"] for s in stages if s["missing"])}

    def start_setup(self, stages: list[str], *, repair: bool = False):
        with self.lock:
            allowed = {"vcruntime", "ffmpeg", "separation", "youtube"}
            stages = [stage for stage in stages if stage in allowed]
            if not stages:
                raise ValueError("Nothing to set up.")
            job = {"id": uuid.uuid4().hex, "kind": "setup", "project": None,
                   "status": "queued", "started": time.time(), "finished": None, "error": None,
                   "phase": "prepare", "progress": 0, "step_progress": 0, "eta": None,
                   "step_eta": None, "step_eta_at": None, "eta_estimated": False,
                   "pass_count": 1, "pass_index": 1,
                   "stages": stages, "stage": None, "repair": bool(repair),
                   "_stages": stages, "_repair": bool(repair)}
            return self._enqueue(job)

    def _setup(self, job, stages: list[str]):
        try:
            run_setup(self, stages, repair=bool(job.get("_repair")))
        except Exception as error:
            with self.lock:
                if job["status"] != "cancelling":
                    job.update(status="failed", error=str(error))

    def _youtube(self, job, root, url, fields):
        job_id = job["id"]
        try:
            with tempfile.TemporaryDirectory(prefix='.youtube-', dir=root) as temporary:
                work = Path(temporary)
                with self.lock:
                    command = youtube_command(url, work)
                    process = self._launch(job, command, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                        env=dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8'), start_new_session=True)
                    if process is None:
                        return
                transcript = JobLog(self.log_dir, job, command)
                try:
                    for line in process.stdout:
                        line = line.strip()
                        transcript.write(line)
                        with self.lock:
                            download = YTDLP_PROGRESS.search(line)
                            if download:
                                job['progress'] = max(job['progress'], float(download.group(1)))
                                job['step_progress'] = max(job.get('step_progress') or 0,
                                                            float(download.group(1)))
                                job['eta'] = clock_seconds(download.group(2)) if download.group(2) else None
                                job['eta_at'] = time.time()
                                job['step_eta'] = job['eta']
                                job['step_eta_at'] = job['eta_at']
                                job['eta_estimated'] = False
                            elif line:
                                self._job_logs(job_id).append(line[-2000:])
                    code = process.wait()
                finally:
                    transcript.close(job['status'], job.get('error'))
                    process.stdout.close()
                with self.lock:
                    if job['status'] == 'cancelling':
                        return
                    if code:
                        message = cli_failure_message('YouTube download', code)
                        self._job_logs(job_id).append(message)
                        raise ValueError(message)
                    # yt-dlp writes UTF-8; Windows' default cp1252 chokes on non-ASCII titles.
                    info = json.loads((work/'source.info.json').read_text(encoding='utf-8'))
                    defaults = {'title': info.get('track') or info.get('title') or 'YouTube recording',
                                'artist': info.get('artist') or info.get('creator') or '',
                                'album': info.get('album') or '',
                                'year': str(info.get('release_year') or info.get('release_date') or '')[:4]}
                    chosen = {key: fields[key] or str(defaults[key])[:500] for key in defaults}
                    result = import_project(root, work/'source.wav', 'original.wav', chosen,
                                            origin={'provider': 'YouTube', 'url': url})
                    job.update(status='succeeded', project=result['id'],
                               progress=100, step_progress=100, phase='done', eta=0)
        except Exception as error:
            with self.lock:
                if job['status'] != 'cancelling':
                    job.update(status='failed', error=str(error))

    def _separate(self, job, project: Path, original: str, stems: list[str],
                  group=None, split_leaves=False, trim=None):
        job_id = job["id"]
        try:
            # Checked again here: queued jobs ahead of this one may have used the space.
            require_free_space(project, job["_disk_need"])
            with tempfile.TemporaryDirectory(prefix=".separation-", dir=project) as temporary:
                stage = Path(temporary) / "stems"
                command = self._separation_command(job, project, original, stage,
                                                   stems, group, split_leaves, trim)
                # UTF-8 both ways, so non-ASCII song titles and paths survive the pipe on Windows.
                env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", ORT_DISABLE_TELEMETRY="1",
                           **self._thread_env())
                with self.lock:
                    process = self._launch(job, command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding="utf-8", errors="replace", env=env, start_new_session=True)
                    if process is None:
                        return
                job_logs = self._job_logs(job_id)
                transcript = JobLog(self.log_dir, job, command)
                try:
                    for line in process.stdout:
                        line = line.strip()
                        transcript.write(line)
                        with self.lock:
                            if line and not self._track_progress(job, line):
                                job_logs.append(line[-2000:])
                    code = process.wait()
                finally:
                    transcript.close(job["status"], job.get("error"))
                    process.stdout.close()
                with self.lock:
                    if job["status"] == "cancelling":
                        return
                if code != 0:
                    message = cli_failure_message("Separation CLI", code)
                    job_logs.append(message)
                    raise RuntimeError(message)
                self._expect_outputs(stage, stems, group)
                # Publish only a successful run; preserve the old stems on failure.
                with self.lock:
                    if job["status"] == "cancelling":
                        return
                    destination = project / "stems"
                    backup = Path(temporary) / "previous-stems"
                    if destination.exists():
                        destination.rename(backup)
                    try:
                        stage.rename(destination)
                    except BaseException:
                        if backup.exists():
                            backup.rename(destination)
                        raise
                    # Stem timeline changed — drop derived chord sheet.
                    invalidate_chords(project)
                    job.update(status="succeeded", progress=100, step_progress=100, phase="done", eta=0)
        except Exception as error:
            with self.lock:
                if job["status"] != "cancelling":
                    job.update(status="failed", error=str(error))

    def _thread_env(self) -> dict:
        """Share the inference cores between jobs that may run side by side.

        Each CLI otherwise takes cpu_count - 1 threads for itself, so two or three
        parallel jobs (CUDA builds only) would oversubscribe the CPU. Uses the cap,
        not the current count, so a job's share never depends on start order. A
        user's own UNWEAVE_THREADS always wins."""
        if self.max_concurrent_jobs <= 1 or env_first("UNWEAVE_THREADS", "SOUND_RAZOR_THREADS"):
            return {}
        return {"UNWEAVE_THREADS": str(max(1, cpu_threads() // self.max_concurrent_jobs))}

    def _separation_command(self, job, project: Path, original: str, stage: Path,
                            stems: list[str], group, split_leaves: bool, trim):
        range_args = ["--start", f"{trim[0]:.3f}", "--end", f"{trim[1]:.3f}"] if trim else []
        # A native crash (torch/onnxruntime access violation) otherwise kills the CLI with
        # no traceback; faulthandler prints the Python stack into the job log first.
        python = [sys.executable, "-X", "faulthandler"]
        if job["kind"] == "split-guitar":
            shutil.copytree(project / 'stems', stage)
            return [*python, '-m', 'unweave', 'split-guitar',
                    '--file', str(stage / stem_file(stage, 'guitar')), '--output', str(stage),
                    '--cache', str(self.cache), *range_args]
        command = [*python, "-m", "unweave", "separate", "--file", str(project / original),
                   "--output", str(stage), "--cache", str(self.cache)]
        if group is not None:
            # An explicit plan may legitimately keep no stem of its own, so send
            # --stems unconditionally (nargs="*") to mean "own files = these".
            command += ["--stems", *stems]
            if group:
                command += ["--group", *group]
        elif stems:
            command += ["--stems", *stems]
        if split_leaves:
            command += ["--split-guitar"]
        return command + range_args

    def _expect_outputs(self, stage: Path, stems: list[str], group):
        """A merged stem must NOT reach the stage: only own files and other.flac do."""
        names = [*stems, *(['other'] if group else [])]
        for name in dict.fromkeys(names):
            path = stage / f"{name}.flac"
            if not path.is_file() or path.stat().st_size <= 44:
                raise RuntimeError(f"CLI did not produce {name}.flac.")

    def start_chords(self, identifier: str, *, lyrics: bool = False):
        del lyrics  # Chordify-style chords only; flag kept for API compatibility.
        with self.lock:
            project = project_path(self.require_root(), identifier)
            info = describe_project(project)
            if not info["stems"]:
                raise ValueError("Separate the song into stems before analyzing chords.")
            if not chords_available():
                raise ValueError(
                    "Chord analysis needs librosa, numpy, and soundfile "
                    "(install the guitars/chords extras)."
                )
            # Rough scratch space for the mixed harmony WAV.
            require_free_space(project, 80_000_000)
            job = {"id": uuid.uuid4().hex, "kind": "chords", "project": identifier,
                   "status": "queued", "started": time.time(), "finished": None, "error": None,
                   "lyrics": False,
                   "phase": "prepare", "progress": 0, "step_progress": 0, "eta": None,
                   "step_eta": None, "step_eta_at": None, "eta_estimated": False,
                   "pass_count": 1, "pass_index": 1,
                   "_project": project, "_lyrics": False}
            return self._enqueue(job)

    def _chords(self, job, project: Path, lyrics: bool = False):
        try:
            def report(phase, pct):
                with self.lock:
                    if job["status"] == "cancelling":
                        raise RuntimeError("Chord analysis cancelled.")
                    job["phase"] = phase if phase != "done" else "finalize"
                    job["progress"] = max(2, min(96, float(pct)))
                    job["step_progress"] = max(job.get("step_progress") or 0, round(float(pct)))
                    if pct > 5:
                        job["eta"] = (time.time() - job["started"]) / (pct / 100) + 1
                        job["eta_at"] = time.time()
                        job["step_eta"] = job["eta"]
                        job["step_eta_at"] = job["eta_at"]
                        job["eta_estimated"] = False

            analyze_project(project, lyrics=False, report=report)
            with self.lock:
                if job["status"] != "cancelling":
                    job.update(status="succeeded", progress=100, step_progress=100,
                               phase="done", eta=0)
        except Exception as error:
            with self.lock:
                if job["status"] != "cancelling":
                    job.update(status="failed", error=str(error))

    def update_chord_overrides(self, identifier: str, overrides):
        with self.lock:
            project = project_path(self.require_root(), identifier)
            return save_chord_overrides(project, overrides)

    def update_chord_ui(self, identifier: str, ui: dict):
        with self.lock:
            project = project_path(self.require_root(), identifier)
            return save_ui_prefs(project, ui)

    def export_chordpro(self, identifier: str, *, mode: str = "simple"):
        with self.lock:
            project = project_path(self.require_root(), identifier)
            info = describe_project(project)
            data = read_chords(project)
            if not data:
                raise ValueError("No chord analysis to export.")
            if mode not in {"simple", "extended"}:
                mode = (data.get("ui") or {}).get("mode") or "simple"
            path = write_chordpro_export(
                project, data, title=info.get("title") or "", artist=info.get("artist") or "",
                mode=mode,
            )
            return {"ok": True, "file": path.name, "text": to_chordpro(
                data, title=info.get("title") or "", artist=info.get("artist") or "", mode=mode)}

    def start_export(self, identifier: str, formats, stems, *, prevent_clipping=True):
        with self.lock:
            project = project_path(self.require_root(), identifier)
            info = describe_project(project)
            if not info["stems"]:
                raise ValueError("This project has no stems to export.")
            if not isinstance(formats, list) or not formats:
                raise ValueError("Choose at least one export format.")
            chosen_formats = []
            for format_id in formats:
                if not isinstance(format_id, str) or format_id not in FORMATS:
                    raise ValueError("Unknown export format.")
                if format_id not in chosen_formats:
                    chosen_formats.append(format_id)
            if not isinstance(stems, list) or not stems:
                raise ValueError("Choose at least one stem to export.")
            if not isinstance(prevent_clipping, bool):
                raise ValueError("Invalid clipping setting.")
            available = {item["file"] for item in info["stems"]}
            chosen_stems = []
            for name in stems:
                if not isinstance(name, str) or name not in available:
                    raise ValueError("Unknown stem to export.")
                if name not in chosen_stems:
                    chosen_stems.append(name)
            exports = project / "exports"
            if exports.is_symlink() or (exports.exists() and not exports.is_dir()):
                raise ValueError("The exports folder must be a real directory.")
            if not ensure_ffmpeg_on_path():
                raise ValueError(MISSING_FFMPEG)
            # Measured from the stored FLAC stems: PCM formats roughly double them,
            # FLAC stays about the same, lossy formats are a fraction.
            factor = sum(EXPORT_SIZE_FACTOR.get(format_id, 2.0) for format_id in chosen_formats)
            sizes = {item["file"]: item["bytes"] for item in info["stems"]}
            disk_need = sum(sizes[name] for name in chosen_stems) * factor
            require_free_space(project, disk_need)
            job = {"id": uuid.uuid4().hex, "kind": "export", "project": identifier,
                   "status": "queued", "started": time.time(), "finished": None, "error": None,
                   "formats": chosen_formats, "stems": chosen_stems,
                   "prevent_clipping": bool(prevent_clipping),
                   "phase": "prepare", "progress": 0, "step_progress": 0, "eta": None,
                   "step_eta": None, "step_eta_at": None, "eta_estimated": False,
                   "pass_count": 1, "pass_index": 1,
                   "_project": project, "_formats": chosen_formats, "_stems": chosen_stems,
                   "_prevent_clipping": bool(prevent_clipping), "_disk_need": disk_need}
            return self._enqueue(job)

    def _export(self, job, project: Path, formats: list[str], stems: list[str], prevent_clipping: bool):
        job_id = job["id"]
        try:
            require_free_space(project, job["_disk_need"])
            def report(completed, total, label):
                with self.lock:
                    if job["status"] == "cancelling":
                        raise RuntimeError("Export cancelled.")
                    fraction = completed / total if total else 0
                    job["phase"] = "finalize" if completed >= total else "export"
                    job["progress"] = max(2, min(96, 2 + fraction * 93))
                    # The ring is per step: this report is the export step's own 0-100.
                    job["step_progress"] = max(job.get("step_progress") or 0, round(fraction * 100))
                    if fraction > 0.02:
                        job["eta"] = (time.time() - job["started"]) / fraction + 1
                        job["eta_at"] = time.time()
                        job["step_eta"] = job["eta"]
                        job["step_eta_at"] = job["eta_at"]
                        job["eta_estimated"] = False

            def set_process(process):
                with self.lock:
                    if process is not None and job["status"] == "cancelling":
                        stop_process(process)
                    if process is None:
                        self.processes.pop(job_id, None)
                    else:
                        self.processes[job_id] = process

            export_stems(project, stems, formats, prevent_clipping, report, set_process)
            with self.lock:
                if job["status"] != "cancelling":
                    job.update(status="succeeded", progress=100, step_progress=100, phase="done", eta=0)
        except Exception as error:
            with self.lock:
                if job["status"] != "cancelling":
                    job.update(status="failed", error=str(error))

    def _jobs_busy(self) -> bool:
        """True when any job is active or queued. Caller holds the lock."""
        if self.active:
            return True
        return any(job.get("status") in {"queued", "running", "cancelling"} for job in self.queue)

    def _process_for(self, job: dict):
        """A job's subprocess. setup_env only sets the `process` alias, which is safe
        to use for setup because setup always runs alone. Caller holds the lock."""
        process = self.processes.get(job["id"])
        if process is None and job["kind"] == "setup":
            process = self.process
        return process

    def cancel(self, job_id: str | None = None):
        processes_to_wait = []
        with self.lock:
            if job_id:
                # Cancel a specific job: either active or waiting in the queue.
                target = None
                if job_id in self.active:
                    target = self.active_jobs.get(job_id)
                    if target:
                        target["status"] = "cancelling"
                        proc = self._process_for(target)
                        if proc:
                            processes_to_wait.append(proc)
                            stop_process(proc)
                else:
                    for index, job in enumerate(self.queue):
                        if job["id"] == job_id:
                            target = self.queue.pop(index)
                            target["status"] = "cancelled"
                            target["finished"] = time.time()
                            break
                    if target is None:
                        raise ValueError("Job not found in queue.")
            else:
                # Cancel all active jobs (used by close).
                if not self.running():
                    return
                for jid in list(self.active):
                    target = self.active_jobs.get(jid)
                    if target:
                        target["status"] = "cancelling"
                    proc = self._process_for(target) if target else None
                    if proc:
                        processes_to_wait.append(proc)
                        stop_process(proc)
        for proc in processes_to_wait:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stop_process(proc, force=True)

    def clear_exports(self, identifier: str, expected_root: str) -> dict:
        """Delete only a project's exports/ folder. Never touches stems or originals."""
        with self.lock:
            root = self.require_root()
            if expected_root != str(root):
                raise ValueError("The projects folder changed. Refresh settings and try again.")
            project = project_path(root, identifier)
            if project.is_symlink() or not project.is_dir():
                raise ValueError("Project not found.")
            for job in list(self.active_jobs.values()) + list(self.queue):
                if job.get("project") == identifier and job.get("status") in {"running", "cancelling", "queued"}:
                    raise ValueError("Wait for this song's job to finish or cancel it before clearing exports.")
            exports = project / "exports"
            freed = 0
            if exports.is_symlink():
                raise ValueError("The exports folder is not a real directory.")
            if exports.is_dir():
                freed = dir_bytes(exports)
                shutil.rmtree(exports)
            # Stems, originals, and metadata stay untouched.
            return {"ok": True, "freed_bytes": freed, "exports_bytes": 0}

    def delete(self, identifier: str, expected_root: str):
        with self.lock:
            root = self.require_root()
            if expected_root != str(root):
                raise ValueError("The projects folder changed. Reopen the deletion dialog.")
            project = project_path(root, identifier)
            active_jobs = list(self.active_jobs.values()) + list(self.queue)
            for job in active_jobs:
                if job.get("project") == identifier:
                    if job["status"] in {"running", "cancelling", "queued"}:
                        raise ValueError("Wait for separation to finish or cancel it before deleting this song.")
            shutil.rmtree(project)
            if self.job and self.job.get("project") == identifier:
                self.job = None
                self.logs.clear()
            return {"ok": True}

