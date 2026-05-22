"""
YouTube Video Dashboard
-----------------------
Requirements:
    pip install flask yt-dlp

Run:
    python dashboard.py

Then open http://localhost:5000 in your browser.
Videos are saved to the ./downloads/ folder by default.
"""

import os
import re
import json
import threading
import subprocess
import glob
import socket
import platform
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory, Response
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

load_dotenv()

app = Flask(__name__)

# ─────────────────────────────────────────────
# Platform-Specific Downloads Directory
# ─────────────────────────────────────────────

def get_platform_downloads_dir():
    """Return platform-specific youtube_downloads path"""
    system = platform.system()

    if system == "Darwin":  # macOS
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path.home() / "AppData" / "Local"
    else:  # Linux
        base = Path.home() / ".local" / "share"

    youtube_dir = base / "youtube_downloads"
    youtube_dir.mkdir(parents=True, exist_ok=True)
    return youtube_dir

DOWNLOAD_DIR = get_platform_downloads_dir()

# ─────────────────────────────────────────────
# Firebase Initialization
# ─────────────────────────────────────────────

DEVICE_ID = os.getenv("DEVICE_ID", socket.gethostname())
db = None

def init_firebase():
    """Initialize Firebase with credentials from environment"""
    global db
    try:
        firebase_config = {
            "type": "service_account",
            "project_id": os.getenv("FIREBASE_PROJECT_ID"),
            "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
            "private_key": os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
            "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
            "client_id": os.getenv("FIREBASE_CLIENT_ID"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": os.getenv("FIREBASE_CERT_URL", ""),
        }

        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        register_device()
        print("[OK] Firebase initialized for device: {}".format(DEVICE_ID))
    except Exception as e:
        print("[WARNING] Firebase not configured: {}".format(str(e)[:100]))
        print("  Downloads will work locally without cloud sync.")
        db = None

def register_device():
    """Register device in Firestore on startup"""
    if db is None:
        return
    try:
        db.collection('devices').document(DEVICE_ID).set({
            "name": DEVICE_ID,
            "last_seen": firestore.SERVER_TIMESTAMP,
            "platform": platform.system(),
        }, merge=True)
    except Exception as e:
        print("[WARNING] Could not register device: {}".format(str(e)[:100]))

def sync_download_to_firestore(task_id, url, status, filename=None, progress=0, error=None):
    """Write download metadata to Firestore"""
    if db is None:
        return
    try:
        doc_ref = db.collection('devices').document(DEVICE_ID).collection('downloads').document(task_id)

        data = {
            "url": url,
            "status": status,
            "progress": progress,
            "timestamp": firestore.SERVER_TIMESTAMP,
        }
        if filename:
            data["filename"] = filename
        if error:
            data["error"] = error
        if status == "done":
            data["completed_at"] = firestore.SERVER_TIMESTAMP

        doc_ref.set(data, merge=True)
    except Exception as e:
        pass  # Fail silently - downloads continue even if Firestore is unavailable

# Track active downloads: { task_id: { status, url, progress, filename, error } }
active_downloads = {}
download_lock = threading.Lock()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def get_video_files():
    extensions = ["*.mp4", "*.mkv", "*.webm", "*.avi", "*.mov", "*.flv"]
    files = []
    for ext in extensions:
        files.extend(DOWNLOAD_DIR.glob(ext))
    result = []
    for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
        stat = f.stat()
        result.append({
            "name": f.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "path": str(f),
        })
    return result


def run_download(task_id, url, quality):
    """Run yt-dlp in a thread and stream progress into active_downloads."""
    format_map = {
        "best":   "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "1080p":  "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
        "720p":   "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
        "480p":   "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
        "audio":  "bestaudio[ext=m4a]/bestaudio",
    }
    fmt = format_map.get(quality, format_map["best"])
    out_tmpl = str(DOWNLOAD_DIR / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--newline",
        "--progress",
        "-o", out_tmpl,
        url,
    ]

    with download_lock:
        active_downloads[task_id]["status"] = "downloading"
    sync_download_to_firestore(task_id, url, "downloading")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        filename = None
        for line in proc.stdout:
            line = line.strip()
            # Parse progress line
            if "[download]" in line:
                # Extract percentage
                pct_match = re.search(r"(\d+\.\d+)%", line)
                if pct_match:
                    pct = float(pct_match.group(1))
                    with download_lock:
                        active_downloads[task_id]["progress"] = pct
                    sync_download_to_firestore(task_id, url, "downloading", progress=pct)
                # Extract destination filename
                dest_match = re.search(r"Destination:\s+(.+)", line)
                if dest_match:
                    filename = Path(dest_match.group(1)).name
                    with download_lock:
                        active_downloads[task_id]["filename"] = filename
                    sync_download_to_firestore(task_id, url, "downloading", filename=filename)
            # Merger line gives final name
            if "[Merger]" in line or "Merging formats" in line:
                merge_match = re.search(r'"([^"]+)"', line)
                if merge_match:
                    filename = Path(merge_match.group(1)).name
                    with download_lock:
                        active_downloads[task_id]["filename"] = filename
                    sync_download_to_firestore(task_id, url, "downloading", filename=filename)

        proc.wait()
        if proc.returncode == 0:
            with download_lock:
                active_downloads[task_id]["status"] = "done"
                active_downloads[task_id]["progress"] = 100
            sync_download_to_firestore(task_id, url, "done", filename=filename, progress=100)
        else:
            with download_lock:
                active_downloads[task_id]["status"] = "error"
                active_downloads[task_id]["error"] = "yt-dlp exited with an error. Check the URL or format."
            sync_download_to_firestore(task_id, url, "error", error="yt-dlp exited with an error")
    except FileNotFoundError:
        with download_lock:
            active_downloads[task_id]["status"] = "error"
            active_downloads[task_id]["error"] = "yt-dlp not found. Install it: pip install yt-dlp"
        sync_download_to_firestore(task_id, url, "error", error="yt-dlp not found")
    except Exception as e:
        with download_lock:
            active_downloads[task_id]["status"] = "error"
            active_downloads[task_id]["error"] = str(e)
        sync_download_to_firestore(task_id, url, "error", error=str(e))


# ─────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────

@app.route("/api/videos")
def api_videos():
    return jsonify(get_video_files())


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    quality = data.get("quality", "best")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    task_id = str(datetime.now().timestamp()).replace(".", "")
    with download_lock:
        active_downloads[task_id] = {
            "status": "queued",
            "url": url,
            "progress": 0,
            "filename": None,
            "error": None,
        }

    t = threading.Thread(target=run_download, args=(task_id, url, quality), daemon=True)
    t.start()

    return jsonify({"task_id": task_id})


@app.route("/api/status/<task_id>")
def api_status(task_id):
    with download_lock:
        info = active_downloads.get(task_id)
    if not info:
        return jsonify({"error": "Unknown task"}), 404
    return jsonify(info)


@app.route("/api/delete", methods=["POST"])
def api_delete():
    data = request.get_json(force=True)
    name = data.get("name", "")
    task_id = data.get("task_id")
    target = DOWNLOAD_DIR / Path(name).name  # prevent path traversal
    if target.exists() and target.is_file():
        target.unlink()
        # Sync deletion to Firestore
        if task_id and db:
            try:
                db.collection('devices').document(DEVICE_ID).collection('downloads').document(task_id).delete()
            except Exception as e:
                print(f"⚠ Could not delete from Firestore: {e}")
        return jsonify({"ok": True})
    return jsonify({"error": "File not found"}), 404


@app.route("/api/device")
def api_device():
    """Return device information"""
    return jsonify({
        "device_id": DEVICE_ID,
        "platform": platform.system(),
        "downloads_dir": str(DOWNLOAD_DIR),
        "firebase_enabled": db is not None,
    })


@app.route("/video/<path:filename>")
def serve_video(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)


# ─────────────────────────────────────────────
# Frontend (single-file SPA)
# ─────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VaultTube — Video Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;500;600&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

:root{
  --bg:#0a0a0f;
  --surface:#12121a;
  --card:#1a1a26;
  --border:#2a2a3d;
  --accent:#ff3c3c;
  --accent2:#ff7c3c;
  --text:#f0f0f8;
  --muted:#6b6b8a;
  --green:#2ecc71;
  --yellow:#f39c12;
}

body{
  font-family:'DM Sans',sans-serif;
  background:var(--bg);
  color:var(--text);
  min-height:100vh;
  overflow-x:hidden;
}

/* Noise texture overlay */
body::before{
  content:'';
  position:fixed;inset:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
  pointer-events:none;z-index:0;opacity:.5;
}

/* ── Header ─────────────────────────────── */
header{
  position:relative;
  padding:2.5rem 3rem 2rem;
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:1.5rem;
  background:linear-gradient(135deg,#12121a 60%,#1a0a1a);
}
.logo-mark{
  width:52px;height:52px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  border-radius:14px;
  display:flex;align-items:center;justify-content:center;
  font-size:1.6rem;
  box-shadow:0 0 30px rgba(255,60,60,.35);
  flex-shrink:0;
}
.logo-text h1{
  font-family:'Bebas Neue',sans-serif;
  font-size:2.4rem;letter-spacing:.08em;
  line-height:1;
  background:linear-gradient(90deg,#fff 30%,var(--accent));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
.logo-text p{font-size:.8rem;color:var(--muted);letter-spacing:.12em;text-transform:uppercase;margin-top:.25rem}
.header-stat{
  margin-left:auto;
  text-align:right;
}
.header-stat span{display:block;font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}
.header-stat strong{font-family:'JetBrains Mono',monospace;font-size:1.5rem;color:var(--accent)}

/* ── Layout ─────────────────────────────── */
main{
  display:grid;
  grid-template-columns:420px 1fr;
  gap:0;
  min-height:calc(100vh - 105px);
}

/* ── Sidebar ─────────────────────────────── */
aside{
  border-right:1px solid var(--border);
  padding:2rem;
  background:var(--surface);
  display:flex;flex-direction:column;gap:1.5rem;
}

.panel-title{
  font-family:'Bebas Neue',sans-serif;
  font-size:1.1rem;letter-spacing:.15em;
  color:var(--muted);
}

/* ── Download form ─────────────────────────── */
.url-input{
  width:100%;
  background:#0d0d16;
  border:1px solid var(--border);
  border-radius:10px;
  padding:.9rem 1rem;
  color:var(--text);
  font-family:'DM Sans',sans-serif;
  font-size:.9rem;
  outline:none;
  transition:border-color .2s,box-shadow .2s;
}
.url-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(255,60,60,.12)}
.url-input::placeholder{color:var(--muted)}

.quality-grid{
  display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;
}
.q-btn{
  background:#0d0d16;
  border:1px solid var(--border);
  border-radius:8px;
  padding:.55rem;
  color:var(--muted);
  font-family:'JetBrains Mono',monospace;
  font-size:.75rem;
  cursor:pointer;
  transition:all .18s;
  text-align:center;
}
.q-btn:hover{border-color:var(--accent);color:var(--text)}
.q-btn.active{
  background:rgba(255,60,60,.15);
  border-color:var(--accent);
  color:var(--accent);
}

.dl-btn{
  width:100%;
  padding:1rem;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  border:none;border-radius:10px;
  color:#fff;
  font-family:'Bebas Neue',sans-serif;
  font-size:1.2rem;letter-spacing:.12em;
  cursor:pointer;
  transition:opacity .2s,transform .15s,box-shadow .2s;
  box-shadow:0 4px 20px rgba(255,60,60,.3);
}
.dl-btn:hover{opacity:.9;transform:translateY(-1px);box-shadow:0 6px 28px rgba(255,60,60,.45)}
.dl-btn:active{transform:translateY(0)}
.dl-btn:disabled{opacity:.4;cursor:not-allowed;transform:none}

/* ── Progress area ─────────────────────────── */
.progress-area{display:flex;flex-direction:column;gap:.75rem}
.task-card{
  background:#0d0d16;
  border:1px solid var(--border);
  border-radius:10px;
  padding:1rem;
  animation:fadeIn .3s ease;
}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.task-url{
  font-family:'JetBrains Mono',monospace;
  font-size:.7rem;color:var(--muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  margin-bottom:.6rem;
}
.task-status{
  font-size:.78rem;font-weight:600;margin-bottom:.5rem;
  display:flex;align-items:center;gap:.4rem;
}
.dot{width:7px;height:7px;border-radius:50%;background:var(--muted)}
.dot.queued{background:var(--yellow)}
.dot.downloading{background:var(--accent);animation:pulse 1s infinite}
.dot.done{background:var(--green)}
.dot.error{background:#e74c3c}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.prog-bar-bg{height:4px;background:var(--border);border-radius:4px;overflow:hidden}
.prog-bar-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:4px;transition:width .4s ease}
.task-filename{font-size:.72rem;color:var(--muted);margin-top:.5rem;font-family:'JetBrains Mono',monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ── Video library ─────────────────────────── */
section.library{
  padding:2rem;
  overflow-y:auto;
}
.lib-header{
  display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem;
}
.lib-header h2{font-family:'Bebas Neue',sans-serif;font-size:1.5rem;letter-spacing:.08em}
.refresh-btn{
  background:transparent;
  border:1px solid var(--border);
  border-radius:8px;
  padding:.4rem .8rem;
  color:var(--muted);
  font-size:.8rem;cursor:pointer;
  transition:all .18s;
  margin-left:auto;
}
.refresh-btn:hover{border-color:var(--accent);color:var(--accent)}

.video-grid{
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(280px,1fr));
  gap:1.25rem;
}

.video-card{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:14px;
  overflow:hidden;
  transition:transform .2s,box-shadow .2s,border-color .2s;
  animation:fadeIn .35s ease both;
}
.video-card:hover{
  transform:translateY(-3px);
  box-shadow:0 12px 40px rgba(0,0,0,.5);
  border-color:rgba(255,60,60,.3);
}
.video-thumb{
  width:100%;
  aspect-ratio:16/9;
  background:#0d0d16;
  position:relative;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;
  overflow:hidden;
}
.video-thumb video{
  width:100%;height:100%;object-fit:cover;
  display:none;
}
.thumb-icon{
  font-size:2.5rem;color:var(--muted);
  transition:color .2s,transform .2s;
  position:absolute;
}
.video-thumb:hover .thumb-icon{color:var(--accent);transform:scale(1.1)}
.video-thumb.playing video{display:block}
.video-thumb.playing .thumb-icon{display:none}

.video-info{padding:.85rem 1rem 1rem}
.video-name{
  font-size:.85rem;font-weight:500;
  line-height:1.35;
  margin-bottom:.5rem;
  /* clamp to 2 lines */
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
}
.video-meta{
  display:flex;align-items:center;justify-content:space-between;
}
.video-size{
  font-family:'JetBrains Mono',monospace;
  font-size:.72rem;color:var(--muted);
  background:rgba(255,255,255,.04);
  padding:.2rem .5rem;border-radius:5px;
}
.video-date{font-size:.7rem;color:var(--muted)}
.card-actions{
  display:flex;gap:.5rem;margin-top:.75rem;
}
.act-btn{
  flex:1;
  padding:.45rem;
  border-radius:7px;
  border:1px solid var(--border);
  background:transparent;
  color:var(--muted);
  font-size:.75rem;cursor:pointer;
  transition:all .18s;
  display:flex;align-items:center;justify-content:center;gap:.3rem;
}
.act-btn:hover{background:rgba(255,60,60,.1);border-color:var(--accent);color:var(--accent)}
.act-btn.del:hover{background:rgba(231,76,60,.1);border-color:#e74c3c;color:#e74c3c}

.empty-state{
  grid-column:1/-1;
  text-align:center;padding:5rem 2rem;
  color:var(--muted);
}
.empty-state .big-icon{font-size:4rem;margin-bottom:1rem;opacity:.3}
.empty-state p{font-size:.9rem;line-height:1.6}

/* ── Toast ─────────────────────────────── */
#toast{
  position:fixed;bottom:2rem;right:2rem;
  background:var(--card);
  border:1px solid var(--border);
  border-radius:10px;
  padding:.85rem 1.25rem;
  font-size:.85rem;
  opacity:0;transform:translateY(10px);
  transition:all .3s;
  z-index:999;
  max-width:320px;
}
#toast.show{opacity:1;transform:translateY(0)}

/* ── Responsive ─────────────────────────────── */
@media(max-width:900px){
  main{grid-template-columns:1fr}
  aside{border-right:none;border-bottom:1px solid var(--border)}
}
@media(max-width:600px){
  header{padding:1.5rem}
  .logo-text h1{font-size:1.8rem}
}
</style>
</head>
<body>

<header>
  <div class="logo-mark">📥</div>
  <div class="logo-text">
    <h1>VaultTube</h1>
    <p>Local Video Dashboard</p>
  </div>
  <div class="header-stat">
    <span>Videos Saved</span>
    <strong id="total-count">—</strong>
  </div>
</header>

<main>
  <!-- Sidebar: downloader -->
  <aside>
    <div class="panel-title">Download New Video</div>

    <input class="url-input" id="url-input" type="url"
      placeholder="https://www.youtube.com/watch?v=..."/>

    <div>
      <div class="panel-title" style="margin-bottom:.6rem;font-size:.8rem">Quality</div>
      <div class="quality-grid" id="quality-grid">
        <button class="q-btn active" data-q="best">Best</button>
        <button class="q-btn" data-q="1080p">1080p</button>
        <button class="q-btn" data-q="720p">720p</button>
        <button class="q-btn" data-q="480p">480p</button>
        <button class="q-btn" data-q="audio">Audio</button>
      </div>
    </div>

    <button class="dl-btn" id="dl-btn" onclick="startDownload()">▶ Download</button>

    <div class="panel-title" style="margin-bottom:-.5rem">Active Downloads</div>
    <div class="progress-area" id="progress-area">
      <p style="font-size:.8rem;color:var(--muted)">No active downloads.</p>
    </div>
  </aside>

  <!-- Library -->
  <section class="library">
    <div class="lib-header">
      <h2>Your Library</h2>
      <button class="refresh-btn" onclick="loadVideos()">↻ Refresh</button>
    </div>
    <div class="video-grid" id="video-grid">
      <div class="empty-state"><div class="big-icon">🎬</div><p>Loading…</p></div>
    </div>
  </section>
</main>

<div id="toast"></div>

<script>
let selectedQuality = 'best';
const tasks = {}; // task_id → interval

// ── Quality selector ─────────────────────────
document.querySelectorAll('.q-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.q-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    selectedQuality = btn.dataset.q;
  });
});

// ── Toast ─────────────────────────────────────
function toast(msg, dur=3000) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), dur);
}

