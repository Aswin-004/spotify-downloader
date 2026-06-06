import { createContext, useContext, useEffect, useState, useCallback, useRef } from 'react';
import { io } from 'socket.io-client';
import { useToast } from '@/components/ui/toast';
import { api } from '@/services/api';

const SocketContext = createContext(null);

const EMPTY_DOWNLOADS = {
  downloading: {},
  completed: {},
  skipped: {},
  failed: {},
};

export function SocketProvider({ children }) {
  const [connected, setConnected] = useState(false);
  const [downloadStatus, setDownloadStatus] = useState({
    status: 'idle',
    progress: 0,
    current: '',
    match_quality: '',
  });
  const [autoStatus, setAutoStatus] = useState({
    status: 'idle',
    total: 0,
    completed: 0,
    current: '',
    last_checked: '',
    playlist_total: 0,
    synced_total: 0,
  });
  const [history, setHistory] = useState([]);
  const [files, setFiles] = useState([]);
  const [queueStatus, setQueueStatus] = useState({
    total: 0,
    completed: 0,
    active_workers: 0,
    current: '',
  });
  const [ingestProgress, setIngestProgress] = useState({
    active: false,
    current: 0,
    total: 0,
    percent: 0,
    currentTrack: '',
    currentArtist: '',
  });
  const [downloads, setDownloads] = useState(EMPTY_DOWNLOADS);
  const [retagProgress, setRetagProgress] = useState(null); // MUSICBRAINZ
  const [needsReviewItems, setNeedsReviewItems] = useState([]);
  const [maintenanceLogs, setMaintenanceLogs] = useState([]);
  const [maintenanceRunning, setMaintenanceRunning] = useState(false);

  const socketRef = useRef(null);
  const { addToast } = useToast();
  const toastRef = useRef(addToast);
  toastRef.current = addToast;

  useEffect(() => {
    // PRODUCTION DEPLOY — connect to the Render backend URL in production.
    // VITE_SOCKET_URL is empty in dev (same-origin via Vite proxy).
    // In production: set VITE_SOCKET_URL=https://your-app.onrender.com
    const _socketUrl = (import.meta.env.VITE_SOCKET_URL || '').replace(/\/$/, '') || undefined;

    // PRODUCTION DEPLOY — in production (gevent worker) WebSocket upgrades work;
    // locally (threading + Werkzeug) they don't. VITE_SOCKET_TRANSPORTS lets
    // the deployment control this without a code change.
    // Default: polling only (safe for all environments).
    // Production override: set VITE_SOCKET_TRANSPORTS=polling,websocket
    const _transports = import.meta.env.VITE_SOCKET_TRANSPORTS
      ? import.meta.env.VITE_SOCKET_TRANSPORTS.split(',').map((t) => t.trim())
      : ['polling'];

    const socket = io(_socketUrl, {
      transports: _transports,
      autoConnect: true,
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 30000,
      pingTimeout: 300000,
      pingInterval: 10000,
    });
    socketRef.current = socket;

    socket.on('connect', () => {
      setConnected(true);
      // Reload catch-all list from disk so a page refresh never loses the queue
      api.getCatchallTracks().then((items) => {
        if (!items?.length) return;
        setNeedsReviewItems((prev) => {
          const existingIds = new Set(prev.map((i) => i.id || `${i.title}|${i.artist}`));
          const fresh = items.filter((i) => !existingIds.has(i.id || `${i.title}|${i.artist}`));
          return [...prev, ...fresh];
        });
      }).catch(() => {});
    });
    socket.on('disconnect', (reason) => {
      setConnected(false);
    });
    socket.on('connect_error', () => {});

    socket.on('status_update', (data) => {
      if (data.download) setDownloadStatus(data.download);
      if (data.auto) setAutoStatus(data.auto);
      if (data.history) setHistory(data.history);
    });

    socket.on('files_list', (filesList) => {
      setFiles(filesList || []);
    });

    socket.on('queue_status', (data) => {
      setQueueStatus(data);
    });

    // ── Real-time ingest events ──
    socket.on('auto_status_update', (data) => {
      setAutoStatus(data);
      if (data.status === 'idle' || data.status === 'completed') {
        setIngestProgress((prev) => ({ ...prev, active: false }));
      }
    });

    // Lightweight 1s heartbeat from backend — keeps the auto-status panel
    // current between discrete `auto_status_update` events. Fires regardless
    // of whether a sync is active, so we don't touch ingestProgress here.
    socket.on('auto_status', (data) => {
      setAutoStatus(data);
    });

    socket.on('download_start', (data) => {
      if (data.source === 'ingest') {
        setIngestProgress((prev) => ({
          ...prev,
          active: true,
          currentTrack: data.title,
          currentArtist: data.artist,
        }));
        setDownloads((prev) => ({
          ...prev,
          downloading: {
            ...prev.downloading,
            [data.title]: {
              title: data.title,
              artist: data.artist,
              progress: 0,
              status: 'downloading',
              timestamp: new Date().toLocaleTimeString(),
            },
          },
        }));
      }
    });

    socket.on('download_progress', (data) => {
      if (data.source === 'ingest') {
        setIngestProgress((prev) => ({
          ...prev,
          current: data.current,
          total: data.total,
          percent: Math.round(Number(data.percent) || 0),
        }));
      }
    });

    // Per-track real-time download progress (yt-dlp percentage)
    socket.on('download_track_progress', (data) => {
      if (data.source === 'ingest') {
        setDownloads((prev) => {
          const item = prev.downloading[data.title];
          if (!item) return prev;
          return {
            ...prev,
            downloading: {
              ...prev.downloading,
              [data.title]: {
                ...item,
                progress: Math.round(Number(data.percent) || 0),
              },
            },
          };
        });
      }
    });

    socket.on('download_complete', (data) => {
      if (data.source === 'ingest') {
        const _meta = [
          data.routing_label,
          data.bpm ? `${data.bpm} BPM` : null,
          data.camelot || null,
        ].filter(Boolean).join(' · ');
        toastRef.current({
          type: 'success',
          title: 'Download Complete',
          description: _meta ? `${data.title} — ${_meta}` : `${data.title} — ${data.artist || ''}`,
        });
        setDownloads((prev) => {
          const updated = { ...prev.downloading };
          delete updated[data.title];
          return {
            ...prev,
            downloading: updated,
            completed: {
              ...prev.completed,
              [data.title]: {
                title: data.title,
                artist: data.artist,
                filename: data.filename || '',
                progress: 100,
                status: 'completed',
                timestamp: new Date().toLocaleTimeString(),
                folder: data.folder || '',
                routing_label: data.routing_label || '',
                bpm: data.bpm || null,
                key: data.key || null,
                camelot: data.camelot || null,
                energy: data.energy || null,
              },
            },
          };
        });
      }
    });

    socket.on('download_skipped', (data) => {
      if (data.source === 'ingest') {
        toastRef.current({
          type: 'warning',
          title: 'Track Skipped',
          description: `${data.title} — ${data.reason || 'Duplicate'}`,
          duration: 3000,
        });
        setDownloads((prev) => ({
          ...prev,
          skipped: {
            ...prev.skipped,
            [data.title]: {
              title: data.title,
              artist: data.artist || '',
              reason: data.reason || 'Duplicate',
              status: 'skipped',
              timestamp: new Date().toLocaleTimeString(),
            },
          },
        }));
      }
    });

    socket.on('download_error', (data) => {
      if (data.source === 'ingest') {
        toastRef.current({
          type: 'error',
          title: 'Download Failed',
          description: `${data.title} — ${data.error || 'Unknown error'}`,
          duration: 5000,
        });
        setDownloads((prev) => {
          const updated = { ...prev.downloading };
          delete updated[data.title];
          return {
            ...prev,
            downloading: updated,
            failed: {
              ...prev.failed,
              [data.title]: {
                title: data.title,
                artist: data.artist || '',
                error: data.error || 'Unknown error',
                status: 'failed',
                timestamp: new Date().toLocaleTimeString(),
              },
            },
          };
        });
      }
    });

    // DISCONNECT FIX: keepalive ping every 15s to prevent server-side timeout
    const keepAlive = setInterval(() => {
      if (socket.connected) {
        socket.emit('ping_keepalive');
      }
    }, 15000);  // DISCONNECT FIX

    // Celery dedup: track is already queued or downloading
    socket.on('download_duplicate', (data) => {
      toastRef.current({
        type: 'warning',
        title: 'Already downloading',
        description: data.message || `${data.title} is already in the queue.`,
        duration: 4000,
      });
    });

    // AI rescued an unknown genre — routed automatically, no action needed
    socket.on('download_auto_classified', (data) => {
      const folder = (data.folder || '').replace('Library/', '');
      toastRef.current({
        type: 'success',
        title: 'Auto-classified by AI',
        description: `${data.title} · ${data.artist} → ${folder}`,
        duration: 5000,
      });
    });

    // Unknown genre — AI unavailable, song landed in Electronic catch-all
    socket.on('download_needs_review', (data) => {
      const conf = Math.round((data.confidence || 0) * 100);
      toastRef.current({
        type: 'warning',
        title: 'Genre unknown — saved to Electronic',
        description: `${data.title} · ${data.artist} (${conf}% confidence) — check Review Queue to fix`,
        duration: 8000,
      });
      setNeedsReviewItems((prev) => {
        const key = `${data.title}|${data.artist}`;
        if (prev.some((i) => `${i.title}|${i.artist}` === key)) return prev;
        return [{ ...data, timestamp: new Date().toLocaleTimeString() }, ...prev.slice(0, 49)];
      });
    });

    // MUSICBRAINZ — retag progress listener
    socket.on('retag_progress', (data) => {
      setRetagProgress(data);
      if (data.status === 'complete') {
        toastRef.current({
          type: 'success',
          title: 'Library Retag Complete',
          description: `Tagged ${data.tagged ?? 0} files, ${data.failed ?? 0} failed`,
          duration: 6000,
        });
      }
    });

    socket.on('maintenance_log', (data) => {
      setMaintenanceLogs((prev) => {
        const next = [...prev, data];
        return next.length > 1000 ? next.slice(-1000) : next;
      });
      if (data.done) setMaintenanceRunning(false);
      else setMaintenanceRunning(true);
    });

    return () => {
      clearInterval(keepAlive);
      socket.removeAllListeners();
      socket.disconnect();
    };
  }, []);

  const requestStatus = useCallback(() => {
    socketRef.current?.emit('request_status');
  }, []);

  const clearNeedsReviewItem = useCallback((title) => {
    setNeedsReviewItems((prev) => prev.filter((item) => item.title !== title));
  }, []);

  const clearDownloads = useCallback((bucket) => {
    setDownloads((prev) => ({ ...prev, [bucket]: {} }));
  }, []);

  const _TASK_LABELS = {
    backfill_gemini: 'Re-classify Undetected Genres',
    backfill_lastfm: 'Enrich Genre & Mood Tags',
    organise:        'Re-sort Music into Folders',
    repair_index:    'Fix Library Scan',
  };

  const startMaintenance = useCallback((task) => {
    const label = _TASK_LABELS[task] || task;
    // Preserve other tasks' logs — only reset the current task's slot
    setMaintenanceLogs(prev => [
      ...prev.filter(e => e.task !== task),
      { task, line: `Starting ${label}…`, done: false },
    ]);
    setMaintenanceRunning(true);
  }, []);

  const stopMaintenance = useCallback(() => {
    setMaintenanceRunning(false);
  }, []);

  // taskId = clear only that task's logs; omit = clear all
  const clearMaintenanceLogs = useCallback((taskId) => {
    setMaintenanceLogs(prev => taskId ? prev.filter(e => e.task !== taskId) : []);
  }, []);

  return (
    <SocketContext.Provider
      value={{
        connected,
        downloadStatus,
        autoStatus,
        history,
        files,
        queueStatus,
        ingestProgress,
        downloads,
        retagProgress, // MUSICBRAINZ
        needsReviewItems,
        needsReviewCount: needsReviewItems.length,
        clearNeedsReviewItem,
        clearDownloads,
        requestStatus,
        maintenanceLogs,
        maintenanceRunning,
        startMaintenance,
        stopMaintenance,
        clearMaintenanceLogs,
      }}
    >
      {children}
    </SocketContext.Provider>
  );
}

export function useSocket() {
  const ctx = useContext(SocketContext);
  if (!ctx) throw new Error('useSocket must be inside SocketProvider');
  return ctx;
}
