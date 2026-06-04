// PRODUCTION DEPLOY — prefix all API calls with the backend base URL.
// In development (Vite proxy): VITE_API_BASE_URL is empty → relative URLs work as-is.
// In production (Vercel + Render): set VITE_API_BASE_URL=https://your-app.onrender.com
const _BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

// True on Vercel+Render. Downloads are blocked there by YouTube's datacenter
// IP ban — no bypass works. Treat this as a local-only feature permanently.
export const IS_CLOUD = !!import.meta.env.VITE_API_BASE_URL;

function apiFetch(path, opts) {
  return globalThis.fetch(`${_BASE}${path}`, opts);
}

async function handleResponse(res) {
  const data = await res.json();
  if (!res.ok) {
    const error = new Error(data.error || `HTTP ${res.status}`);
    error.status = res.status;
    error.data = data;
    throw error;
  }
  return data;
}

export const api = {
  fetchMetadata(url) {
    return apiFetch('/api/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }).then(handleResponse);
  },

  startDownload(url) {
    return apiFetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
  },

  // Cloud streaming download — file goes directly to user's ~/Downloads folder
  downloadStream(url) {
    return apiFetch('/api/download-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
  },

  getFiles() {
    return apiFetch('/api/files').then(handleResponse);
  },

  getHistory() {
    return apiFetch('/api/history').then(handleResponse);
  },

  clearHistory() {
    return apiFetch('/api/history/clear', { method: 'POST' }).then(handleResponse);
  },

  getAutoStatus() {
    return apiFetch('/api/auto-status').then(handleResponse);
  },

  getQueueStatus() {
    return apiFetch('/api/queue-status').then(handleResponse);
  },

  getApiUsage() {
    return apiFetch('/api/api-usage').then(handleResponse);
  },

  getIngestConfig() {
    return apiFetch('/api/ingest-config').then(handleResponse);
  },

  setIngestPlaylist(playlistId) {
    return apiFetch('/api/ingest-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: playlistId }),
    }).then(handleResponse);
  },

  retagCatchallTrack(filepath) {
    return apiFetch('/api/retag-catchall-track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filepath }),
    }).then(handleResponse);
  },

  getCatchallTracks() {
    return apiFetch('/api/catchall-tracks').then(handleResponse);
  },

  getGeminiQuota() {
    return apiFetch('/api/gemini-quota').then(handleResponse);
  },

  stopSync() {
    return apiFetch('/api/stop-sync', { method: 'POST' }).then(handleResponse);
  },

  refreshPlaylist(options = {}) {
    // Optional per-trigger folder override. When present, every track in the
    // sync is pinned to Ingest/{forceFolder}/ regardless of artist routing.
    // Optional force_redownload flag bypasses history + registry dedup so
    // previously-downloaded tracks actually land in the new folder.
    const body = {};
    const forceFolder = (options.forceFolder || '').trim();
    if (forceFolder) body.force_folder = forceFolder;
    // Only set force_redownload if explicitly passed as true
    if (options.forceRedownload === true) {
      body.force_redownload = true;
    }
    return apiFetch('/api/refresh-playlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(handleResponse);
  },

  clearHistoryForPlaylist: async (playlistId = null) => {
    const body = playlistId ? { playlist_id: playlistId } : {};
    return apiFetch('/api/clear-history-for-playlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(handleResponse);
  },

  deleteFile(filename) {
    return apiFetch(`/api/delete/${encodeURIComponent(filename)}`, {
      method: 'DELETE',
    }).then(handleResponse);
  },

  getHealth() {
    return apiFetch('/api/health').then(handleResponse);
  },

  // MUSICBRAINZ — Retag library
  retagLibrary() {
    return apiFetch('/api/library/retag', { method: 'POST' }).then(handleResponse);
  },

  // MUSICBRAINZ — Get retag status
  getRetagStatus() {
    return apiFetch('/api/library/retag/status').then(handleResponse);
  },

  // ANALYTICS — Analytics dashboard endpoints
  getAnalyticsOverview() { // ANALYTICS
    return apiFetch('/api/analytics/overview').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsDownloadsPerDay(days = 30) { // ANALYTICS
    return apiFetch(`/api/analytics/downloads-per-day?days=${days}`).then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsTopArtists(limit = 10) { // ANALYTICS
    return apiFetch(`/api/analytics/top-artists?limit=${limit}`).then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsSourceBreakdown() { // ANALYTICS
    return apiFetch('/api/analytics/source-breakdown').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsTaggingBreakdown() { // ANALYTICS
    return apiFetch('/api/analytics/tagging-breakdown').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsRecent() { // ANALYTICS
    return apiFetch('/api/analytics/recent').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsFailed() { // ANALYTICS
    return apiFetch('/api/analytics/failed').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  retryDownload(trackId) { // ANALYTICS
    return apiFetch('/api/download/retry', { // ANALYTICS
      method: 'POST', // ANALYTICS
      headers: { 'Content-Type': 'application/json' }, // ANALYTICS
      body: JSON.stringify({ track_id: trackId }), // ANALYTICS
    }).then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getCacheAnalytics() { // ANALYTICS
    return apiFetch('/api/cache-analytics').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getTaggingFailuresSummary() { // ANALYTICS
    return apiFetch('/api/tagging-failures/summary').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getDownloadHistoryStats() { // ANALYTICS
    return apiFetch('/api/download-history/stats').then(handleResponse); // ANALYTICS
  }, // ANALYTICS

  // FILE ORGANIZER — Batch organize library
  organize(options = {}) {
    const mode = options.mode || 'artist';
    return apiFetch('/api/library/organize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }).then(handleResponse);
  },

  // FILE ORGANIZER — Organize recently downloaded files (last N hours)
  organizeRecent(options = {}) {
    const mode = options.mode || 'artist';
    const hours = options.hours || 24;
    return apiFetch('/api/library/organize-recent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, hours }),
    }).then(handleResponse);
  },

  runMaintenance(task, opts = {}) {
    return apiFetch('/api/maintenance/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task, ...opts }),
    }).then(handleResponse);
  },

  getMaintenanceStatus() {
    return apiFetch('/api/maintenance/status').then(handleResponse);
  },

  // APP CONFIG — all user-configurable settings
  getAppConfig() {
    return apiFetch('/api/settings/app-config').then(handleResponse);
  },

  saveAppConfig(data) {
    return apiFetch('/api/settings/app-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }).then(handleResponse);
  },

  // SKIPPED TRACKS — permanently failed tracks
  getSkippedTracks() {
    return apiFetch('/api/skipped-tracks').then(handleResponse);
  },

  resetSkippedTracks(trackId = null) {
    return apiFetch('/api/skipped-tracks/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(trackId ? { track_id: trackId } : {}),
    }).then(handleResponse);
  },

  clearGenreCache() {
    return apiFetch('/api/clear-genre-cache', { method: 'POST' }).then(handleResponse);
  },

  moveAndRemember(filepath, genre, artist = '') {
    return apiFetch('/api/move-and-remember', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filepath, genre, artist }),
    }).then(handleResponse);
  },

  getGenreOverrides() {
    return apiFetch('/api/genre-overrides').then(handleResponse);
  },

  deleteGenreOverride(artistKey) {
    return apiFetch(`/api/genre-override/${encodeURIComponent(artistKey)}`, {
      method: 'DELETE',
    }).then(handleResponse);
  },

  // NOTIFICATIONS
  getNotificationStatus() {
    return apiFetch('/api/notifications/status').then(handleResponse);
  },

  testNotification() {
    return apiFetch('/api/notifications/test', { method: 'POST' }).then(handleResponse);
  },

  // SETTINGS — custom folder mappings
  scanFolders() {
    return apiFetch('/api/settings/scan-folders').then(handleResponse);
  },

  getCustomFolders() {
    return apiFetch('/api/settings/custom-folders').then(handleResponse);
  },

  saveCustomFolder(folderName, genreLabel) {
    return apiFetch('/api/settings/custom-folders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_name: folderName, genre_label: genreLabel }),
    }).then(handleResponse);
  },

  deleteCustomFolder(folderName) {
    return apiFetch(`/api/settings/custom-folders/${encodeURIComponent(folderName)}`, {
      method: 'DELETE',
    }).then(handleResponse);
  },

  updateTrackBpm(filename, bpm, key = null) {
    return apiFetch('/api/track/bpm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, bpm, key }),
    }).then(handleResponse);
  },

  previewTrackUrl(filename) {
    return `${_BASE}/api/preview-track?filename=${encodeURIComponent(filename)}`;
  },

  previewTrackByPath(relativePath) {
    return `${_BASE}/api/preview-track?path=${encodeURIComponent(relativePath)}`;
  },

  getFolderTags(folder) {
    return apiFetch(`/api/files/folder-tags?folder=${encodeURIComponent(folder)}`).then(handleResponse);
  },

  updateTrackTags(path, { artist, title }) {
    return apiFetch('/api/track/tags', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, artist, title }),
    }).then(handleResponse);
  },

  getDuplicates() {
    return apiFetch('/api/duplicates').then(handleResponse);
  },

  deleteDuplicate(filename) {
    return apiFetch('/api/duplicates/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename }),
    }).then(handleResponse);
  },

  keepDuplicate(filename, genre) {
    return apiFetch('/api/duplicates/keep', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, genre }),
    }).then(handleResponse);
  },

  rekordboxExportUrl(folder = 'all', name = '') {
    const params = new URLSearchParams({ folder });
    if (name) params.set('name', name);
    return `${_BASE}/api/export/rekordbox?${params.toString()}`;
  },
};
