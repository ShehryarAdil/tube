# Cross-Device YouTube Downloader with Firebase — Design Spec

**Date:** 2026-05-23  
**Project:** VaultTube + Firebase Migration  
**Author:** Claude Code  
**Status:** Design Approved

---

## Overview

Migrate VaultTube (Flask YouTube downloader) from single-device to **cross-device support using Firebase Firestore** while keeping videos stored locally on each device.

**Goal:** Users can see download history across all their devices (Mac, Windows, Linux), manage downloads from a central GitHub Pages dashboard, and each device maintains its own `youtube_downloads/` folder with platform-aware paths.

---

## Architecture

### System Components

1. **Local Flask Backend** (each device)
   - Runs `dashboard.py` on localhost:5000
   - Executes yt-dlp downloads to platform-specific `youtube_downloads/` folder
   - Syncs download metadata to Firebase Firestore
   - Minimal changes to existing code

2. **Firebase Firestore** (cloud)
   - Stores device registry and download history
   - Real-time metadata for all devices
   - No video files stored (only local copies)

3. **GitHub Pages Frontend** (static)
   - Hosted on `github.com/ShehryarAdil/tube`
   - Communicates with local Flask API + Firebase Firestore
   - Responsive web app (desktop/tablet; mobile browser support)

4. **Environment Configuration**
   - `.env` file with Firebase credentials (user-provided)
   - `python-dotenv` loads credentials into Flask
   - GitHub Pages uses config injection or environment-specific config

### Data Flow

```
User (GitHub Pages)
  ↓
  ├→ Download Request → Local Flask (localhost:5000)
  │                          ↓
  │                    Run yt-dlp
  │                    Save to youtube_downloads/
  │                    Write metadata to Firestore
  │                          ↓
  │                    Return task_id
  │
  └→ Status Updates ← Firestore (real-time listener)
```

### Device Architecture

Each device operates independently:
- Downloads stored in local `youtube_downloads/` folder
- Flask backend can be started/stopped independently
- Firestore provides cross-device visibility (optional)
- No files synced between devices (local-only storage)

---

## Backend Implementation (Flask)

### 1. Platform-Specific Download Directory

Replace hardcoded `DOWNLOAD_DIR = Path("downloads")` with:

```python
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
```

**Result:**
- macOS: `~/Library/Application Support/youtube_downloads/`
- Windows: `C:\Users\{username}\AppData\Local\youtube_downloads/`
- Linux: `~/.local/share/youtube_downloads/`

### 2. Firebase Initialization

Add to `dashboard.py`:

```python
import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv

load_dotenv()

# Initialize Firebase with credentials from environment
firebase_config = {
    "type": "service_account",
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace("\\n", "\n"),
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "client_id": os.getenv("FIREBASE_CLIENT_ID"),
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}

cred = credentials.Certificate(firebase_config)
firebase_admin.initialize_app(cred)
db = firestore.client()

DEVICE_ID = os.getenv("DEVICE_ID", socket.gethostname())  # Use hostname or env var
```

### 3. Firestore Metadata Schema

```
/devices/{device_id}/
  ├── name: string (e.g., "MacBook-Pro")
  ├── last_seen: timestamp
  └── /downloads/{download_id}/
      ├── url: string (YouTube URL)
      ├── filename: string (final video filename)
      ├── size_mb: number
      ├── status: string ("queued" | "downloading" | "done" | "error")
      ├── progress: number (0-100)
      ├── timestamp: timestamp (when download started)
      ├── completed_at: timestamp (when download finished, null if not done)
      └── error: string (null or error message)
```

### 4. Firebase Sync Functions

Add helper functions to sync download state:

```python
def sync_download_to_firestore(task_id, status, filename=None, progress=0, error=None):
    """Write download metadata to Firestore"""
    doc_ref = db.collection('devices').document(DEVICE_ID).collection('downloads').document(task_id)
    
    data = {
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

def register_device():
    """Register device in Firestore on startup"""
    doc_ref = db.collection('devices').document(DEVICE_ID)
    doc_ref.set({
        "name": DEVICE_ID,
        "last_seen": firestore.SERVER_TIMESTAMP,
    }, merge=True)
```

### 5. Integrate Sync into Existing Code

Modify `run_download()` function to call `sync_download_to_firestore()`:
- When status changes to "downloading"
- When progress updates
- When status changes to "done" or "error"

Keep existing Flask API endpoints (`/api/download`, `/api/status`, `/api/videos`, `/api/delete`) unchanged. They continue to work for local access.

---

## Frontend Implementation (GitHub Pages)

### 1. Extract HTML/CSS/JS

Move embedded HTML from `dashboard.py` to GitHub Pages:
- Create `/docs/index.html` with dashboard UI (copy from current HTML in dashboard.py)
- Separate CSS/JS if needed
- Commit to `tube` repo

