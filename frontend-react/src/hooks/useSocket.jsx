import { createContext, useContext, useEffect, useState, useCallback, useRef } from 'react';
import { io } from 'socket.io-client';

const SocketContext = createContext(null);

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

  const socketRef = useRef(null);

  useEffect(() => {
    const socket = io({
      transports: ['polling', 'websocket'],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionAttempts: Infinity,
    });
    socketRef.current = socket;

    socket.on('connect', () => setConnected(true));
    socket.on('disconnect', () => setConnected(false));

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

    return () => {
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
