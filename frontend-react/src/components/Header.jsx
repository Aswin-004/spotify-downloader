import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  AlertTriangle, RefreshCw, RotateCcw, Menu, FolderInput,
  X, Settings2, CheckCircle2, StopCircle, ChevronLeft, ChevronRight,
  Search, Disc3,
} from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { fadeBackdrop, scaleModal } from '@/lib/motion';

export default function Header({ onMenuToggle, onOpenCommand }) {
  const { connected, autoStatus } = useSocket();
  const navigate = useNavigate();

  const [refreshOpen,  setRefreshOpen]  = useState(false);
  const [forceFolder,  setForceFolder]  = useState('');
  const [submitting,   setSubmitting]   = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const inputRef = useRef(null);

  const [settingsOpen,    setSettingsOpen]    = useState(false);
  const [playlistInput,   setPlaylistInput]   = useState('');
  const [playlistConfig,  setPlaylistConfig]  = useState(null);
  const [savingPlaylist,  setSavingPlaylist]  = useState(false);
  const [playlistError,   setPlaylistError]   = useState('');
  const settingsPanelRef = useRef(null);
  const playlistInputRef = useRef(null);

  const rateLimited =
    autoStatus.current?.toLowerCase().includes('rate limited') ||
    (autoStatus.status === 'idle' && autoStatus.current?.toLowerCase().includes('cooldown'));

  useEffect(() => {
    if (refreshOpen) {
      const t = setTimeout(() => inputRef.current?.focus(), 80);
      return () => clearTimeout(t);
    }
  }, [refreshOpen]);

  useEffect(() => {
    if (!confirmClear) return;
    const t = setTimeout(() => setConfirmClear(false), 4000);
    return () => clearTimeout(t);
  }, [confirmClear]);

  useEffect(() => {
    if (!settingsOpen) return;
    const t = setTimeout(() => playlistInputRef.current?.focus(), 80);
    api.getIngestConfig().then(setPlaylistConfig).catch(() => {});
    return () => clearTimeout(t);
  }, [settingsOpen]);

  // Close settings popover on outside click
  useEffect(() => {
    if (!settingsOpen) return;
    function onClick(e) {
      if (settingsPanelRef.current && !settingsPanelRef.current.contains(e.target))
        setSettingsOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
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
      await api.refreshPlaylist({ forceFolder: forceFolder.trim(), forceRedownload: false });
      setRefreshOpen(false);
      setForceFolder('');
    } catch (_) {}
    finally { setSubmitting(false); }
  }

  async function handleClearAndRefresh() {
    setConfirmClear(false);
    setSubmitting(true);
    try {
      await api.clearHistoryForPlaylist();
      await api.refreshPlaylist({ forceFolder: forceFolder.trim(), forceRedownload: false });
      setRefreshOpen(false);
      setForceFolder('');
    } catch (_) {}
    finally { setSubmitting(false); }
  }

  return (
    <>
      <header
        className="sticky top-0 z-30 flex items-center gap-3 h-12 px-4 flex-shrink-0"
        style={{
          background: 'rgba(12,12,20,0.85)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        {/* Left: mobile menu toggle */}
        <button
          onClick={onMenuToggle}
          className="lg:hidden w-8 h-8 flex items-center justify-center rounded-lg cursor-pointer focus-ring transition-colors flex-shrink-0"
          style={{ color: 'var(--text-tertiary)' }}
        >
          <Menu className="w-4 h-4" />
        </button>

        {/* Logo (mobile only — sidebar carries it on desktop) */}
        <div className="lg:hidden flex items-center gap-2 flex-shrink-0">
          <div
            className="w-6 h-6 rounded-md flex items-center justify-center"
            style={{ background: 'var(--accent-violet-dim)' }}
          >
            <Disc3 className="w-3.5 h-3.5" style={{ color: 'var(--accent-violet)' }} />
          </div>
        </div>

        {/* Nav history arrows */}
        <div className="hidden sm:flex items-center gap-1 flex-shrink-0">
          <button
            onClick={() => navigate(-1)}
            className="tb-btn"
            title="Go back"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button
            onClick={() => navigate(1)}
            className="tb-btn"
            title="Go forward"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>

        {/* Search → opens command palette */}
        <button onClick={onOpenCommand} className="search-wrap hidden md:flex">
          <Search className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="tb-search text-left">Search pages, actions…</span>
          <span className="text-mono flex-shrink-0" style={{ color: 'var(--text-muted)', fontSize: '10px' }}>Ctrl K</span>
        </button>

        {/* Right: actions */}
        <div className="flex items-center gap-1.5 ml-auto flex-shrink-0">

          {/* Rate limit warning */}
          <AnimatePresence>
            {rateLimited && (
              <motion.div
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0  }}
                exit={{ opacity: 0, x: 12   }}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-12"
                style={{
                  background: 'var(--accent-amber-dim)',
                  color: 'var(--accent-amber)',
                  border: '1px solid rgba(245,158,11,0.2)',
                }}
              >
                <AlertTriangle className="w-3.5 h-3.5" />
                Rate limited
              </motion.div>
            )}
          </AnimatePresence>

          {/* Stop sync */}
          <AnimatePresence>
            {autoStatus.status === 'downloading' && (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1  }}
                exit={{ opacity: 0, scale: 0.9   }}
                transition={{ duration: 0.15 }}
              >
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => api.stopSync().catch(() => {})}
                  title="Stop the current sync"
                >
                  <StopCircle className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Stop</span>
                </Button>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Playlist config popover */}
          <div className="relative" ref={settingsPanelRef}>
            <button
              onClick={() => setSettingsOpen(v => !v)}
              className={`tb-btn ${settingsOpen ? 'on' : ''}`}
              title="Configure ingest playlist"
            >
              <Settings2 className="w-4 h-4" />
            </button>

            <AnimatePresence>
              {settingsOpen && (
                <motion.div
                  role="dialog"
                  aria-label="Ingest playlist configuration"
                  initial={{ opacity: 0, y: -8, scale: 0.96 }}
                  animate={{ opacity: 1, y: 0,  scale: 1    }}
                  exit={{ opacity: 0,  y: -8, scale: 0.96   }}
                  transition={{ duration: 0.18 }}
                  className="absolute right-0 top-full mt-2 w-80 rounded-xl shadow-panel p-4 z-40"
                  style={{
                    background: 'var(--surface-2)',
                    border: '1px solid var(--border-default)',
                  }}
                >
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Settings2 className="w-4 h-4" style={{ color: 'var(--accent-violet)' }} />
                      <h3 className="text-13 font-semibold" style={{ color: 'var(--text-primary)' }}>
                        Ingest Playlist
                      </h3>
                    </div>
                    <button onClick={() => setSettingsOpen(false)} className="cursor-pointer focus-ring rounded"
                            style={{ color: 'var(--text-tertiary)' }}>
                      <X className="w-4 h-4" />
                    </button>
                  </div>

                  {playlistConfig && (
                    <div className="flex items-center gap-2 mb-3 text-12 px-2 py-1.5 rounded-lg"
                         style={playlistConfig.enabled
                           ? { background: 'var(--accent-emerald-dim)', color: 'var(--accent-emerald)', border: '1px solid rgba(16,185,129,0.2)' }
                           : { background: 'var(--accent-amber-dim)',   color: 'var(--accent-amber)',   border: '1px solid rgba(245,158,11,0.2)' }}>
                      {playlistConfig.enabled
                        ? <><CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" /><span className="font-mono truncate">{playlistConfig.playlist_id}</span></>
                        : <><AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" /><span>No playlist configured</span></>}
                    </div>
                  )}

                  <label className="text-label block mb-1.5" htmlFor="playlist-url-input">
                    Spotify playlist URL or ID
                  </label>
                  <Input
                    id="playlist-url-input"
                    ref={playlistInputRef}
                    value={playlistInput}
                    onChange={e => setPlaylistInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && savePlaylist()}
                    placeholder="https://open.spotify.com/playlist/..."
                    disabled={savingPlaylist}
                  />
                  {playlistError && (
                    <p className="mt-1.5 text-11" style={{ color: 'var(--accent-rose)' }}>{playlistError}</p>
                  )}
                  <p className="mt-1.5 text-11" style={{ color: 'var(--text-muted)' }}>
                    Paste a URL or playlist ID — takes effect immediately.
                  </p>

                  <div className="mt-4 flex justify-end gap-2">
                    <Button variant="ghost" size="sm" onClick={() => setSettingsOpen(false)} disabled={savingPlaylist}>
                      Cancel
                    </Button>
                    <Button size="sm" onClick={savePlaylist} disabled={savingPlaylist || !playlistInput.trim()}>
                      Save
                    </Button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Refresh button */}
          <button
            onClick={() => setRefreshOpen(v => !v)}
            className={`tb-btn ${submitting ? 'on' : ''}`}
            style={submitting ? { color: 'var(--accent-cyan)' } : undefined}
            title="Refresh playlist"
          >
            <RefreshCw className={`w-4 h-4 ${submitting ? 'animate-spin' : ''}`} />
          </button>

          {/* Connection dot */}
          <div
            className="w-2 h-2 rounded-full flex-shrink-0 ml-1"
            style={{ background: connected ? 'var(--accent-emerald)' : 'var(--accent-rose)' }}
            title={connected ? 'Connected' : 'Disconnected'}
          />
        </div>
      </header>

      {/* Refresh modal (full-screen overlay) */}
      <AnimatePresence>
        {refreshOpen && (
          <>
            <motion.div
              {...fadeBackdrop}
              className="fixed inset-0 z-40"
              style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(8px)' }}
              onClick={() => { setRefreshOpen(false); setConfirmClear(false); }}
            />
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
              <motion.div
                {...scaleModal}
                role="dialog"
                aria-label="Refresh playlist options"
                aria-modal="true"
                className="pointer-events-auto w-full max-w-sm rounded-2xl shadow-modal p-5"
                style={{ background: 'var(--surface-2)', border: '1px solid var(--border-default)' }}
              >
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <FolderInput className="w-4 h-4" style={{ color: 'var(--accent-violet)' }} />
                    <h3 className="text-14 font-semibold" style={{ color: 'var(--text-primary)' }}>
                      Refresh playlist
                    </h3>
                  </div>
                  <button
                    onClick={() => { setRefreshOpen(false); setConfirmClear(false); }}
                    className="cursor-pointer focus-ring rounded p-1 transition-colors"
                    style={{ color: 'var(--text-tertiary)' }}
                    aria-label="Close dialog"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <label className="text-label block mb-1.5" htmlFor="force-folder-input">
                  Save all to folder (optional)
                </label>
                <Input
                  id="force-folder-input"
                  ref={inputRef}
                  value={forceFolder}
                  onChange={e => setForceFolder(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && triggerRefresh()}
                  placeholder="Sammy Virji"
                  disabled={submitting}
                />
                <p className="mt-1.5 text-11" style={{ color: 'var(--text-muted)' }}>
                  Leave empty to auto-route by artist
                </p>

                {/* Clear history section */}
                <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--border-subtle)' }}>
                  <AnimatePresence mode="wait">
                    {confirmClear ? (
                      <motion.div
                        key="confirm"
                        initial={{ opacity: 0, y: -4 }}
                        animate={{ opacity: 1,  y: 0  }}
                        exit={{ opacity: 0,    y: -4  }}
                        className="flex items-start gap-2.5 p-3 rounded-xl"
                        style={{ background: 'var(--accent-rose-dim)', border: '1px solid rgba(244,63,94,0.2)' }}
                      >
                        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--accent-rose)' }} />
                        <div className="flex-1 min-w-0">
                          <p className="text-12 font-medium" style={{ color: 'var(--accent-rose)' }}>
                            Clears all download history
                          </p>
                          <p className="text-11 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
                            Every track will re-download on next sync.
                          </p>
                        </div>
                        <button
                          onClick={handleClearAndRefresh}
                          disabled={submitting}
                          className="text-12 px-3 py-1.5 rounded-lg font-medium cursor-pointer transition-all active:scale-95 focus-ring"
                          style={{ background: 'var(--accent-rose)', color: 'white' }}
                        >
                          Confirm
                        </button>
                      </motion.div>
                    ) : (
                      <motion.button
                        key="clear-btn"
                        initial={{ opacity: 0, y: -4 }}
                        animate={{ opacity: 1,  y: 0  }}
                        exit={{ opacity: 0,    y: -4  }}
                        onClick={() => setConfirmClear(true)}
                        disabled={submitting}
                        className="w-full flex items-center justify-center gap-1.5 text-12 py-2 px-3 rounded-lg cursor-pointer transition-all duration-150 active:scale-[0.98] focus-ring"
                        style={{
                          color: 'var(--accent-amber)',
                          border: '1px solid rgba(245,158,11,0.2)',
                          background: 'transparent',
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-amber-dim)'}
                        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                        Clear history &amp; re-download all
                      </motion.button>
                    )}
                  </AnimatePresence>
                </div>

                <div className="mt-4 flex justify-end gap-2">
                  <Button variant="ghost" size="sm"
                          onClick={() => { setRefreshOpen(false); setConfirmClear(false); }}
                          disabled={submitting}>
                    Cancel
                  </Button>
                  <Button size="sm" onClick={triggerRefresh} disabled={submitting}>
                    <RefreshCw className={`w-3.5 h-3.5 ${submitting ? 'animate-spin' : ''}`} />
                    {submitting ? 'Refreshing…' : 'Refresh'}
                  </Button>
                </div>
              </motion.div>
            </div>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