### 2. Dual API Integration

Frontend makes two types of calls:

```javascript
// 1. Local Flask API (for downloads, deletion)
fetch('http://localhost:5000/api/download', {
  method: 'POST',
  body: JSON.stringify({ url, quality })
})

// 2. Firebase Firestore (for metadata/history)
import { initializeApp } from "https://www.gstatic.com/firebasejs/latest/firebase-app.js"
import { getFirestore, collection, query, where, onSnapshot } from "https://www.gstatic.com/firebasejs/latest/firebase-firestore.js"

const firebaseConfig = {
  apiKey: env.FIREBASE_API_KEY,
  projectId: env.FIREBASE_PROJECT_ID,
  // ... other config
}

const app = initializeApp(firebaseConfig)
const db = getFirestore(app)

// Listen to downloads from current device
const deviceId = localStorage.getItem('deviceId') || 'unknown'
const q = query(
  collection(db, 'devices', deviceId, 'downloads')
)
onSnapshot(q, snapshot => {
  // Update UI with real-time status
})
```

### 3. Configuration

Firebase config passed to frontend via:
- Option A: `docs/config.json` (committed to repo, public)
- Option B: Environment variables injected by GitHub Actions
- Option A recommended: Firebase API key is meant to be public; Firestore rules secure the data

### 4. Device Identifier

- Frontend stores `deviceId` in localStorage
- On first visit, uses device hostname (via Flask endpoint) or user selects device name
- Used to query/filter downloads in Firestore

---

## Environment Configuration (.env)

User creates `.env` file in project root with Firebase credentials:

```
# Firebase Service Account
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_PRIVATE_KEY_ID=xxx
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
FIREBASE_CLIENT_EMAIL=firebase-adminsdk-xxx@your-project.iam.gserviceaccount.com
FIREBASE_CLIENT_ID=xxx

# Device Identifier (optional; defaults to hostname)
DEVICE_ID=MacBook-Pro

# Local Flask
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=True
```

**Note:** User gets these from Firebase Console → Project Settings → Service Accounts → Generate Key.

---

## Deployment

### Local Setup (Each Device)

1. Clone repo: `git clone git@github.com:ShehryarAdil/tube.git`
2. Install dependencies: `pip install flask yt-dlp firebase-admin python-dotenv`
3. Create `.env` file with Firebase credentials
4. Run: `python dashboard.py`
5. Access dashboard at `http://localhost:5000` OR GitHub Pages URL (if using GitHub Pages frontend)

### GitHub Pages Setup

1. Create `/docs` folder in repo
2. Move frontend (index.html + assets) to `/docs`
3. Enable GitHub Pages in repo settings (deploy from `/docs`)
4. GitHub Pages hosted at `https://shehryaradil.github.io/tube/`
5. Frontend communicates with:
   - Local Flask at `http://localhost:5000` (for downloads)
   - Firebase Firestore (for metadata, real-time updates)

---

## Error Handling & Edge Cases

### Flask Backend
- Firebase connection fails → Log error, continue local operations (graceful degradation)
- Firestore sync fails → Retry on next status update, don't block download
- Device name collision → Use hostname + timestamp

### Frontend
- Local Flask unreachable → Show "Connect to local machine" message
- Firebase not configured → Show Firebase setup guide
- No network → Display cached data from localStorage

---

## Testing

### Manual Testing Checklist
- [ ] Flask starts, registers device in Firestore
- [ ] Download starts and syncs progress to Firestore
- [ ] Videos save to correct platform-specific folder
- [ ] GitHub Pages frontend loads and connects to Flask
- [ ] Firestore updates appear in real-time in frontend
- [ ] Delete locally and in Firestore syncs correctly
- [ ] Open dashboard on second device, see first device's downloads

### Scope Not Included (Future)
- Mobile native app
- Video sync across devices
- Automatic device registration
- Offline-first sync

---

## Files Modified/Created

**Modified:**
- `dashboard.py` — Add Firebase init, platform paths, sync functions

**Created:**
- `.env.example` — Template for Firebase credentials
- `/docs/index.html` — GitHub Pages frontend
- `/docs/config.json` — Firebase public config (if using Option A)
- `requirements.txt` — Add firebase-admin, python-dotenv

**No Changes:**
- Video download logic (yt-dlp execution stays the same)
- Flask API endpoints (work as-is)

---

## Success Criteria

✅ Each device stores videos in platform-specific `youtube_downloads/` folder  
✅ Firebase tracks download history and metadata  
✅ GitHub Pages frontend displays downloads and syncs in real-time  
✅ Local Flask backend unaffected (downloads work the same)  
✅ Cross-device visibility without syncing files  
✅ Environment-based Firebase configuration  

---
