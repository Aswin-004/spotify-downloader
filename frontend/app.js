/**
 * Spotify Meta Downloader - Frontend
 * Supports single tracks and full albums with real-time progress
 */

let isDownloading = false;
let pollInterval = null;

document.addEventListener("DOMContentLoaded", () => {
    const fetchBtn = document.getElementById("fetchBtn");
    const spotifyInput = document.getElementById("spotifyInput");

    if (fetchBtn) fetchBtn.addEventListener("click", fetchMetadata);
    if (spotifyInput) {
        spotifyInput.focus();
        spotifyInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") fetchMetadata();
        });
    }
});

// ── Fetch metadata (track or album) ──────────────────────────

async function fetchMetadata() {
    const url = document.getElementById("spotifyInput").value.trim();
    if (!url) return alert("Please paste a Spotify URL");

    const fetchBtn = document.getElementById("fetchBtn");
    fetchBtn.disabled = true;
    fetchBtn.textContent = "Loading...";

    try {
        const res = await fetch("/api/track", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || `HTTP ${res.status}`);
        }

        const data = await res.json();

        if (data.type === "album") {
            displayAlbum(data);
        } else {
            displayTrack(data);
        }
    } catch (err) {
        alert("Failed to fetch: " + err.message);
    } finally {
        fetchBtn.disabled = false;
        fetchBtn.textContent = "Fetch";
    }
}

// ── Display single track ─────────────────────────────────────

function displayTrack(data) {
    const resultDiv = document.getElementById("result");
    const minutes = Math.floor((data.duration || 0) / 60);
    const seconds = String((data.duration || 0) % 60).padStart(2, "0");

    resultDiv.innerHTML = `
        <h3>${esc(data.title)}</h3>
        <p><strong>${esc(data.artist)}</strong></p>
        <p>${esc(data.album)} &bull; ${minutes}:${seconds}</p>
        <button id="downloadBtn" class="download-btn">\u2B07\uFE0F DOWNLOAD</button>
        <div class="progress-bar"><div id="progressFill" class="progress-fill"></div></div>
        <p id="progressText">Ready</p>
    `;
    resultDiv.classList.remove("hidden");

    document.getElementById("downloadBtn").addEventListener("click", () => {
        startDownload();
    });
}

// ── Display album ────────────────────────────────────────────

function displayAlbum(data) {
    const resultDiv = document.getElementById("result");
    const trackRows = data.tracks
        .map((t, i) => {
            const m = Math.floor((t.duration || 0) / 60);
            const s = String((t.duration || 0) % 60).padStart(2, "0");
            return `<div class="track"><span class="track-num">${i + 1}</span><span class="track-info"><span class="track-title">${esc(t.title)}</span><span class="track-artist">${esc(t.artist)}</span></span><span class="track-dur">${m}:${s}</span></div>`;
        })
        .join("");

    resultDiv.innerHTML = `
        <h3>${esc(data.name)}</h3>
        <p><strong>${esc(data.artist)}</strong></p>
        <p>${data.total_tracks} tracks</p>
        <div class="track-list">${trackRows}</div>
        <button id="downloadAll" class="download-btn">\u2B07\uFE0F DOWNLOAD ALBUM</button>
        <div class="progress-bar"><div id="progressFill" class="progress-fill"></div></div>
        <p id="progressText">Ready</p>
    `;
    resultDiv.classList.remove("hidden");

    document.getElementById("downloadAll").addEventListener("click", () => {
        startDownload();
    });
}

// ── Start download (works for both track and album) ──────────

async function startDownload() {
    if (isDownloading) return;
    isDownloading = true;

    const url = document.getElementById("spotifyInput").value.trim();
    const btn = document.getElementById("downloadBtn") || document.getElementById("downloadAll");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Downloading...";
    }

    try {
        const res = await fetch("/api/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });

        if (res.status === 202) {
            startPolling();
        } else {
            throw new Error("Download request failed");
        }
    } catch (err) {
        alert("Download failed: " + err.message);
        isDownloading = false;
        if (btn) {
            btn.disabled = false;
            btn.textContent = btn.id === "downloadAll" ? "\u2B07\uFE0F DOWNLOAD ALBUM" : "\u2B07\uFE0F DOWNLOAD";
        }
    }
}

// ── Real-time polling (1 second) ─────────────────────────────

function startPolling() {
    pollInterval = setInterval(async () => {
        try {
            const res = await fetch("/api/status");
            const data = await res.json();

            const fill = document.getElementById("progressFill");
            const text = document.getElementById("progressText");

            if (fill) fill.style.width = data.progress + "%";
            if (text) text.textContent = data.current || data.status;

            if (data.status === "completed") {
                clearInterval(pollInterval);
                pollInterval = null;
                isDownloading = false;
                if (fill) fill.style.width = "100%";
                if (text) text.textContent = "\u2705 " + (data.current || "Download complete!");
                showCompletion();
                resetButton();
            } else if (data.status === "failed") {
                clearInterval(pollInterval);
                pollInterval = null;
                isDownloading = false;
                if (text) text.textContent = "\u274C Download failed";
                resetButton();
            } else if (data.status === "fallback") {
                clearInterval(pollInterval);
                pollInterval = null;
                isDownloading = false;
                if (text) text.textContent = "\u26A0\uFE0F " + (data.current || "Fallback needed");
                resetButton();
            }
        } catch (err) {
            console.error("Polling error:", err);
        }
    }, 1000);
}

function resetButton() {
    const btn = document.getElementById("downloadBtn") || document.getElementById("downloadAll");
    if (btn) {
        btn.disabled = false;
        btn.textContent = btn.id === "downloadAll" ? "\u2B07\uFE0F DOWNLOAD ALBUM" : "\u2B07\uFE0F DOWNLOAD";
    }
}

function showCompletion() {
    const existing = document.getElementById("completionMsg");
    if (existing) existing.remove();
    const msg = document.createElement("div");
    msg.id = "completionMsg";
    msg.className = "completion-msg";
    msg.textContent = "Download completed. Check your music folder.";
    const result = document.getElementById("result");
    if (result) result.appendChild(msg);
}

// ── Helpers ──────────────────────────────────────────────────

function esc(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
}
