from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCRIPT_NAME = "belfer_llm_enrich.py"
ALLOWED_COMMANDS = {"belfer_llm_enrich"}
PROCESSED_RE = re.compile(r"\[INFO\]\s+processed\s+(\d+)", re.IGNORECASE)


def _to_cli_args(args: dict[str, Any]) -> list[str]:
    cli: list[str] = []
    for key, value in args.items():
        flag = "--" + str(key).strip().replace("_", "-")
        if isinstance(value, bool):
            if value:
                cli.append(flag)
            continue
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        cli.extend([flag, str(value)])
    return cli


@dataclass
class CrawlJob:
    job_id: str
    command: str
    args: dict[str, Any]
    status: str = "queued"
    started_at: str | None = None
    ended_at: str | None = None
    created_ts: float = field(default_factory=time.time)
    logs: list[str] = field(default_factory=list)
    log_lock: threading.Lock = field(default_factory=threading.Lock)
    process: subprocess.Popen[str] | None = None
    stop_requested: bool = False
    exit_code: int | None = None
    processed: int = 0
    success_count: int = 0
    failed_count: int = 0


class CrawlJobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, CrawlJob] = {}
        self._jobs_lock = threading.Lock()
        self._base_dir = Path(__file__).resolve().parent
        self._script_path = self._base_dir / SCRIPT_NAME

    def start_job(self, command: str, args: dict[str, Any]) -> CrawlJob:
        if command not in ALLOWED_COMMANDS:
            raise ValueError(f"Unsupported command: {command}")
        if not self._script_path.exists():
            raise FileNotFoundError(f"Script not found: {self._script_path}")

        job_id = uuid.uuid4().hex[:12]
        job = CrawlJob(job_id=job_id, command=command, args=dict(args or {}))
        with self._jobs_lock:
            self._jobs[job_id] = job

        t = threading.Thread(target=self._run_job, args=(job_id,), daemon=True)
        t.start()
        return job

    def get_job(self, job_id: str) -> CrawlJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def stop_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        job.stop_requested = True
        proc = job.process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                return True
            except Exception:
                return False
        return True

    def get_logs(self, job_id: str, from_idx: int) -> tuple[list[str], int]:
        job = self.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        start = max(0, int(from_idx))
        with job.log_lock:
            lines = job.logs[start : start + 800]
            nxt = start + len(lines)
        return lines, nxt

    def _run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return

        py = sys.executable or "python"
        cmd = [py, str(self._script_path), *_to_cli_args(job.args)]

        job.status = "running"
        job.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._append_log(job, f"[SERVICE] start job {job.job_id}")
        self._append_log(job, f"[SERVICE] command: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._base_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            job.process = proc

            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                if line:
                    self._append_log(job, line)
                    self._update_metrics_from_line(job, line)

            if job.stop_requested and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

            rc = proc.wait(timeout=5)
            job.exit_code = rc
            if job.stop_requested:
                job.status = "stopped"
            else:
                job.status = "succeeded" if rc == 0 else "failed"
        except subprocess.TimeoutExpired:
            proc = job.process
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            job.status = "stopped" if job.stop_requested else "failed"
            job.exit_code = -1
            self._append_log(job, "[SERVICE] process timeout, force killed")
        except Exception as e:
            job.status = "failed"
            job.exit_code = -1
            self._append_log(job, f"[SERVICE] runtime error: {e}")
        finally:
            job.ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._append_log(job, f"[SERVICE] end job {job.job_id}, status={job.status}, exit_code={job.exit_code}")

    def _append_log(self, job: CrawlJob, line: str) -> None:
        with job.log_lock:
            job.logs.append(line)

    def _update_metrics_from_line(self, job: CrawlJob, line: str) -> None:
        m = PROCESSED_RE.search(line)
        if m:
            try:
                job.processed = max(job.processed, int(m.group(1)))
                job.success_count = job.processed
            except Exception:
                pass
        if "[WARN]" in line.upper():
            job.failed_count += 1

    def serialize_job(self, job: CrawlJob) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "command": job.command,
            "status": job.status,
            "started_at": job.started_at,
            "ended_at": job.ended_at,
            "processed": job.processed,
            "success_count": job.success_count,
            "failed_count": job.failed_count,
            "exit_code": job.exit_code,
            "args": job.args,
        }


manager = CrawlJobManager()
