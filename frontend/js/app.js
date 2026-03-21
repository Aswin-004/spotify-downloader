/**
 * Spotify Meta Downloader - Dashboard Frontend
 * WebSocket-powered real-time updates via Socket.IO
 */

let isDownloading = false;
let socket = null;

// ═══════════════════════ INIT ═══════════════════════

document.addEventListener("DOMContentLoaded", () => {
    initSocket();
    initTabs();
    initSearch();
    loadDownloads();
    loadHistory();
    loadIngestConfig();
    startRateLimitPolling();
});

// ═══════════════════════ WEBSOCKET ═══════════════════════

function initSocket() {
    socket = io({ transports: ["polling", "websocket"] });

    const dot = document.getElementById("connectionDot");
    const text = document.getElementById("connectionText");

    socket.on("connect", () => {
        dot.className = "connection-dot connected";
        text.textContent = "Connected";
    });

    socket.on("disconnect", () => {
        dot.className = "connection-dot disconnected";
        text.textContent = "Disconnected";
    });

    socket.on("status_update", (data) => {
        if (data.download) updateDownloadStatus(data.download);
        if (data.auto) updateAutoStatus(data.auto);
        if (data.history) updateHistory(data.history);
    });

    socket.on("files_list", (files) => {
        renderFiles(files);
    });

    socket.on("queue_status", (data) => {
        updateQueuePanel(data);
    });
}

// ═══════════════════════ TABS ═══════════════════════

function initTabs() {
    document.querySelectorAll(".nav-item").forEach(item => {
        item.addEventListener("click", (e) => {
            e.preventDefault();
            const tab = item.dataset.tab;
            if (!tab) return;

            document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
            item.classList.add("active");

            document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
            const target = document.getElementById("tab-" + tab);
            if (target) target.classList.add("active");

            // Refresh data on tab switch
            if (tab === "files") loadDownloads();
            if (tab === "history") loadHistory();
        });
    });
}

// ═══════════════════════ SEARCH & FETCH ═══════════════════════

function initSearch() {
    const fetchBtn = document.getElementById("fetchBtn");
    const input = document.getElementById("spotifyInput");

    if (fetchBtn) fetchBtn.addEventListener("click", fetchMetadata);
    if (input) {
        input.focus();
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") fetchMetadata();
        });
    }

    const clearBtn = document.getElementById("clearHistoryBtn");
    if (clearBtn) clearBtn.addEventListener("click", clearHistory);

    const retryBtn = document.getElementById("retryBtn");
    if (retryBtn) retryBtn.addEventListener("click", () => {
        retryBtn.classList.add("hidden");
        startDownload();
    });

    const dismissBtn = document.getElementById("rateLimitDismiss");
    if (dismissBtn) dismissBtn.addEventListener("click", () => {
        document.getElementById("rateLimitBanner").classList.add("hidden");
    });
}

async function fetchMetadata() {
    const url = document.getElementById("spotifyInput").value.trim();
    if (!url) return alert("Please paste a Spotify URL");

    const fetchBtn = document.getElementById("fetchBtn");
    const spinner = document.getElementById("fetchSpinner");
    const btnText = document.getElementById("fetchBtnText");
    fetchBtn.disabled = true;
    if (spinner) spinner.classList.remove("hidden");
    if (btnText) btnText.textContent = "Loading...";

    try {
        const res = await fetch("/api/track", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });

        if (!res.ok) {
            const err = await res.json();
            if (err.error_type === "RATE_LIMIT") {
                showRateLimitBanner(err.error);
                return;
            }
            throw new Error(err.error || "HTTP " + res.status);
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
        if (spinner) spinner.classList.add("hidden");
        if (btnText) btnText.textContent = "Fetch";
    }
}

// ═══════════════════════ DISPLAY METADATA ═══════════════════════

function displayTrack(data) {
    const resultDiv = document.getElementById("result");
    const m = Math.floor((data.duration || 0) / 60);
    const s = String((data.duration || 0) % 60).padStart(2, "0");
    const cacheBadge = data.source === "cache"
        ? '<span class="cache-badge from-cache">From cache</span>'
        : '<span class="cache-badge from-spotify">From Spotify</span>';

    resultDiv.innerHTML =
        '<h3>' + esc(data.title) + cacheBadge + '</h3>' +
        '<p class="meta"><strong>' + esc(data.artist) + '</strong></p>' +
        '<p class="meta">' + esc(data.album) + ' &bull; ' + m + ':' + s + '</p>' +
        '<button id="downloadBtn" class="download-btn">DOWNLOAD</button>';
    resultDiv.classList.remove("hidden");

    document.getElementById("downloadBtn").addEventListener("click", startDownload);
}

