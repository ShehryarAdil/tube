# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**VaultTube** is a cross-device YouTube video downloader dashboard. Each device maintains its own local `youtube_downloads/` folder (platform-specific paths), while Firebase Firestore tracks download metadata and history across devices. The frontend is hosted on GitHub Pages and communicates with a local Flask backend (via localhost:5000) and Firebase for real-time status updates.

**Architecture:**
- **Backend:** Flask web server running locally on each device (`dashboard.py`)
- **Download Engine:** yt-dlp for YouTube video downloading
- **Cloud Sync:** Firebase Firestore for metadata storage (no file sync)
- **Frontend:** Static HTML/CSS/JS on GitHub Pages (`/docs/index.html`)
- **Storage:** Platform-specific local folders (Mac: `~/Library/Application Support/youtube_downloads/`, Windows: `%APPDATA%\Local\youtube_downloads/`, Linux: `~/.local/share/youtube_downloads/`)

## Setup & Development Commands

### Installation

```bash
# Clone the repository
git clone git@github.com:ShehryarAdil/tube.git
cd tube

# Install Python dependencies
pip install -r requirements.txt

# Copy environment template and fill in Firebase credentials
cp .env.example .env
# Then edit .env with your Firebase Service Account credentials
```

### Running the Project

**Local Backend:**
```bash
python dashboard.py
```
Starts Flask server at `http://localhost:5000`

**GitHub Pages Frontend:**
- Deployed automatically from `/docs` folder
- Access at: `https://shehryaradil.github.io/tube/`
- Must configure Firebase credentials in `docs/index.html` first

### Testing

Current manual testing (no automated test suite yet):
1. Start Flask: `python dashboard.py`
2. Open `http://localhost:5000` or GitHub Pages URL
3. Verify Flask backend and Firebase connection badges
4. Test download with a YouTube URL
5. Verify file appears in platform-specific `youtube_downloads/` folder
6. Check Firestore for metadata sync

### Firebase Setup

**Required once per project:**
1. Create Firebase project at https://console.firebase.google.com/
2. Enable Firestore Database
3. Create Service Account:
   - Go to Project Settings → Service Accounts
   - Click "Generate New Private Key"
   - Save JSON file
4. Fill in `.env` with values from the JSON:
   - `FIREBASE_PROJECT_ID`
   - `FIREBASE_PRIVATE_KEY_ID`
   - `FIREBASE_PRIVATE_KEY` (with literal `\n` for newlines)
   - `FIREBASE_CLIENT_EMAIL`
   - `FIREBASE_CLIENT_ID`
   - `FIREBASE_CERT_URL`

**For GitHub Pages frontend:** Update `docs/index.html` with your Firebase public config:
```javascript
const firebaseConfig = {
  apiKey: "YOUR_API_KEY_HERE",
  authDomain: "YOUR_PROJECT.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT.appspot.com",
  messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
  appId: "YOUR_APP_ID"
};
```

## Architecture

### High-Level Structure

**Backend (`dashboard.py`):**
- Platform detection and downloads directory setup (Windows/Mac/Linux)
- Flask routes for API endpoints (`/api/download`, `/api/status`, `/api/videos`, `/api/delete`, `/api/device`)
- yt-dlp subprocess management with real-time progress tracking
- Firebase sync functions (metadata writes on download state changes)
- Threading for concurrent downloads

**Data Storage:**
- **Local:** Videos in `youtube_downloads/` (platform-specific)
- **Cloud:** Metadata in Firestore (`/devices/{device_id}/downloads/{task_id}`)

**Frontend (`docs/index.html`):**
- Dual API integration: local Flask (downloads/deletion) + Firebase (real-time metadata)
- Quality selector (Best, 1080p, 720p, 480p, Audio)
- Download progress tracking via polling
- Video library grid with play/delete actions
- Connection status badge

### Key Files/Directories

- `dashboard.py` — Flask backend with yt-dlp integration and Firebase sync
- `.env` (user-created) — Firebase service account credentials (not committed)
- `.env.example` — Template for credentials
- `requirements.txt` — Python dependencies (Flask, yt-dlp, firebase-admin, python-dotenv)
- `docs/index.html` — GitHub Pages frontend (static HTML with Firebase Web SDK)
- `.gitignore` — Excludes `.env`, `youtube_downloads/`, and common IDE/Python files

## Important Notes

### Firebase Configuration

- Firebase SDK is initialized server-side in `dashboard.py` (backend writes metadata)
- Frontend uses Firebase Web SDK (read-only Firestore access via security rules)
- Service account credentials in `.env` should **never be committed** (in `.gitignore`)
- Frontend config in `docs/index.html` is public (API key is meant to be public; Firestore rules enforce security)

### Cross-Device Behavior

- Each device has its **own** isolated `youtube_downloads/` folder (no file sync)
- Firestore tracks metadata and download history for visibility across devices
- Graceful degradation: downloads work even if Firebase is unreachable
- Device ID defaults to hostname but can be overridden via `DEVICE_ID` env var

### Download Progress

- Real-time progress updates sent to Firestore (every 1-2 seconds during downloads)
- Frontend polls Firestore for status changes
- Download cleanup: deleted locally and from Firestore simultaneously

## External Resources

- **Firebase Console:** https://console.firebase.google.com/
- **yt-dlp Documentation:** https://github.com/yt-dlp/yt-dlp
- **GitHub Pages Setup:** https://docs.github.com/en/pages
- **Firestore Security Rules:** https://firebase.google.com/docs/firestore/security/start
