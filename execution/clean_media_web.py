"""
clean_media_web.py
==================
Flask web UI for the Media Cleaner tool.
Team members upload images/videos, pick number of copies,
and download cleaned files as a zip.

Usage:
    python execution/clean_media_web.py

Requires:
    Flask, Pillow, FFmpeg on PATH, tqdm
    CLEANER_PIN in .env (shared access PIN)
"""

import os
import sys
import uuid
import shutil
import zipfile
import threading
import time
import logging
from pathlib import Path
from datetime import datetime

from flask import (
    Flask, request, session, redirect, url_for,
    render_template, jsonify, send_file,
)
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Path setup — ensure we can import clean_media.py
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

from clean_media import (
    process_single_image, process_single_video,
    SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_VIDEO_EXTENSIONS,
    check_ffmpeg, generate_output_filename,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv(PROJECT_ROOT / ".env")

CLEANER_PIN = os.getenv("CLEANER_PIN", "1234")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", uuid.uuid4().hex)
PORT = int(os.getenv("CLEANER_PORT", "8081"))
MAX_CONTENT_LENGTH = 1 * 1024 * 1024 * 1024  # 1GB

UPLOAD_DIR = PROJECT_ROOT / ".tmp" / "cleaner_uploads"
OUTPUT_DIR = PROJECT_ROOT / ".tmp" / "cleaner_outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Set in __main__ before app.run()
HAS_FFMPEG = None

# Job tracking: job_id -> {status, total, done, errors, output_dir, zip_path, created}
jobs = {}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("clean_media_web")

# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# ---------------------------------------------------------------------------
# Cleanup — remove jobs older than 1 hour
# ---------------------------------------------------------------------------

def cleanup_old_jobs():
    """Background thread: delete jobs older than 1 hour."""
    while True:
        time.sleep(300)  # check every 5 min
        now = time.time()
        expired = [
            jid for jid, j in jobs.items()
            if now - j.get("created", now) > 3600
        ]
        for jid in expired:
            job = jobs.pop(jid, None)
            if job:
                _cleanup_job_files(jid)
                log.info(f"Cleaned up expired job {jid}")


def _cleanup_job_files(job_id):
    """Remove upload and output dirs for a job."""
    upload_path = UPLOAD_DIR / job_id
    output_path = OUTPUT_DIR / job_id
    zip_path = OUTPUT_DIR / f"{job_id}.zip"
    for p in [upload_path, output_path]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    if zip_path.exists():
        zip_path.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.before_request
def require_pin():
    """Check PIN for all routes except /login and /static."""
    allowed = ("login", "static")
    if request.endpoint and request.endpoint in allowed:
        return
    if not session.get("authenticated"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pin = request.form.get("pin", "")
        if pin == CLEANER_PIN:
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "Wrong PIN"
    return render_template("cleaner.html", page="login", error=error)

# ---------------------------------------------------------------------------
# Main Page
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    has_ffmpeg = check_ffmpeg()
    return render_template("cleaner.html", page="upload", has_ffmpeg=has_ffmpeg)

# ---------------------------------------------------------------------------
# Upload + Process
# ---------------------------------------------------------------------------

@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    copies = int(request.form.get("copies", 1))
    copies = max(1, min(5, copies))

    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "No files uploaded"}), 400

    # Create job
    job_id = uuid.uuid4().hex[:12]
    job_upload_dir = UPLOAD_DIR / job_id
    job_output_dir = OUTPUT_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    job_output_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded files
    saved_files = []
    all_extensions = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
    for f in files:
        if f.filename == "":
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in all_extensions:
            continue
        safe_name = f"{uuid.uuid4().hex[:8]}_{Path(f.filename).name}"
        save_path = job_upload_dir / safe_name
        f.save(save_path)
        saved_files.append(save_path)

    if not saved_files:
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        shutil.rmtree(job_output_dir, ignore_errors=True)
        return jsonify({"error": "No supported files (JPG, PNG, WebP, MP4, MOV)"}), 400

    total_tasks = len(saved_files) * copies
    jobs[job_id] = {
        "status": "processing",
        "total": total_tasks,
        "done": 0,
        "errors": 0,
        "output_dir": str(job_output_dir),
        "zip_path": None,
        "created": time.time(),
        "filenames": [f.name for f in saved_files],
    }

    # Process in background thread
    log.info(f"Job {job_id}: launching thread for {len(saved_files)} files x {copies} copies")
    thread = threading.Thread(
        target=_process_job,
        args=(job_id, saved_files, job_output_dir, copies),
        daemon=True,
    )
    thread.start()
    log.info(f"Job {job_id}: thread started")

    return jsonify({"job_id": job_id, "total": total_tasks})


