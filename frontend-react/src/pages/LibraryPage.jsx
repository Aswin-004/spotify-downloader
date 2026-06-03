import { useState, useMemo, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Music, Search, ChevronRight, ChevronDown,
  AlertTriangle, RotateCw, Loader2, MoreHorizontal, FolderInput, X, Download,
  Play, Pause, Tags,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { useToast } from '@/components/ui/toast';
import GenreBadge from '@/components/GenreBadge';
import { ease } from '@/lib/motion';

const GENRE_OPTIONS = [
  'House', 'Trance', 'UK Garage', 'Drum & Bass', 'Dubstep',
  'Techno', 'Grime', 'Electronic',
  'Bollywood', 'Punjabi', 'Tamil',
  'Hip Hop', 'R&B', 'Pop', 'Latin',
];

const KNOWN_GENRES = new Set(GENRE_OPTIONS.map(g => g.toLowerCase()));

function extractArtist(filename) {
  const base = filename.replace(/\.mp3$/i, '');
  const parts = base.split(' - ');
  return parts.length > 1 ? parts[parts.length - 1].trim() : '';
}

function TrackArt({ path }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
           style={{ background: 'var(--surface-2)' }}>
        <Music className="w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
      </div>
    );
  }
  return (
    <img
      src={`/api/artwork?path=${encodeURIComponent(path)}`}
      alt=""
      className="w-8 h-8 rounded-lg object-cover flex-shrink-0"
      onError={() => setFailed(true)}
    />
  );
}

function FolderSkeleton() {
  return (
    <div className="rounded-xl overflow-hidden"
         style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}>
      <div className="px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-4 h-4 rounded shimmer" />
          <div className="w-28 h-3.5 rounded shimmer" />
        </div>
        <div className="w-8 h-4 rounded-full shimmer" />
      </div>
    </div>
  );
}

