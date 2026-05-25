import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, RefreshCw, RotateCcw, Menu, FolderInput, X, Settings2, CheckCircle2, StopCircle } from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export default function Header({ onMenuToggle }) {
  const { connected, autoStatus } = useSocket();
  const [open, setOpen] = useState(false);
  const [forceFolder, setForceFolder] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const panelRef = useRef(null);
  const inputRef = useRef(null);

  // Two-click confirmation for destructive clear action
  const [confirmClear, setConfirmClear] = useState(false);

  // Auto-reset confirm state after 4 seconds
  useEffect(() => {
    if (!confirmClear) return;
    const t = setTimeout(() => setConfirmClear(false), 4000);
    return () => clearTimeout(t);
  }, [confirmClear]);

  // Playlist config popover
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [playlistInput, setPlaylistInput] = useState('');
  const [playlistConfig, setPlaylistConfig] = useState(null); // { enabled, playlist_id }
  const [savingPlaylist, setSavingPlaylist] = useState(false);
  const [playlistError, setPlaylistError] = useState('');
  const settingsPanelRef = useRef(null);
  const playlistInputRef = useRef(null);

  // Rate-limit status derived from auto status (pushed via socket)
  const rateLimited =
    autoStatus.current?.toLowerCase().includes('rate limited') ||
    (autoStatus.status === 'idle' &&
      autoStatus.current?.toLowerCase().includes('cooldown'));

  // Autofocus when panel opens
  useEffect(() => {
    if (open) {
      // small delay so focus lands after the motion mount
      const t = setTimeout(() => inputRef.current?.focus(), 80);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Close on outside click / Escape
  useEffect(() => {
    if (!open) return;
    function onClick(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Load playlist config when settings popover opens
  useEffect(() => {
    if (!settingsOpen) return;
    const t = setTimeout(() => playlistInputRef.current?.focus(), 80);
    api.getIngestConfig().then(setPlaylistConfig).catch(() => {});
    return () => clearTimeout(t);
  }, [settingsOpen]);

  // Close settings on outside click / Escape
  useEffect(() => {
    if (!settingsOpen) return;
    function onClick(e) {
      if (settingsPanelRef.current && !settingsPanelRef.current.contains(e.target)) {
        setSettingsOpen(false);
      }
    }
    function onKey(e) {
      if (e.key === 'Escape') setSettingsOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [settingsOpen]);

  async function savePlaylist() {
    if (savingPlaylist) return;
    setSavingPlaylist(true);
    setPlaylistError('');
    try {
      const result = await api.setIngestPlaylist(playlistInput.trim());
      setPlaylistConfig({ enabled: true, playlist_id: result.playlist_id });
      setPlaylistInput('');
    } catch (err) {
      setPlaylistError(err.message || 'Failed to save');
    } finally {
      setSavingPlaylist(false);
    }
  }

  async function triggerRefresh() {
    if (submitting) return;
    setSubmitting(true);
    try {
      // If empty, api.refreshPlaylist omits force_folder from the body — backend
      // falls back to normal artist-based routing.
      // force_redownload is now an explicit opt-in via the Clear & Retry button only.
      const trimmed = forceFolder.trim();
      await api.refreshPlaylist({
        forceFolder: trimmed,
        forceRedownload: false,
      });
      setOpen(false);
      setForceFolder('');
    } catch (_) {
      // Swallow; toast/feedback handled elsewhere in the app
    } finally {
      setSubmitting(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') triggerRefresh();
  }

  async function handleClearAndRefresh() {
    setConfirmClear(false);
    setSubmitting(true);
    try {
      await api.clearHistoryForPlaylist();
      const trimmed = forceFolder.trim();
      await api.refreshPlaylist({ forceFolder: trimmed, forceRedownload: false });
      setOpen(false);
      setForceFolder('');
    } catch (_) {
      // swallow; feedback handled elsewhere
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <header className="sticky top-0 z-30 flex items-center justify-between h-14 px-6 border-b border-border bg-background/80 backdrop-blur-xl">
      <div className="flex items-center gap-3">
        <button
          onClick={onMenuToggle}
          className="lg:hidden p-2 text-gray-400 hover:text-white"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>

      <div className="flex items-center gap-3">
        {/* Connection status dot */}
        <div
          title={connected ? 'Connected' : 'Disconnected — reconnecting…'}
          className={`w-2 h-2 rounded-full transition-colors ${
            connected ? 'bg-emerald-400' : 'bg-red-400 animate-pulse'
          }`}
        />

        {rateLimited && (
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-warning-muted border border-amber-500/20"
          >
            <AlertTriangle className="w-4 h-4 text-amber-400" />
            <span className="text-xs text-amber-400">Rate limited</span>
          </motion.div>
        )}

        {/* Stop Sync button — visible while a sync is actively downloading */}
        <AnimatePresence>
          {autoStatus.status === 'downloading' && (
            <motion.div
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.15 }}
            >
              <Button
                variant="ghost"
                size="sm"
                onClick={() => api.stopSync().catch(() => {})}
                className="text-red-400 hover:text-red-300 hover:bg-red-500/10 gap-1.5"
                title="Stop the current sync"
              >
                <StopCircle className="w-4 h-4" />
                <span className="text-xs hidden sm:inline">Stop</span>
              </Button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Playlist config popover */}
        <div className="relative" ref={settingsPanelRef}>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSettingsOpen((v) => !v)}
            title="Configure ingest playlist"
            aria-expanded={settingsOpen}
            aria-haspopup="dialog"
          >
            <Settings2 className="w-4 h-4" />
          </Button>

          <AnimatePresence>
            {settingsOpen && (
              <motion.div
                role="dialog"
                aria-label="Ingest playlist configuration"
                initial={{ opacity: 0, y: -8, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -8, scale: 0.96 }}
                transition={{ duration: 0.18, ease: 'easeOut' }}
                className="absolute right-0 top-full mt-2 w-80 origin-top-right glass rounded-2xl border border-white/10 shadow-xl p-4 z-40"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Settings2 className="w-4 h-4 text-primary" />
                    <h3 className="text-sm font-semibold">Ingest Playlist</h3>
                  </div>
                  <button
                    onClick={() => setSettingsOpen(false)}
                    className="text-gray-500 hover:text-white transition-colors"
                    aria-label="Close"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                {/* Current status */}
                {playlistConfig && (
                  <div className={`flex items-center gap-2 mb-3 text-xs px-2 py-1.5 rounded-lg ${
                    playlistConfig.enabled
                      ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                      : 'bg-amber-500/10 border border-amber-500/20 text-amber-400'
                  }`}>
                    {playlistConfig.enabled ? (
                      <>
                        <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />
                        <span className="font-mono truncate">{playlistConfig.playlist_id}</span>
                      </>
                    ) : (
                      <>
                        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
                        <span>No playlist configured</span>
                      </>
                    )}
                  </div>
                )}

                <label
                  htmlFor="playlist-url-input"
                  className="block text-xs font-medium text-gray-300 mb-1.5"
                >
                  Spotify playlist URL or ID
                </label>
                <Input
                  id="playlist-url-input"
                  ref={playlistInputRef}
                  value={playlistInput}
                  onChange={(e) => setPlaylistInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && savePlaylist()}
                  placeholder="https://open.spotify.com/playlist/..."
                  className="h-9 bg-background/60 border-white/10 text-sm"
                  disabled={savingPlaylist}
                />
                {playlistError && (
                  <p className="mt-1.5 text-[11px] text-red-400">{playlistError}</p>
                )}
                <p className="mt-1.5 text-[11px] text-gray-500">
                  Paste a full URL or bare playlist ID — takes effect immediately, no restart needed.
                </p>

                <div className="mt-4 flex justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSettingsOpen(false)}
                    disabled={savingPlaylist}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={savePlaylist}
                    disabled={savingPlaylist || !playlistInput.trim()}
                  >
                    Save
                  </Button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Refresh trigger + force_folder modal */}
        <div className="relative" ref={panelRef}>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setOpen((v) => !v)}
            title="Refresh playlist"
            aria-expanded={open}
            aria-haspopup="dialog"
          >
            <RefreshCw
              className={`w-4 h-4 ${submitting ? 'animate-spin' : ''}`}
            />
          </Button>

          <AnimatePresence>
            {open && (
              <>
                {/* Backdrop */}
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="fixed inset-0 bg-black/70 backdrop-blur-sm z-40"
                  onClick={() => { setOpen(false); setConfirmClear(false); }}
                />

                {/* Centered modal */}
                <motion.div
                  role="dialog"
                  aria-label="Refresh playlist options"
                  aria-modal="true"
                  initial={{ opacity: 0, scale: 0.95, y: -12 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95, y: -12 }}
                  transition={{ duration: 0.2, ease: 'easeOut' }}
                  className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[calc(100vw-2rem)] max-w-sm glass rounded-2xl shadow-2xl p-5 z-50"
                >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <FolderInput className="w-4 h-4 text-primary" />
                    <h3 className="text-sm font-semibold">Refresh playlist</h3>
                  </div>
                  <button
                    onClick={() => { setOpen(false); setConfirmClear(false); }}
                    className="p-1 text-gray-500 hover:text-white active:scale-95 transition-all duration-150 cursor-pointer rounded-lg hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20"
                    aria-label="Close dialog"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <label
                  htmlFor="force-folder-input"
                  className="block text-xs font-medium text-gray-300 mb-1.5"
                >
                  Save all to folder (optional)
                </label>
                <Input
                  id="force-folder-input"
                  ref={inputRef}
                  value={forceFolder}
                  onChange={(e) => setForceFolder(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Sammy Virji"
                  className="h-9 bg-background/60 border-white/10 text-sm"
                  disabled={submitting}
                />
                <p className="mt-1.5 text-[11px] text-gray-500">
                  Leave empty to auto-route by artist
                </p>

                {/* Clear history action */}
                <div className="mt-3 border-t border-white/5 pt-3">
                  <AnimatePresence mode="wait">
                    {confirmClear ? (
                      <motion.div
                        key="confirm"
                        initial={{ opacity: 0, y: -4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -4 }}
                        transition={{ duration: 0.15 }}
                        className="flex items-start gap-2.5 p-2.5 rounded-xl bg-red-500/10 border border-red-500/20"
                      >
                        <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                        <div className="flex-1 min-w-0">
                          <p className="text-xs text-red-300 font-medium">Clears all download history</p>
                          <p className="text-[10px] text-red-400/70 mt-0.5">Every track will re-download on next sync.</p>
                        </div>
                        <button
                          onClick={handleClearAndRefresh}
                          disabled={submitting}
                          aria-label="Confirm clear history and re-download all"
                          className="shrink-0 text-xs px-2.5 py-1 rounded-lg bg-red-500 hover:bg-red-400 active:scale-95 text-white font-medium transition-all duration-150 cursor-pointer disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/60"
                        >
                          Confirm
                        </button>
                      </motion.div>
                    ) : (
                      <motion.button
                        key="clear-btn"
                        initial={{ opacity: 0, y: -4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -4 }}
                        transition={{ duration: 0.15 }}
                        onClick={() => setConfirmClear(true)}
                        disabled={submitting}
                        aria-label="Clear download history and re-download all tracks"
                        className="w-full flex items-center justify-center gap-1.5 text-xs py-2 px-3 rounded-xl border border-amber-500/20 text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 active:scale-95 transition-all duration-150 cursor-pointer disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/40"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                        Clear history &amp; re-download all
                      </motion.button>
                    )}
                  </AnimatePresence>
                </div>

                <div className="mt-4 flex justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => { setOpen(false); setConfirmClear(false); }}
                    disabled={submitting}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={triggerRefresh}
                    disabled={submitting}
                    className="active:scale-95 transition-all duration-150"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${submitting ? 'animate-spin' : ''}`} />
                    {submitting ? 'Refreshing…' : 'Refresh'}
                  </Button>
                </div>
              </motion.div>
              </>
            )}
          </AnimatePresence>
        </div>
      </div>
    </header>
  );
}
