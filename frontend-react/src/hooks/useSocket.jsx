import { createContext, useContext, useEffect, useState, useCallback, useRef } from 'react';
import { io } from 'socket.io-client';
import { useToast } from '@/components/ui/toast';

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

  const socketRef = useRef(null);
  const { addToast } = useToast();
  const toastRef = useRef(addToast);
  toastRef.current = addToast;

  useEffect(() => {
    const socket = io('http://localhost:5000', {  // SOCKET FIX — added polling fallback
      transports: ['websocket', 'polling'],
      autoConnect: true,
      reconnection: true,
      reconnectionAttempts: Infinity,  // DISCONNECT FIX: never stop trying
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 30000,  // DISCONNECT FIX: increased from 10s to 30s
      pingTimeout: 300000,  // DISCONNECT FIX: 5 min to match server
      pingInterval: 10000,  // DISCONNECT FIX: 10s to match server
    });
    socketRef.current = socket;

    socket.on('connect', () => {
      console.log('✅ Socket connected:', socket.id);  // SOCKET FIX — debug logging
      setConnected(true);
    });
    socket.on('disconnect', (reason) => {
      console.log('❌ Socket disconnected:', reason);  // DISCONNECT FIX: log disconnect reason
      setConnected(false);
    });
    socket.on('connect_error', (err) => {
      console.log('🔴 Connection error:', err.message);  // SOCKET FIX — debug logging
    });

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
        toastRef.current({
          type: 'success',
          title: 'Download Complete',
          description: `${data.title} — ${data.artist || ''}`,
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

    // MUSICBRAINZ — per-track tagging complete
    socket.on('tagging_complete', (data) => {
      // silent — no toast for every download, just updates state
    });

    return () => {
      clearInterval(keepAlive);  // DISCONNECT FIX
      socket.disconnect();
    };
  }, []);

  const requestStatus = useCallback(() => {
    socketRef.current?.emit('request_status');
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
        requestStatus,
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
