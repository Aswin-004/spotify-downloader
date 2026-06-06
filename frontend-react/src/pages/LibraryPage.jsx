import { useState, useMemo, useEffect, useRef } from 'react';
import { usePlayer } from '@/context/PlayerContext';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Music, Search, ChevronRight, ChevronDown,
  AlertTriangle, RotateCw, Loader2, MoreHorizontal, FolderInput, X, Download,
  Play, Pause, Tags, Pencil, Check,
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
import { getGenreColor, getCamelotColor } from '@/lib/tokens';

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
  const [editingTags, setEditingTags] = useState(null);   // { path, artist, title, folder, filename }
  const [tagDraft, setTagDraft] = useState({ artist: '', title: '' });
  const [savingTags, setSavingTags] = useState(false);
  const { nowPlaying, playing, toggle, setQueueAndPlay } = usePlayer();
  const retagTimerRef = useRef(null);
  const [retagging, setRetagging] = useState(false);
  const [folderTags, setFolderTags] = useState({});   // { "Library/House": { "Song.mp3": {bpm,camelot_key,...} } }
  const { retagProgress } = useSocket();

  function handleTogglePlay(file, folderFiles) {
    if (nowPlaying?.path === file.path) { toggle(); return; }
    const tags   = folderTags[file.folder] || {};
    const tracks = folderFiles.map(f => {
      const t = tags[f.name] || {};
      return {
        title:    t.title  || f.name.replace(/\.mp3$/i, ''),
        artist:   t.artist || extractArtist(f.name),
        bpm:      t.bpm    ?? null,
        camelot:  t.camelot_key || null,
        audioUrl: api.previewTrackByPath(f.path),
        filename: f.name,
        path:     f.path,
      };
    });
    const idx = folderFiles.findIndex(f => f.path === file.path);
    setQueueAndPlay(tracks, idx >= 0 ? idx : 0);
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
      else next.add(name);
      return next;
    });
    // Tag loading is handled by the useEffect below — no duplicate fetch here
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

  async function handleSaveTags() {
    if (!editingTags) return;
    setSavingTags(true);
    try {
      await api.updateTrackTags(editingTags.path, tagDraft);
      // Update folderTags cache so UI refreshes immediately
      setFolderTags(prev => {
        const folder = editingTags.folder;
        const existing = prev[folder] || {};
        return {
          ...prev,
          [folder]: {
            ...existing,
            [editingTags.filename]: {
              ...(existing[editingTags.filename] || {}),
              artist: tagDraft.artist,
              title: tagDraft.title,
            }
          }
        };
      });
      addToast({ type: 'success', title: 'Tags saved', description: `${editingTags.filename}`, duration: 3000 });
      setEditingTags(null);
    } catch (err) {
      addToast({ type: 'error', title: 'Save failed', description: err.message, duration: 4000 });
    } finally {
      setSavingTags(false);
    }
  }

  const totalFiles = files.length;
  const isKnownGenre = (name) => KNOWN_GENRES.has(name.toLowerCase());

  async function handleRetag() {
    setRetagging(true);
    try {
      await api.retagLibrary();
      // Fallback: unlock after 5 min if WebSocket never delivers completion
      retagTimerRef.current = setTimeout(() => setRetagging(false), 5 * 60 * 1000);
    } catch (err) {
      setRetagging(false);
      addToast({ type: 'error', title: 'Retag failed', description: err.message, duration: 5000 });
    }
  }
  useEffect(() => {
    if (retagProgress?.status === 'complete' || retagProgress?.status === 'error') {
      setRetagging(false);
      // WebSocket delivered — cancel the fallback timer
      if (retagTimerRef.current) {
        clearTimeout(retagTimerRef.current);
        retagTimerRef.current = null;
      }
    }
  }, [retagProgress]);
  const retagPct = retagProgress?.percentage ?? 0;

  return (
    <div className="max-w-5xl mx-auto flex flex-col pb-10" style={{ gap: 20, minHeight: 0 }}>

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

      {/* Search — terminal filter */}
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...ease, delay: 0.08 }}
        style={{
          display: 'flex', alignItems: 'center',
          border: '1px solid var(--border-default)',
          borderRadius: 4, background: 'var(--surface-0)',
          transition: 'border-color 0.15s, box-shadow 0.15s',
        }}
        onFocusCapture={e => {
          e.currentTarget.style.borderColor = 'var(--accent-cyan)';
          e.currentTarget.style.boxShadow = '0 0 0 1px var(--accent-cyan)';
        }}
        onBlurCapture={e => {
          e.currentTarget.style.borderColor = 'var(--border-default)';
          e.currentTarget.style.boxShadow = 'none';
        }}
      >
        <span style={{
          paddingLeft: 12, paddingRight: 6, flexShrink: 0,
          color: 'var(--accent-cyan)', fontSize: 11, fontWeight: 700,
          letterSpacing: '0.04em', userSelect: 'none',
          fontFamily: 'ui-monospace, monospace',
        }}>
          filter:
        </span>
        <input
          placeholder="artist, title, or genre…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            flex: 1, height: 40, background: 'transparent', border: 'none',
            outline: 'none', color: 'var(--text-primary)',
            fontSize: 13, fontFamily: 'ui-monospace, monospace',
          }}
        />
        {search && (
          <button
            onClick={() => setSearch('')}
            style={{ padding: '0 10px', color: 'var(--text-muted)', background: 'transparent', border: 'none', cursor: 'pointer' }}
          >
            <X style={{ width: 12, height: 12 }} />
          </button>
        )}
      </motion.div>

      {/* Track List */}
      <ScrollArea className="flex-1 min-h-0" style={{ height: 'calc(100vh - 240px)' }}>
        <div className="space-y-0">
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
                const genreName = folder.replace(/^Library[/\\]/, '');
                const accentColor = getGenreColor(genreName);
                return (
                  <motion.div
                    key={folder}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ ...ease, delay: Math.min(idx * 0.03, 0.18) }}
                    style={{ borderBottom: '1px solid var(--border-subtle)', overflow: 'hidden' }}
                  >
                    {/* Folder header — crate divider with genre color bar */}
                    <button
                      onClick={() => toggleFolder(folder)}
                      aria-expanded={isExpanded}
                      aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${genreName}`}
                      className="w-full focus-ring group/folder"
                      style={{
                        display: 'flex', alignItems: 'center', gap: 10,
                        padding: '9px 12px',
                        background: 'transparent', border: 'none', cursor: 'pointer',
                        borderLeft: `3px solid ${accentColor}`,
                        transition: 'background 0.1s',
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-0)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <motion.div
                        animate={{ rotate: isExpanded ? 90 : 0 }}
                        transition={{ duration: 0.15 }}
                        style={{ flexShrink: 0 }}
                      >
                        <ChevronRight style={{ width: 12, height: 12, color: accentColor }} />
                      </motion.div>
                      <span style={{
                        fontSize: 11, fontWeight: 700, letterSpacing: '0.09em',
                        textTransform: 'uppercase', fontFamily: 'ui-monospace, monospace',
                        color: isExpanded ? 'var(--text-primary)' : 'var(--text-secondary)',
                        flex: 1, textAlign: 'left',
                      }}>
                        {genreName}
                      </span>
                      <span style={{
                        fontSize: 10, fontFamily: 'monospace', fontVariantNumeric: 'tabular-nums',
                        color: 'var(--text-muted)', flexShrink: 0,
                      }}>
                        {tracks.length}
                      </span>
                      <a
                        href={api.rekordboxExportUrl(folder, folder.split('/').pop())}
                        download
                        title={`Export "${genreName}" as Rekordbox XML`}
                        onClick={e => e.stopPropagation()}
                        style={{
                          width: 22, height: 22, display: 'flex', alignItems: 'center',
                          justifyContent: 'center', borderRadius: 4, flexShrink: 0,
                          color: 'var(--text-muted)', opacity: 0, transition: 'opacity 0.15s',
                        }}
                        className="group-hover/folder:opacity-100 focus:opacity-100"
                        onMouseEnter={e => { e.currentTarget.style.color = accentColor; e.currentTarget.style.background = 'var(--surface-1)'; }}
                        onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}
                      >
                        <Download style={{ width: 12, height: 12 }} />
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
                              initial={{ opacity: 0, x: -8 }}
                              animate={{ opacity: 1, x: 0 }}
                              transition={{ delay: Math.min(fileIdx * 0.015, 0.12), duration: 0.15 }}
                              className="group/track"
                              style={{
                                display: 'flex', alignItems: 'center', gap: 8,
                                padding: '5px 12px',
                                borderTop: '1px solid var(--border-subtle)',
                                minHeight: 38, transition: 'background 0.1s',
                              }}
                              onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-0)'}
                              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                            >
                              {/* Row index */}
                              <span style={{
                                fontSize: 9, fontFamily: 'monospace', color: 'var(--text-muted)',
                                width: 20, textAlign: 'right', flexShrink: 0,
                                fontVariantNumeric: 'tabular-nums',
                              }}>
                                {fileIdx + 1}
                              </span>

                              {/* Track info */}
                              {(() => {
                                const meta = folderTags[folder]?.[file.name];
                                return (
                                  <>
                                    <TrackArt path={file.path} />
                                    <div className="min-w-0 flex-1">
                                      <p style={{
                                        fontSize: 12, fontWeight: 500, color: 'var(--text-primary)',
                                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                      }}>
                                        {meta?.title || file.name}
                                      </p>
                                      {meta?.artist && (
                                        <p style={{
                                          fontSize: 10, color: 'var(--text-tertiary)',
                                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                        }}>
                                          {meta.artist}
                                        </p>
                                      )}
                                    </div>
                                    {/* Fixed metadata column */}
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                                      {meta?.bpm && (
                                        <span style={{
                                          fontSize: 10, fontFamily: 'monospace', fontWeight: 700,
                                          padding: '1px 5px', borderRadius: 3,
                                          background: 'var(--accent-violet-dim)', color: 'var(--accent-violet)',
                                          letterSpacing: '0.02em',
                                        }}>
                                          {meta.bpm}
                                        </span>
                                      )}
                                      {meta?.camelot_key && (
                                        <span style={{
                                          fontSize: 10, fontFamily: 'monospace', fontWeight: 700,
                                          padding: '1px 5px', borderRadius: 3,
                                          background: `${getCamelotColor(meta.camelot_key)}22`,
                                          color: getCamelotColor(meta.camelot_key),
                                          letterSpacing: '0.02em',
                                        }}>
                                          {meta.camelot_key}
                                        </span>
                                      )}
                                    </div>
                                  </>
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
                                      onClick={() => handleTogglePlay(file, grouped[folder])}
                                      className="w-7 h-7 flex items-center justify-center rounded-lg cursor-pointer sm:opacity-0 sm:group-hover/track:opacity-100 focus:opacity-100 active:scale-95 transition-all duration-150 focus-ring"
                                      style={{
                                        color: nowPlaying?.path === file.path ? 'var(--accent-violet)' : 'var(--text-muted)',
                                        background: nowPlaying?.path === file.path ? 'var(--accent-violet-dim)' : 'transparent',
                                        ...(nowPlaying?.path === file.path ? { opacity: 1 } : {}),
                                      }}
                                      onMouseEnter={e => { if (nowPlaying?.path !== file.path) { e.currentTarget.style.background = 'var(--surface-2)'; e.currentTarget.style.color = 'var(--accent-violet)'; }}}
                                      onMouseLeave={e => { if (nowPlaying?.path !== file.path) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)'; }}}
                                    >
                                      {nowPlaying?.path === file.path && playing
                                        ? <Pause className="w-4 h-4" />
                                        : <Play  className="w-4 h-4" />}
                                    </button>
                                    {/* Edit tags */}
                                    <button
                                      aria-label="Edit tags"
                                      onClick={() => {
                                        const m = folderTags[folder]?.[file.name];
                                        setEditingTags({ path: (file.folder + '/' + file.name).replace(/\\/g, '/'), folder, filename: file.name });
                                        setTagDraft({ artist: m?.artist || '', title: m?.title || file.name.replace(/\.mp3$/i,'') });
                                        setOpenMenu(null);
                                      }}
                                      className="w-7 h-7 flex items-center justify-center rounded-lg cursor-pointer sm:opacity-0 sm:group-hover/track:opacity-100 focus:opacity-100 active:scale-95 transition-all duration-150 flex-shrink-0 focus-ring"
                                      style={{ color: 'var(--text-muted)' }}
                                      onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; e.currentTarget.style.color = 'var(--accent-cyan)'; }}
                                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)'; }}
                                    >
                                      <Pencil className="w-3.5 h-3.5" />
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

      {/* Tag edit modal */}
      <AnimatePresence>
        {editingTags && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
            onClick={e => { if (e.target === e.currentTarget) setEditingTags(null); }}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.95, opacity: 0 }}
              className="w-full max-w-md rounded-xl p-5 space-y-4"
              style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Pencil className="w-4 h-4" style={{ color: 'var(--accent-cyan)' }} />
                  <h3 className="font-display text-15 font-semibold" style={{ color: 'var(--text-primary)' }}>
                    Edit Tags
                  </h3>
                </div>
                <button onClick={() => setEditingTags(null)} className="w-6 h-6 flex items-center justify-center rounded focus-ring"
                        style={{ color: 'var(--text-muted)' }}>
                  <X className="w-4 h-4" />
                </button>
              </div>

              <p className="text-11 truncate" style={{ color: 'var(--text-tertiary)' }}>
                {editingTags.filename}
              </p>

              <div className="space-y-3">
                <div>
                  <label className="text-11 font-medium block mb-1" style={{ color: 'var(--text-secondary)' }}>Title</label>
                  <input
                    type="text"
                    value={tagDraft.title}
                    onChange={e => setTagDraft(d => ({ ...d, title: e.target.value }))}
                    className="w-full text-13 px-3 py-2 rounded-lg focus-ring outline-none"
                    style={{ background: 'var(--surface-1)', border: '1px solid var(--border-default)', color: 'var(--text-primary)' }}
                    placeholder="Track title"
                    autoFocus
                  />
                </div>
                <div>
                  <label className="text-11 font-medium block mb-1" style={{ color: 'var(--text-secondary)' }}>Artist</label>
                  <input
                    type="text"
                    value={tagDraft.artist}
                    onChange={e => setTagDraft(d => ({ ...d, artist: e.target.value }))}
                    className="w-full text-13 px-3 py-2 rounded-lg focus-ring outline-none"
                    style={{ background: 'var(--surface-1)', border: '1px solid var(--border-default)', color: 'var(--text-primary)' }}
                    placeholder="Artist name"
                    onKeyDown={e => { if (e.key === 'Enter') handleSaveTags(); if (e.key === 'Escape') setEditingTags(null); }}
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <Button variant="ghost" size="sm" onClick={() => setEditingTags(null)}>Cancel</Button>
                <Button size="sm" onClick={handleSaveTags} disabled={savingTags}>
                  {savingTags
                    ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Saving…</>
                    : <><Check className="w-3.5 h-3.5" /> Save Tags</>}
                </Button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