export default function LibraryPage() {
  const { files: socketFiles } = useSocket();
  const { addToast } = useToast();
  const [files, setFiles] = useState([]);
  const [search, setSearch] = useState('');
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [openMenu, setOpenMenu] = useState(null);
  const [selectedGenres, setSelectedGenres] = useState({});
  const [moving, setMoving] = useState({});
  const [playingPath, setPlayingPath] = useState(null);
  const audioRef = useRef(null);
  const [retagging, setRetagging] = useState(false);
  const [folderTags, setFolderTags] = useState({});   // { "Library/House": { "Song.mp3": {bpm,camelot_key,...} } }
  const { retagProgress } = useSocket();

  function handleTogglePlay(path) {
    const audio = audioRef.current;
    if (!audio) return;
    const url = api.previewTrackByPath(path);
    if (audio.src !== window.location.origin + url) {
      audio.src = url;
      audio.load();
      audio.play().then(() => setPlayingPath(path)).catch(() => {});
    } else if (audio.paused) {
      audio.play().then(() => setPlayingPath(path)).catch(() => {});
    } else {
      audio.pause();
      setPlayingPath(null);
    }
  }

  async function handleMoveFromLibrary(file) {
    const genre = selectedGenres[file.path];
    if (!genre) return;
    setMoving(p => ({ ...p, [file.path]: true }));
    try {
      const artist = extractArtist(file.name);
      const result = await api.moveAndRemember(file.path, genre, artist);
      if (result.moved) {
        addToast({
          type: 'success',
          title: `Moved to ${genre}`,
          description: artist ? `${artist} will auto-route to ${genre} from now on` : file.name,
          duration: 5000,
        });
        setOpenMenu(null);
        loadFiles();
      } else {
        addToast({ type: 'error', title: 'Move failed', description: result.error, duration: 5000 });
      }
    } catch (err) {
      addToast({ type: 'error', title: 'Move failed', description: err.message, duration: 4000 });
    } finally {
      setMoving(p => ({ ...p, [file.path]: false }));
    }
  }

  function loadFiles() {
    setLoading(true);
    setError('');
    api.getFiles()
      .then(data => { if (data.files) setFiles(data.files); })
      .catch(() => setError('Failed to load library. Check that the backend is running.'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { loadFiles(); }, []);
  useEffect(() => { if (socketFiles.length > 0) setFiles(socketFiles); }, [socketFiles]);

  const grouped = useMemo(() => {
    const q = search.toLowerCase();
    // Only show Library/ folders — exclude Ingest, Staging, NeedsReview, Manual, etc.
    const libOnly = files.filter(f => {
      const folder = (f.folder || '').replace(/\\/g, '/');
      return folder.startsWith('Library/') || folder === 'Library';
    });
    const filtered = search
      ? libOnly.filter(f => f.name.toLowerCase().includes(q))
      : libOnly;
    const groups = {};
    filtered.forEach(f => {
      const folder = f.folder || 'Root';
      if (!groups[folder]) groups[folder] = [];
      groups[folder].push(f);
    });
    return groups;
  }, [files, search]);

  const folderNames = useMemo(() => Object.keys(grouped).sort(), [grouped]);

  function toggleFolder(name) {
    setExpandedFolders(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else {
        next.add(name);
        // Lazy-load BPM/Camelot tags for this folder on first expand
        if (!folderTags[name]) {
          api.getFolderTags(name).then(data => {
            setFolderTags(prev => ({ ...prev, [name]: data }));
          }).catch(err => console.warn('[folder-tags]', err));
        }
      }
      return next;
    });
  }

  useEffect(() => {
    if (folderNames.length > 0 && expandedFolders.size === 0)
      setExpandedFolders(new Set(folderNames.slice(0, 5)));
  }, [folderNames]);

  // Fetch tags for any newly expanded folder that hasn't been loaded yet
  useEffect(() => {
    expandedFolders.forEach(name => {
      if (!folderTags[name]) {
        api.getFolderTags(name).then(data => {
          setFolderTags(prev => ({ ...prev, [name]: data }));
        }).catch(err => console.warn('[folder-tags]', err));
      }
    });
  }, [expandedFolders]);

  const totalFiles = files.length;
  const isKnownGenre = (name) => KNOWN_GENRES.has(name.toLowerCase());

  async function handleRetag() {
    setRetagging(true);
    try { await api.retagLibrary(); } catch { setRetagging(false); }
  }
  useEffect(() => {
    if (retagProgress?.status === 'complete' || retagProgress?.status === 'error')
      setRetagging(false);
  }, [retagProgress]);
  const retagPct = retagProgress?.percentage ?? 0;

  return (
    <div className="max-w-5xl mx-auto space-y-5 pb-10">
      <audio
        ref={audioRef}
        onEnded={() => setPlayingPath(null)}
        onPause={() => setPlayingPath(null)}
      />

      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={ease}
        className="flex items-center justify-between"
      >
        <div>
          <h1 className="font-display text-22 font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
            Library
          </h1>
          <p className="text-12 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
            Manage your downloaded tracks
          </p>
        </div>
        <div className="flex items-center gap-3">
          {!loading && (
            <>
              <Button variant="secondary" size="sm" onClick={handleRetag} disabled={retagging}>
                {retagging
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <Tags className="w-3.5 h-3.5" />}
                {retagging ? 'Retagging…' : 'Retag'}
              </Button>
              <a
                href={api.rekordboxExportUrl('all', 'ObsidianDJ Library')}
                download="rekordbox_library.xml"
                title="Export full library as Rekordbox XML"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-12 font-medium transition-colors"
                style={{ background: 'var(--surface-1)', color: 'var(--text-secondary)', border: '1px solid var(--border-subtle)' }}
              >
                <Download className="w-3.5 h-3.5" />
                Rekordbox
              </a>
            </>
          )}
          <div className="text-right">
            {loading ? (
              <Loader2 className="w-5 h-5 animate-spin ml-auto" style={{ color: 'var(--accent-violet)' }} />
            ) : (
              <>
                <div className="font-display text-28 font-bold tabular-nums" style={{ color: 'var(--accent-violet)', letterSpacing: '-0.03em' }}>
                  {totalFiles}
                </div>
                <div className="text-11" style={{ color: 'var(--text-tertiary)' }}>tracks</div>
              </>
            )}
          </div>
        </div>
      </motion.div>

      {/* Error banner */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
            className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl"
            style={{ background: 'var(--accent-rose-dim)', border: '1px solid rgba(244,63,94,0.2)' }}
          >
            <div className="flex items-center gap-2.5">
              <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--accent-rose)' }} />
              <span className="text-13" style={{ color: 'var(--text-secondary)' }}>{error}</span>
            </div>
            <Button size="sm" variant="ghost" onClick={loadFiles} style={{ color: 'var(--accent-rose)', flexShrink: 0 }}>
              <RotateCw className="w-3.5 h-3.5 mr-1" /> Retry
            </Button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Retag progress bar */}
      <AnimatePresence>
        {retagProgress?.status === 'processing' && (
          <motion.div
            initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            className="rounded-xl p-3 space-y-2"
            style={{ background: 'var(--accent-violet-dim)', border: '1px solid rgba(139,92,246,0.2)' }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--accent-violet)' }} />
                <span className="text-13 font-medium" style={{ color: 'var(--accent-violet)' }}>Retagging Library</span>
              </div>
              <span className="text-11 font-mono tabular-nums" style={{ color: 'var(--text-tertiary)' }}>
                {retagProgress.current}/{retagProgress.total}
              </span>
            </div>
            <div className="w-full h-1 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
              <motion.div className="h-full rounded-full" style={{ background: 'var(--accent-violet)' }}
                animate={{ width: `${retagPct}%` }} transition={{ duration: 0.5, ease: 'easeOut' }} />
            </div>
            {retagProgress.current_file && (
              <p className="text-10 truncate" style={{ color: 'var(--text-tertiary)' }}>{retagProgress.current_file}</p>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Search */}
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...ease, delay: 0.08 }}
        className="relative"
      >
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 pointer-events-none"
                style={{ color: 'var(--text-muted)' }} />
        <Input
          placeholder="Search tracks..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="pl-9"
        />
      </motion.div>

      {/* Track List */}
      <ScrollArea className="h-[600px]">
        <div className="pr-3 space-y-2.5">
          {loading ? (
            [...Array(6)].map((_, i) => <FolderSkeleton key={i} />)
          ) : folderNames.length === 0 ? (
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="flex flex-col items-center justify-center py-16 rounded-xl"
              style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}
            >
              <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-4"
                   style={{ background: 'var(--surface-1)' }}>
                <Music className="w-5 h-5" style={{ color: 'var(--text-muted)' }} />
              </div>
              <p className="text-13 font-medium" style={{ color: 'var(--text-tertiary)' }}>
                {search ? 'No tracks found' : 'No tracks in library'}
              </p>
              <p className="text-11 mt-1" style={{ color: 'var(--text-muted)' }}>
                {search ? 'Try a different search term' : 'Downloaded tracks appear here'}
              </p>
            </motion.div>
          ) : (
            <AnimatePresence>
              {folderNames.map((folder, idx) => {
                const isExpanded = expandedFolders.has(folder);
                const tracks = grouped[folder];
                return (
                  <motion.div
                    key={folder}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -10 }}
                    transition={{ ...ease, delay: Math.min(idx * 0.04, 0.2) }}
                    className="rounded-xl overflow-hidden"
                    style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}
                  >
                    {/* Folder header */}
                    <button
                      onClick={() => toggleFolder(folder)}
                      aria-expanded={isExpanded}
                      aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${folder}`}
                      className="w-full px-4 py-3 flex items-center justify-between transition-colors duration-150 cursor-pointer focus-ring group/folder"
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-1)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <div className="flex items-center gap-2.5 flex-1 min-w-0">
                        <motion.div
                          animate={{ rotate: isExpanded ? 90 : 0 }}
                          transition={{ duration: 0.15 }}
                        >
                          <ChevronRight className="w-4 h-4 flex-shrink-0"
                            style={{ color: isExpanded ? 'var(--accent-violet)' : 'var(--text-muted)' }} />
                        </motion.div>
                        <span className="text-13 font-semibold truncate"
                              style={{ color: isExpanded ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                          {folder.replace(/^Library[/\\]/, '')}
                        </span>
                        {isKnownGenre(folder.replace(/^Library[/\\]/, '')) && (
                          <GenreBadge genre={folder.replace(/^Library[/\\]/, '')} className="flex-shrink-0" />
                        )}
                      </div>
                      <span
                        className="text-11 font-mono tabular-nums px-2 py-0.5 rounded-full ml-2 flex-shrink-0"
                        style={{ background: 'var(--surface-2)', color: 'var(--text-tertiary)' }}
                      >
                        {tracks.length}
                      </span>
                      <a
                        href={api.rekordboxExportUrl(folder, folder.split('/').pop())}
                        download
                        title={`Export "${folder}" as Rekordbox XML`}
                        onClick={e => e.stopPropagation()}
                        className="ml-2 w-6 h-6 flex items-center justify-center rounded-lg opacity-0 group-hover/folder:opacity-100 focus:opacity-100 transition-opacity duration-150 flex-shrink-0 focus-ring cursor-pointer"
                        style={{ color: 'var(--text-muted)' }}
                        onMouseEnter={e => {
                          e.currentTarget.style.background = 'var(--surface-2)';
                          e.currentTarget.style.color = 'var(--accent-violet)';
                        }}
                        onMouseLeave={e => {
                          e.currentTarget.style.background = 'transparent';
                          e.currentTarget.style.color = 'var(--text-muted)';
                        }}
                      >
                        <Download className="w-3.5 h-3.5" />
                      </a>
                    </button>

                    {/* Track rows */}
                    <AnimatePresence>
                      {isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                          style={{ borderTop: '1px solid var(--border-subtle)' }}
                        >
                          {tracks.map((file, fileIdx) => (
                            <motion.div
                              key={file.path}
                              initial={{ opacity: 0, x: -12 }}
                              animate={{ opacity: 1, x: 0 }}
                              transition={{ delay: Math.min(fileIdx * 0.02, 0.15), duration: 0.18 }}
                              className="px-4 py-2.5 flex items-center group/track transition-colors duration-150"
                              style={{ borderTop: fileIdx > 0 ? '1px solid var(--border-subtle)' : 'none' }}
                              onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-1)'}
                              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                            >
                              {/* Track info */}
                              {(() => {
                                const meta = folderTags[folder]?.[file.name];
                                return (
                                  <div className="flex items-center gap-2.5 flex-1 min-w-0">
                                    <TrackArt path={file.path} />
                                    <div className="min-w-0 flex-1">
                                      <p className="text-12 font-medium truncate"
                                         style={{ color: 'var(--text-primary)' }}>
                                        {meta?.title || file.name}
                                      </p>
                                      <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                                        {meta?.artist && (
                                          <span className="text-10 truncate max-w-[120px]"
                                                style={{ color: 'var(--text-tertiary)' }}>
                                            {meta.artist}
                                          </span>
                                        )}
                                        {meta?.bpm && (
                                          <span className="text-10 font-mono font-semibold px-1.5 py-0.5 rounded"
                                                style={{ background: 'var(--accent-amber-dim)', color: 'var(--accent-amber)' }}>
                                            {meta.bpm}
                                          </span>
                                        )}
                                        {meta?.camelot_key && (
                                          <span className="text-10 font-mono font-semibold px-1.5 py-0.5 rounded"
                                                style={{ background: 'rgba(20,184,166,0.15)', color: '#14b8a6' }}>
                                            {meta.camelot_key}
                                          </span>
                                        )}
                                        {!meta && (
                                          <span className="text-10" style={{ color: 'var(--text-muted)' }}>
                                            {file.mtime ? new Date(file.mtime * 1000).toLocaleDateString() : ''}
                                          </span>
                                        )}
                                      </div>
                                    </div>
                                  </div>
                                );
                              })()}

                              {/* Inline genre picker or action button */}
                              <AnimatePresence mode="wait">
                                {openMenu === file.path ? (
                                  <motion.div
                                    key="picker"
                                    initial={{ opacity: 0, x: 8 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    exit={{ opacity: 0, x: 8 }}
                                    transition={{ duration: 0.15 }}
                                    className="flex items-center gap-1.5 ml-3 flex-shrink-0"
                                  >
                                    <select
                                      aria-label="Select genre"
                                      value={selectedGenres[file.path] || ''}
                                      onChange={e =>
                                        setSelectedGenres(p => ({ ...p, [file.path]: e.target.value }))
                                      }
                                      className="text-11 rounded-lg px-2 py-1.5 cursor-pointer focus-ring transition-colors"
                                      style={{
                                        background: 'var(--surface-2)',
                                        border: '1px solid var(--border-default)',
                                        color: 'var(--text-secondary)',
                                      }}
                                    >
                                      <option value="">Pick genre…</option>
                                      {GENRE_OPTIONS.map(g => (
                                        <option key={g} value={g}>{g}</option>
                                      ))}
                                    </select>
                                    <Button
                                      size="sm"
                                      disabled={!selectedGenres[file.path] || moving[file.path]}
                                      onClick={() => handleMoveFromLibrary(file)}
                                    >
                                      {moving[file.path] ? (
                                        <><Loader2 className="w-3 h-3 animate-spin" /> Moving…</>
                                      ) : (
                                        <><FolderInput className="w-3 h-3" /> Move</>
                                      )}
                                    </Button>
                                    <button
                                      aria-label="Cancel"
                                      onClick={() => setOpenMenu(null)}
                                      className="w-6 h-6 flex items-center justify-center rounded-lg cursor-pointer focus-ring transition-colors"
                                      style={{ color: 'var(--text-muted)' }}
                                      onMouseEnter={e => e.currentTarget.style.color = 'var(--text-tertiary)'}
                                      onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
                                    >
                                      <X className="w-3.5 h-3.5" />
                                    </button>
                                  </motion.div>
                                ) : (
                                  <div className="flex items-center gap-1 ml-3 flex-shrink-0">
                                    {/* Play preview */}
                                    <button
                                      aria-label="Preview"
                                      onClick={() => handleTogglePlay(file.path)}
                                      className="w-7 h-7 flex items-center justify-center rounded-lg cursor-pointer sm:opacity-0 sm:group-hover/track:opacity-100 focus:opacity-100 active:scale-95 transition-all duration-150 focus-ring"
                                      style={{
                                        color: playingPath === file.path ? 'var(--accent-violet)' : 'var(--text-muted)',
                                        background: playingPath === file.path ? 'var(--accent-violet-dim)' : 'transparent',
                                      }}
                                      onMouseEnter={e => { if (playingPath !== file.path) { e.currentTarget.style.background = 'var(--surface-2)'; e.currentTarget.style.color = 'var(--accent-violet)'; }}}
                                      onMouseLeave={e => { if (playingPath !== file.path) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)'; }}}
                                    >
                                      {playingPath === file.path
                                        ? <Pause className="w-4 h-4" />
                                        : <Play  className="w-4 h-4" />}
                                    </button>
                                    {/* Move to genre */}
                                    <motion.button
                                      key="dots"
                                      initial={{ opacity: 0 }}
                                      animate={{ opacity: 1 }}
                                      exit={{ opacity: 0 }}
                                      aria-label="Move to genre"
                                      onClick={() => setOpenMenu(file.path)}
                                      className="w-7 h-7 flex items-center justify-center rounded-lg cursor-pointer sm:opacity-0 sm:group-hover/track:opacity-100 focus:opacity-100 active:scale-95 transition-all duration-150 flex-shrink-0 focus-ring"
                                      style={{ color: 'var(--text-muted)' }}
                                      onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; e.currentTarget.style.color = 'var(--text-secondary)'; }}
                                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)'; }}
                                    >
                                      <MoreHorizontal className="w-4 h-4" />
                                    </motion.button>
                                  </div>
                                )}
                              </AnimatePresence>
                            </motion.div>
                          ))}
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </motion.div>
                );
              })}
            </AnimatePresence>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
