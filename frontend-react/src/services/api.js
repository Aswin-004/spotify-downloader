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

  refreshPlaylist() {
    return fetch('/api/refresh-playlist', { method: 'POST' }).then(handleResponse);
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
};