def _process_job(job_id, files, output_dir, copies):
    """Background worker: process all files for a job."""
    log.info(f"Job {job_id}: thread ENTERED _process_job")
    job = jobs[job_id]

    try:
        has_ffmpeg = HAS_FFMPEG
        log.info(f"Job {job_id}: ffmpeg={has_ffmpeg}, processing {len(files)} files")

        for source_file in files:
            ext = source_file.suffix.lower()
            is_image = ext in SUPPORTED_IMAGE_EXTENSIONS
            is_video = ext in SUPPORTED_VIDEO_EXTENSIONS

            # Use original filename (strip the uuid prefix we added)
            original_name = "_".join(source_file.name.split("_")[1:])
            original_path = Path(original_name)

            for copy_idx in range(1, copies + 1):
                out_name = generate_output_filename(original_path, copy_idx, copies)
                output_path = output_dir / out_name

                try:
                    log.info(f"Job {job_id}: processing {source_file.name} (image={is_image}, video={is_video}) copy {copy_idx}")
                    if is_image:
                        ok = process_single_image(source_file, output_path)
                    elif is_video and has_ffmpeg:
                        ok = process_single_video(source_file, output_path)
                    else:
                        log.warning(f"Job {job_id}: skipping {source_file.name} (unsupported or no ffmpeg)")
                        ok = False
                    log.info(f"Job {job_id}: {source_file.name} copy {copy_idx} -> {'OK' if ok else 'FAIL'}")
                except Exception as e:
                    log.error(f"Job {job_id}: failed processing {source_file.name} copy {copy_idx}: {e}", exc_info=True)
                    ok = False

                if not ok:
                    job["errors"] += 1
                job["done"] += 1

        # Create zip
        zip_path = OUTPUT_DIR / f"{job_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(output_dir.iterdir()):
                if f.is_file():
                    zf.write(f, f.name)

        job["zip_path"] = str(zip_path)
        job["status"] = "done"

        # Clean up upload dir (keep outputs for download)
        upload_dir = UPLOAD_DIR / job_id
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)

        log.info(f"Job {job_id} complete: {job['done'] - job['errors']}/{job['total']} ok")

    except Exception as e:
        log.error(f"Job {job_id} CRASHED: {e}", exc_info=True)
        job["status"] = "error"
        job["error_message"] = str(e)
        # Still mark remaining as done so frontend stops polling
        job["done"] = job["total"]

# ---------------------------------------------------------------------------
# Status Polling
# ---------------------------------------------------------------------------

@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    result = {
        "status": job["status"],
        "total": job["total"],
        "done": job["done"],
        "errors": job["errors"],
    }
    if job.get("error_message"):
        result["error_message"] = job["error_message"]
    return jsonify(result)

# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

@app.route("/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if not job or not job.get("zip_path"):
        return jsonify({"error": "Not ready or not found"}), 404

    zip_path = Path(job["zip_path"])
    if not zip_path.exists():
        return jsonify({"error": "File not found"}), 404

    return send_file(
        zip_path,
        as_attachment=True,
        download_name=f"cleaned_{job_id}.zip",
        mimetype="application/zip",
    )

# ---------------------------------------------------------------------------
# Cleanup single job (called after download)
# ---------------------------------------------------------------------------

@app.route("/cleanup/<job_id>", methods=["POST"])
def cleanup(job_id):
    job = jobs.pop(job_id, None)
    if job:
        _cleanup_job_files(job_id)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_jobs, daemon=True)
    cleanup_thread.start()

    log.info(f"Media Cleaner Web UI starting on port {PORT}")
    HAS_FFMPEG = check_ffmpeg()  # noqa: F841 — reassigns module-level var
    log.info(f"FFmpeg available: {HAS_FFMPEG}")

    app.run(host="0.0.0.0", port=PORT, debug=False)
