import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, RefreshCw, Menu, FolderInput, X } from 'lucide-react';
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

  async function triggerRefresh() {
    if (submitting) return;
    setSubmitting(true);
    try {
      // If empty, api.refreshPlaylist omits force_folder from the body — backend
      // falls back to normal artist-based routing.
      // When force_folder IS set, auto-enable force_redownload: if the user
      // pinned a folder, they clearly want the tracks to actually land there
      // regardless of whether ingest history already marks them as seen.
      const trimmed = forceFolder.trim();
      await api.refreshPlaylist({
        forceFolder: trimmed,
        forceRedownload: Boolean(trimmed),
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

        {/* Refresh trigger + force_folder popover */}
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
              <motion.div
                role="dialog"
                aria-label="Refresh playlist options"
                initial={{ opacity: 0, y: -8, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -8, scale: 0.96 }}
                transition={{ duration: 0.18, ease: 'easeOut' }}
                className="absolute right-0 top-full mt-2 w-80 origin-top-right glass rounded-2xl border border-white/10 shadow-xl p-4 z-40"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <FolderInput className="w-4 h-4 text-primary" />
                    <h3 className="text-sm font-semibold">Refresh playlist</h3>
                  </div>
                  <button
                    onClick={() => setOpen(false)}
                    className="text-gray-500 hover:text-white transition-colors"
                    aria-label="Close"
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

                <div className="mt-4 flex justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setOpen(false)}
                    disabled={submitting}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={triggerRefresh}
                    disabled={submitting}
                  >
                    <RefreshCw
                      className={`w-3.5 h-3.5 ${
                        submitting ? 'animate-spin' : ''
                      }`}
                    />
                    Refresh
                  </Button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </header>
  );
}
