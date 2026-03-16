from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, request
from flask_cors import CORS

from crawl_service_core import manager


app = Flask(__name__)
CORS(app)


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok"})


@app.post("/api/crawl/start")
def start_job() -> Any:
    data = request.get_json(silent=True) or {}
    command = str(data.get("command") or "belfer_llm_enrich")
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    try:
        job = manager.start_job(command, args)
        return jsonify({"job_id": job.job_id, "status": job.status})
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": f"start failed: {e}"}), 500


@app.get("/api/crawl/jobs/<job_id>")
def get_job(job_id: str) -> Any:
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"message": "job not found"}), 404
    return jsonify(manager.serialize_job(job))


@app.get("/api/crawl/jobs/<job_id>/logs")
def get_logs(job_id: str) -> Any:
    from_idx = request.args.get("from", default="0")
    try:
        start = int(from_idx)
    except Exception:
        start = 0
    try:
        lines, nxt = manager.get_logs(job_id, start)
        return jsonify({"job_id": job_id, "lines": lines, "next_cursor": nxt})
    except KeyError:
        return jsonify({"message": "job not found"}), 404
    except Exception as e:
        return jsonify({"message": f"log read failed: {e}"}), 500


@app.post("/api/crawl/jobs/<job_id>/stop")
def stop_job(job_id: str) -> Any:
    ok = manager.stop_job(job_id)
    if not ok:
        return jsonify({"message": "job not found"}), 404
    return jsonify({"job_id": job_id, "message": "stop signal sent"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, debug=True)