function displayAlbum(data) {
    const resultDiv = document.getElementById("result");
    const cacheBadge = data.source === "cache"
        ? '<span class="cache-badge from-cache">From cache</span>'
        : '<span class="cache-badge from-spotify">From Spotify</span>';
    let rows = "";
    for (let i = 0; i < data.tracks.length; i++) {
        const t = data.tracks[i];
        const m = Math.floor((t.duration || 0) / 60);
        const s = String((t.duration || 0) % 60).padStart(2, "0");
        rows += '<div class="track"><span class="track-num">' + (i + 1) +
            '</span><span class="track-info"><span class="track-title">' + esc(t.title) +
            '</span><span class="track-artist">' + esc(t.artist) +
            '</span></span><span class="track-dur">' + m + ':' + s + '</span></div>';
    }

    resultDiv.innerHTML =
        '<h3>' + esc(data.name) + cacheBadge + '</h3>' +
        '<p class="meta"><strong>' + esc(data.artist) + '</strong></p>' +
        '<p class="meta">' + data.total_tracks + ' tracks</p>' +
        '<div class="track-list">' + rows + '</div>' +
        '<button id="downloadAll" class="download-btn">DOWNLOAD ALL</button>';
    resultDiv.classList.remove("hidden");

    document.getElementById("downloadAll").addEventListener("click", startDownload);
}

// ═══════════════════════ DOWNLOAD ═══════════════════════

async function startDownload() {
    if (isDownloading) return;
    isDownloading = true;

    const url = document.getElementById("spotifyInput").value.trim();
    const btn = document.getElementById("downloadBtn") || document.getElementById("downloadAll");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Downloading...";
    }

    // Show progress card
    const card = document.getElementById("activeDownload");
    card.classList.remove("hidden");
    updateProgressCard({ status: "starting", progress: 0, current: "Starting..." });

    try {
        const res = await fetch("/api/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });

        if (res.status !== 202) {
            throw new Error("Download request failed");
        }
        // Progress updates come via WebSocket
    } catch (err) {
        alert("Download failed: " + err.message);
        isDownloading = false;
        card.classList.add("hidden");
        if (btn) {
            btn.disabled = false;
            btn.textContent = btn.id === "downloadAll" ? "DOWNLOAD ALL" : "DOWNLOAD";
        }
    }
}

// ═══════════════════════ STATUS UPDATES ═══════════════════════

function updateDownloadStatus(data) {
    const card = document.getElementById("activeDownload");

    if (data.status === "idle") {
        // No active download
        if (!isDownloading) card.classList.add("hidden");
        return;
    }

    if (data.status === "starting" || data.status === "downloading") {
        card.classList.remove("hidden");
        updateProgressCard(data);
    } else if (data.status === "completed") {
        updateProgressCard(data);
        isDownloading = false;
        resetDownloadButton();
        showCompletion();
        loadDownloads();
        // Hide progress card after delay
        setTimeout(() => card.classList.add("hidden"), 4000);
    } else if (data.status === "failed" || data.status === "fallback") {
        updateProgressCard(data);
        isDownloading = false;
        resetDownloadButton();
        // Show retry button for failures
        const retryBtn = document.getElementById("retryBtn");
        if (retryBtn) retryBtn.classList.remove("hidden");
        setTimeout(() => card.classList.add("hidden"), 8000);
    }
}

