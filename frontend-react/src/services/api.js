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
    return fetch('/api/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }).then(handleResponse);
  },

  startDownload(url) {
    return fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
  },

  getFiles() {
    return fetch('/api/files').then(handleResponse);
  },

  getHistory() {
    return fetch('/api/history').then(handleResponse);
  },

  clearHistory() {
    return fetch('/api/history/clear', { method: 'POST' }).then(handleResponse);
  },

  getAutoStatus() {
    return fetch('/api/auto-status').then(handleResponse);
  },

  getQueueStatus() {
    return fetch('/api/queue-status').then(handleResponse);
  },

  getApiUsage() {
    return fetch('/api/api-usage').then(handleResponse);
  },

  getIngestConfig() {
    return fetch('/api/ingest-config').then(handleResponse);
  },

  setIngestPlaylist(playlistId) {
    return fetch('/api/ingest-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: playlistId }),
    }).then(handleResponse);
  },

  retagCatchallTrack(filepath) {
    return fetch('/api/retag-catchall-track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filepath }),
    }).then(handleResponse);
  },

  getCatchallTracks() {
    return fetch('/api/catchall-tracks').then(handleResponse);
  },

  getGeminiQuota() {
    return fetch('/api/gemini-quota').then(handleResponse);
  },

  stopSync() {
    return fetch('/api/stop-sync', { method: 'POST' }).then(handleResponse);
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
    return fetch('/api/refresh-playlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(handleResponse);
  },

  clearHistoryForPlaylist: async (playlistId = null) => {
    const body = playlistId ? { playlist_id: playlistId } : {};
    return fetch('/api/clear-history-for-playlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(handleResponse);
  },

  deleteFile(filename) {
    return fetch(`/api/delete/${encodeURIComponent(filename)}`, {
      method: 'DELETE',
    }).then(handleResponse);
  },

  getHealth() {
    return fetch('/api/health').then(handleResponse);
  },

  // MUSICBRAINZ — Retag library
  retagLibrary() {
    return fetch('/api/library/retag', { method: 'POST' }).then(handleResponse);
  },

  // MUSICBRAINZ — Get retag status
  getRetagStatus() {
    return fetch('/api/library/retag/status').then(handleResponse);
  },

  // ANALYTICS — Analytics dashboard endpoints
  getAnalyticsOverview() { // ANALYTICS
    return fetch('/api/analytics/overview').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsDownloadsPerDay(days = 30) { // ANALYTICS
    return fetch(`/api/analytics/downloads-per-day?days=${days}`).then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsTopArtists(limit = 10) { // ANALYTICS
    return fetch(`/api/analytics/top-artists?limit=${limit}`).then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsSourceBreakdown() { // ANALYTICS
    return fetch('/api/analytics/source-breakdown').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsTaggingBreakdown() { // ANALYTICS
    return fetch('/api/analytics/tagging-breakdown').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsRecent() { // ANALYTICS
    return fetch('/api/analytics/recent').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getAnalyticsFailed() { // ANALYTICS
    return fetch('/api/analytics/failed').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  retryDownload(trackId) { // ANALYTICS
    return fetch('/api/download/retry', { // ANALYTICS
      method: 'POST', // ANALYTICS
      headers: { 'Content-Type': 'application/json' }, // ANALYTICS
      body: JSON.stringify({ track_id: trackId }), // ANALYTICS
    }).then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getCacheAnalytics() { // ANALYTICS
    return fetch('/api/cache-analytics').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getTaggingFailuresSummary() { // ANALYTICS
    return fetch('/api/tagging-failures/summary').then(handleResponse); // ANALYTICS
  }, // ANALYTICS
  getDownloadHistoryStats() { // ANALYTICS
    return fetch('/api/download-history/stats').then(handleResponse); // ANALYTICS
  }, // ANALYTICS

  // FILE ORGANIZER — Batch organize library
  organize(options = {}) {
    const mode = options.mode || 'artist';
    return fetch('/api/library/organize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }).then(handleResponse);
  },

  // FILE ORGANIZER — Organize recently downloaded files (last N hours)
  organizeRecent(options = {}) {
    const mode = options.mode || 'artist';
    const hours = options.hours || 24;
    return fetch('/api/library/organize-recent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, hours }),
    }).then(handleResponse);
  },

  runMaintenance(task, opts = {}) {
    return fetch('/api/maintenance/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task, ...opts }),
    }).then(handleResponse);
  },

  getMaintenanceStatus() {
    return fetch('/api/maintenance/status').then(handleResponse);
  },

  // APP CONFIG — all user-configurable settings
  getAppConfig() {
    return fetch('/api/settings/app-config').then(handleResponse);
  },

  saveAppConfig(data) {
    return fetch('/api/settings/app-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }).then(handleResponse);
  },

  // SKIPPED TRACKS — permanently failed tracks
  getSkippedTracks() {
    return fetch('/api/skipped-tracks').then(handleResponse);
  },

  resetSkippedTracks(trackId = null) {
    return fetch('/api/skipped-tracks/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(trackId ? { track_id: trackId } : {}),
    }).then(handleResponse);
  },

  clearGenreCache() {
    return fetch('/api/clear-genre-cache', { method: 'POST' }).then(handleResponse);
  },

  // NOTIFICATIONS
  getNotificationStatus() {
    return fetch('/api/notifications/status').then(handleResponse);
  },

  testNotification() {
    return fetch('/api/notifications/test', { method: 'POST' }).then(handleResponse);
  },

  // SETTINGS — custom folder mappings
  scanFolders() {
    return fetch('/api/settings/scan-folders').then(handleResponse);
  },

  getCustomFolders() {
    return fetch('/api/settings/custom-folders').then(handleResponse);
  },

  saveCustomFolder(folderName, genreLabel) {
    return fetch('/api/settings/custom-folders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_name: folderName, genre_label: genreLabel }),
    }).then(handleResponse);
  },

  deleteCustomFolder(folderName) {
    return fetch(`/api/settings/custom-folders/${encodeURIComponent(folderName)}`, {
      method: 'DELETE',
    }).then(handleResponse);
  },
};
