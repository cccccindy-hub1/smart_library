from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from crawl_service_core import manager


class StartRequest(BaseModel):
    command: str = Field(default="belfer_llm_enrich")
    args: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="Global Smart Library Crawl API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/crawl/start")
def start_job(req: StartRequest) -> dict[str, Any]:
    try:
        job = manager.start_job(req.command, req.args)
        return {"job_id": job.job_id, "status": job.status}
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"start failed: {e}")


@app.get("/api/crawl/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return manager.serialize_job(job)


@app.get("/api/crawl/jobs/{job_id}/logs")
def get_logs(job_id: str, from_idx: int = Query(default=0, alias="from")) -> dict[str, Any]:
    try:
        lines, nxt = manager.get_logs(job_id, from_idx)
        return {"job_id": job_id, "lines": lines, "next_cursor": nxt}
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"log read failed: {e}")


@app.post("/api/crawl/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, Any]:
    ok = manager.stop_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, "message": "stop signal sent"}