// ── Download ─────────────────────────────────
async function startDownload() {
  const url = document.getElementById('url-input').value.trim();
  if (!url) { toast('⚠️ Paste a YouTube URL first'); return; }

  const btn = document.getElementById('dl-btn');
  btn.disabled = true;
  btn.textContent = 'Starting…';

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, quality: selectedQuality }),
    });
    const data = await res.json();
    if (data.error) { toast('❌ ' + data.error); return; }
    addTaskCard(data.task_id, url);
    document.getElementById('url-input').value = '';
    toast('✅ Download queued!');
  } catch(e) {
    toast('❌ Request failed: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Download';
  }
}

// Allow Enter key
document.getElementById('url-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') startDownload();
});

// ── Task card ─────────────────────────────────
function addTaskCard(taskId, url) {
  const area = document.getElementById('progress-area');
  // Remove placeholder
  area.querySelectorAll('p').forEach(p => p.remove());

  const card = document.createElement('div');
  card.className = 'task-card';
  card.id = 'task-' + taskId;
  card.innerHTML = `
    <div class="task-url">${url}</div>
    <div class="task-status"><span class="dot queued"></span> <span class="status-text">Queued</span></div>
    <div class="prog-bar-bg"><div class="prog-bar-fill" style="width:0%"></div></div>
    <div class="task-filename"></div>
  `;
  area.prepend(card);

  // Poll
  const iv = setInterval(async () => {
    try {
      const res = await fetch('/api/status/' + taskId);
      const info = await res.json();
      updateTaskCard(taskId, info);
      if (info.status === 'done' || info.status === 'error') {
        clearInterval(iv);
        if (info.status === 'done') {
          toast('🎉 Download complete: ' + (info.filename || ''));
          setTimeout(loadVideos, 1000);
        } else {
          toast('❌ Download failed: ' + (info.error || 'Unknown error'));
        }
      }
    } catch(e) { clearInterval(iv); }
  }, 1000);
}

