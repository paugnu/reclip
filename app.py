import os
import uuid
import glob
import json
import subprocess
import threading
import shutil
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

jobs = {}


def parse_spotify_url(url):
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc != "open.spotify.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if parts[0].startswith("intl-"):
        parts = parts[1:]
    if len(parts) < 2:
        return None
    media_type = parts[0]
    if media_type not in {"track", "album", "playlist"}:
        return None
    return {"type": media_type, "id": parts[1]}


def is_spotify_url(url):
    return parse_spotify_url(url) is not None


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
    spotify_info = parse_spotify_url(url)
    if spotify_info:
        run_spotify_download(job_id, url, spotify_info)
        return

    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = ["yt-dlp", "--no-playlist", "-o", out_template]

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            job["status"] = "error"
            job["error"] = result.stderr.strip().split("\n")[-1]
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            return

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
            chosen = target[0] if target else files[0]
        else:
            target = [f for f in files if f.endswith(".mp4")]
            chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        job["status"] = "done"
        job["file"] = chosen
        ext = os.path.splitext(chosen)[1]
        title = job.get("title", "").strip()
        # Sanitize title for filename
        if title:
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:20].strip()
            job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        else:
            job["filename"] = os.path.basename(chosen)
    except subprocess.TimeoutExpired:
        job["status"] = "error"
        job["error"] = "Download timed out (5 min limit)"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


def run_spotify_download(job_id, spotify_url, spotify_info):
    job = jobs[job_id]
    job["status"] = "resolving metadata"
    if shutil.which("spotdl") is None:
        job["status"] = "error"
        job["error"] = "Spotify support requires spotDL. Install it with: pip install spotdl"
        return

    before = set(glob.glob(os.path.join(DOWNLOAD_DIR, "*")))
    output_template = os.path.join(DOWNLOAD_DIR, "{artist} - {title}.{output-ext}")
    cmd = [
        "spotdl",
        "download",
        spotify_url,
        "--output",
        output_template,
        "--format",
        "mp3",
        "--restrict",
        "ascii",
    ]
    timeout = 1200 if spotify_info["type"] in {"album", "playlist"} else 600
    try:
        job["status"] = "matching audio"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            job["status"] = "error"
            job["error"] = (result.stderr or result.stdout or "spotDL failed").strip().split("\n")[-1]
            return

        after = set(glob.glob(os.path.join(DOWNLOAD_DIR, "*")))
        new_files = sorted([f for f in (after - before) if os.path.isfile(f)])
        if not new_files:
            job["status"] = "error"
            job["error"] = "spotDL completed but no files were produced"
            return

        job["status"] = "tagging metadata"
        if len(new_files) == 1:
            job["file"] = new_files[0]
            job["filename"] = os.path.basename(new_files[0])
        else:
            job["files"] = new_files
            job["filename"] = f"{spotify_info['type']}-{job_id} ({len(new_files)} tracks).txt"
        job["status"] = "done"
    except subprocess.TimeoutExpired:
        job["status"] = "error"
        job["error"] = "Spotify download timed out"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    spotify_info = parse_spotify_url(url)
    if spotify_info:
        return jsonify({
            "id": spotify_info["id"],
            "original_url": url,
            "source": "spotify",
            "type": spotify_info["type"],
            "title": f"Spotify {spotify_info['type']}",
            "thumbnail": None,
            "formats": [],
            "available_output_formats": ["mp3"],
        })

    cmd = ["yt-dlp", "--no-playlist", "-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)

        # Build quality options — keep best format per resolution
        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = []
        for height, f in best_by_height.items():
            formats.append({
                "id": f["format_id"],
                "label": f"{height}p",
                "height": height,
            })
        formats.sort(key=lambda x: x["height"], reverse=True)

        return jsonify({
            "source": "yt-dlp",
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "queued", "url": url, "title": title}

    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    if job.get("files"):
        return jsonify({"error": "Multiple files created; single-file download is not available yet for this Spotify URL type"}), 400
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