function updateProgressCard(data) {
    const fill = document.getElementById("dpFill");
    const percent = document.getElementById("dpPercent");
    const detail = document.getElementById("dpDetail");
    const title = document.getElementById("dpTitle");

    if (fill) fill.style.width = (data.progress || 0) + "%";
    if (percent) percent.textContent = (data.progress || 0) + "%";
    if (detail) detail.textContent = data.current || data.status || "";

    if (title) {
        if (data.status === "completed") {
            let badge = "";
            const mq = data.match_quality;
            if (mq === "exact") badge = ' <span class="accuracy-badge exact">\u2714 Exact match</span>';
            else if (mq === "approx") badge = ' <span class="accuracy-badge approx">\u26A0 Approx match</span>';
            else if (mq === "fallback") badge = ' <span class="accuracy-badge fallback">\u2716 Fallback</span>';
            title.innerHTML = "Complete!" + badge;
        }
        else if (data.status === "failed") title.textContent = "Failed";
        else if (data.status === "fallback") title.innerHTML = 'Manual download needed <span class="accuracy-badge fallback">\u2716 Fallback</span>';
        else {
            // Enhanced status text based on current activity
            const cur = (data.current || "").toLowerCase();
            if (cur.includes("retrying") || cur.includes("retry")) {
                title.textContent = "Retrying...";
            } else if (cur.includes("fallback") || cur.includes("soundcloud")) {
                title.textContent = "Fallback source...";
            } else if (cur.includes("searching") || cur.includes("matching") || data.progress <= 10) {
                title.textContent = "Matching...";
            } else {
                title.textContent = "Downloading...";
            }
        }
    }
}

function resetDownloadButton() {
    const btn = document.getElementById("downloadBtn") || document.getElementById("downloadAll");
    if (btn) {
        btn.disabled = false;
        btn.textContent = btn.id === "downloadAll" ? "DOWNLOAD ALL" : "DOWNLOAD";
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
    if (result && !result.classList.contains("hidden")) result.appendChild(msg);
}

// ═══════════════════════ AUTO DOWNLOADER ═══════════════════════

function updateAutoStatus(data) {
    const dot = document.getElementById("autoDot");
    const statusText = document.getElementById("autoStatusText");
    const progressFill = document.getElementById("autoProgressFill");
    const progressLabel = document.getElementById("autoProgressLabel");
    const currentTrack = document.getElementById("autoCurrentTrack");

    const isActive = data.status === "downloading" || data.status === "checking";

    if (dot) {
        dot.classList.toggle("active", isActive);
    }

    if (statusText) {
        statusText.textContent = capitalize(data.status || "idle");
    }

    if (progressFill && data.total > 0) {
        const pct = Math.round((data.completed / data.total) * 100);
        progressFill.style.width = pct + "%";
    } else if (progressFill) {
        progressFill.style.width = "0%";
    }

    if (progressLabel) {
        progressLabel.textContent = (data.completed || 0) + "/" + (data.total || 0);
    }

    if (currentTrack) {
        currentTrack.textContent = data.current || "";
    }

    // Extended auto-downloader info
    const lastChecked = document.getElementById("autoLastChecked");
    const playlistInfo = document.getElementById("autoPlaylistInfo");
    if (lastChecked) {
        lastChecked.textContent = data.last_checked ? "Last: " + data.last_checked : "";
    }
    if (playlistInfo) {
        if (data.playlist_total > 0) {
            playlistInfo.textContent = data.synced_total + "/" + data.playlist_total + " synced";
        } else {
            playlistInfo.textContent = "";
        }
    }
}

// ═══════════════════════ HISTORY ═══════════════════════

function updateHistory(items) {
    const container = document.getElementById("historyList");
    if (!container) return;

    if (!items || items.length === 0) {
        container.innerHTML = '<p class="empty-state">No downloads yet</p>';
        return;
    }

    let html = "";
    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        const statusClass = item.status === "success" ? "success" :
            item.status === "skipped" ? "skipped" : "failed";
        html += '<div class="history-item">' +
            '<span class="history-status ' + statusClass + '"></span>' +
            '<div class="history-info">' +
            '<div class="history-title">' + esc(item.title || "") + '</div>' +
            '<div class="history-artist">' + esc(item.artist || "") + '</div>' +
            '</div>' +
            '<span class="history-time">' + esc(item.timestamp || "") + '</span>' +
            '</div>';
    }
    container.innerHTML = html;
}

async function loadHistory() {
    try {
        const res = await fetch("/api/history");
        const data = await res.json();
        if (data.history) updateHistory(data.history);
    } catch (e) {
        console.log("loadHistory error:", e);
    }
}

async function clearHistory() {
    try {
        await fetch("/api/history/clear", { method: "POST" });
        updateHistory([]);
    } catch (e) {
        console.log("clearHistory error:", e);
    }
}