function updateTaskCard(taskId, info) {
  const card = document.getElementById('task-' + taskId);
  if (!card) return;
  const statusMap = { queued:'Queued', downloading:'Downloading…', done:'Complete ✓', error:'Failed ✗' };
  card.querySelector('.status-text').textContent = statusMap[info.status] || info.status;
  const dot = card.querySelector('.dot');
  dot.className = 'dot ' + info.status;
  card.querySelector('.prog-bar-fill').style.width = (info.progress || 0) + '%';
  if (info.filename) card.querySelector('.task-filename').textContent = info.filename;
  if (info.error) card.querySelector('.task-filename').textContent = '⚠ ' + info.error;
}

// ── Library ───────────────────────────────────
async function loadVideos() {
  try {
    const res = await fetch('/api/videos');
    const videos = await res.json();
    document.getElementById('total-count').textContent = videos.length;
    renderVideos(videos);
  } catch(e) {
    document.getElementById('video-grid').innerHTML =
      '<div class="empty-state"><div class="big-icon">⚠️</div><p>Could not load videos.</p></div>';
  }
}

function renderVideos(videos) {
  const grid = document.getElementById('video-grid');
  if (!videos.length) {
    grid.innerHTML = '<div class="empty-state"><div class="big-icon">🎬</div><p>No videos yet.<br>Download one to get started!</p></div>';
    return;
  }
  grid.innerHTML = videos.map((v, i) => `
    <div class="video-card" style="animation-delay:${i*0.04}s">
      <div class="video-thumb" id="thumb-${i}" onclick="playVideo(${i}, '${encodeURIComponent(v.name)}')">
        <span class="thumb-icon">▶</span>
        <video id="vid-${i}" src="/video/${encodeURIComponent(v.name)}" controls preload="none"></video>
      </div>
      <div class="video-info">
        <div class="video-name" title="${v.name}">${v.name}</div>
        <div class="video-meta">
          <span class="video-size">${v.size_mb} MB</span>
          <span class="video-date">${v.modified}</span>
        </div>
        <div class="card-actions">
          <button class="act-btn" onclick="window.open('/video/${encodeURIComponent(v.name)}','_blank')">⬇ Open</button>
          <button class="act-btn del" onclick="deleteVideo('${v.name}', this)">🗑 Delete</button>
        </div>
      </div>
    </div>
  `).join('');
}

function playVideo(i, encodedName) {
  const thumb = document.getElementById('thumb-' + i);
  const vid = document.getElementById('vid-' + i);
  if (thumb.classList.contains('playing')) return;
  thumb.classList.add('playing');
  vid.play();
}

async function deleteVideo(name, btn) {
  if (!confirm('Delete "' + name + '"?')) return;
  btn.textContent = '…';
  try {
    const res = await fetch('/api/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (data.ok) { toast('🗑 Deleted'); loadVideos(); }
    else toast('❌ ' + data.error);
  } catch(e) { toast('❌ Request failed'); }
}

// ── Init ──────────────────────────────────────
loadVideos();
setInterval(loadVideos, 30000); // auto-refresh every 30s
</script>
</body>
</html>"""


@app.route("/")
def index():
    return HTML


if __name__ == "__main__":
    init_firebase()
    print("=" * 50)
    print("  VaultTube Dashboard")
    print("  http://localhost:5000")
    print("  Videos saved to: {}".format(DOWNLOAD_DIR))
    print("  Device: {}".format(DEVICE_ID))
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