// ═══════════════════════ FILES ═══════════════════════

function renderFiles(files) {
    const container = document.getElementById("filesList");
    if (!container) return;

    if (!files || files.length === 0) {
        container.innerHTML = '<p class="empty-state">No files yet</p>';
        return;
    }

    // Group by folder
    const groups = {};
    for (let i = 0; i < files.length; i++) {
        const f = files[i];
        const folder = f.folder || "Root";
        if (!groups[folder]) groups[folder] = [];
        groups[folder].push(f);
    }

    let html = '<div class="files-count">' + files.length + ' files</div>';
    const folderNames = Object.keys(groups).sort();
    for (let g = 0; g < folderNames.length; g++) {
        const folder = folderNames[g];
        const items = groups[folder];
        html += '<div class="file-group">';
        html += '<div class="file-group-header">' + esc(folder) + ' <span class="file-group-count">(' + items.length + ')</span></div>';
        for (let i = 0; i < items.length; i++) {
            html += '<div class="file-item">' +
                '<span class="file-icon">&#9835;</span>' +
                '<span class="file-name">' + esc(items[i].name) + '</span>' +
                '</div>';
        }
        html += '</div>';
    }
    container.innerHTML = html;
}

async function loadDownloads() {
    try {
        const res = await fetch("/api/files");
        const data = await res.json();
        renderFiles(data.files || []);
    } catch (e) {
        console.log("loadDownloads error:", e);
    }
}

// ═══════════════════════ HELPERS ═══════════════════════

function esc(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
}

function capitalize(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
}

// ═══════════════════════ QUEUE PANEL ═══════════════════════

function updateQueuePanel(data) {
    const total = document.getElementById("queueTotal");
    const completed = document.getElementById("queueCompleted");
    const workers = document.getElementById("queueWorkers");
    const current = document.getElementById("queueCurrent");

    if (total) total.textContent = data.total || 0;
    if (completed) completed.textContent = data.completed || 0;
    if (workers) workers.textContent = data.active_workers || 0;
    if (current) current.textContent = data.current || "";
}

// ═══════════════════════ RATE LIMIT BANNER ═══════════════════════

let _rateLimitPollTimer = null;

function startRateLimitPolling() {
    pollRateLimitStatus();
    _rateLimitPollTimer = setInterval(pollRateLimitStatus, 5000);
}

async function pollRateLimitStatus() {
    try {
        const res = await fetch("/api/api-usage");
        const data = await res.json();
        const banner = document.getElementById("rateLimitBanner");
        const text = banner ? banner.querySelector(".rate-limit-text") : null;
        const fetchBtn = document.getElementById("fetchBtn");
        const downloadBtn = document.getElementById("downloadBtn");
        if (!banner) return;
        if (data.is_rate_limited && data.cooldown_remaining > 0) {
            const hours = Math.floor(data.cooldown_remaining / 3600);
            const mins = Math.floor((data.cooldown_remaining % 3600) / 60);
            let timeStr;
            if (hours > 0) {
                timeStr = mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
            } else {
                const secs = data.cooldown_remaining % 60;
                timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
            }
            if (text) text.textContent = `\u26A0 Spotify API blocked. Retry in ${timeStr}.`;
            banner.classList.remove("hidden");
            if (fetchBtn) fetchBtn.disabled = true;
            if (downloadBtn) downloadBtn.disabled = true;
        } else {
            banner.classList.add("hidden");
            if (fetchBtn) fetchBtn.disabled = false;
            if (downloadBtn) downloadBtn.disabled = false;
        }
    } catch (e) {
        // Server unreachable, ignore
    }
}

function showRateLimitBanner(message) {
    const banner = document.getElementById("rateLimitBanner");
    const text = banner ? banner.querySelector(".rate-limit-text") : null;
    if (banner) {
        if (text && message) text.textContent = message;
        banner.classList.remove("hidden");
    }
}

// ═══════════════════════ INGEST CONFIG ═══════════════════════

async function loadIngestConfig() {
    try {
        const res = await fetch("/api/ingest-config");
        const data = await res.json();
        const status = document.getElementById("ingestStatus");
        if (status) {
            status.textContent = data.enabled
                ? "Active — " + data.playlist_id
                : "Not configured";
        }
    } catch (e) {
        console.log("loadIngestConfig error:", e);
    }
}
