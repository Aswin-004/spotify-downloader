import { useState, useMemo, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Music, Search, ChevronRight, ChevronDown, AlertTriangle, RotateCw, Loader2, MoreHorizontal, FolderInput, X } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { useToast } from '@/components/ui/toast';

const GENRE_OPTIONS = [
  'House', 'Trance', 'UK Garage', 'Drum & Bass', 'Dubstep',
  'Techno', 'Grime', 'Electronic',
  'Bollywood', 'Punjabi', 'Tamil',
  'Hip Hop', 'R&B', 'Pop', 'Latin',
];

function extractArtist(filename) {
  const base = filename.replace(/\.mp3$/i, '');
  const parts = base.split(' - ');
  return parts.length > 1 ? parts[parts.length - 1].trim() : '';
}

function TrackArt({ path }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <Music className="w-4 h-4 text-blue-400 flex-shrink-0" />;
  return (
    <img
      src={`/api/artwork?path=${encodeURIComponent(path)}`}
      alt=""
      className="w-8 h-8 rounded object-cover flex-shrink-0"
      onError={() => setFailed(true)}
    />
  );
}

function FolderSkeleton() {
  return (
    <div className="rounded-xl border border-gray-700/50 bg-white/5 overflow-hidden animate-pulse">
      <div className="px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-5 h-5 rounded bg-gray-700/60" />
          <div className="w-32 h-4 rounded bg-gray-700/60" />
        </div>
        <div className="w-8 h-5 rounded-full bg-gray-700/60" />
      </div>
    </div>
  );
}

// Main LibraryPage Component
export default function LibraryPage() {
  const { files: socketFiles } = useSocket();
  const { addToast } = useToast();
  const [files, setFiles] = useState([]);
  const [search, setSearch] = useState('');
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [openMenu, setOpenMenu] = useState(null);       // file.path with picker open
  const [selectedGenres, setSelectedGenres] = useState({}); // { [path]: genre }
  const [moving, setMoving] = useState({});             // { [path]: boolean }

  async function handleMoveFromLibrary(file) {
    const genre = selectedGenres[file.path];
    if (!genre) return;
    setMoving((p) => ({ ...p, [file.path]: true }));
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
      setMoving((p) => ({ ...p, [file.path]: false }));
    }
  }

  function loadFiles() {
    setLoading(true);
    setError('');
    api.getFiles().then((data) => {
      if (data.files) setFiles(data.files);
    }).catch(() => {
      setError('Failed to load library. Check that the backend is running.');
    }).finally(() => {
      setLoading(false);
    });
  }

  useEffect(() => {
    loadFiles();
  }, []);

  useEffect(() => {
    if (socketFiles.length > 0) setFiles(socketFiles);
  }, [socketFiles]);

  const grouped = useMemo(() => {
    const q = search.toLowerCase();
    const filtered = search
      ? files.filter((f) => f.name.toLowerCase().includes(q))
      : files;

    const groups = {};
    filtered.forEach((f) => {
      const folder = f.folder || 'Root';
      if (!groups[folder]) groups[folder] = [];
      groups[folder].push(f);
    });
    return groups;
  }, [files, search]);

  const folderNames = useMemo(
    () => Object.keys(grouped).sort(),
    [grouped]
  );

  function toggleFolder(name) {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  // Expand all folders on initial load
  useEffect(() => {
    if (folderNames.length > 0 && expandedFolders.size === 0) {
      setExpandedFolders(new Set(folderNames.slice(0, 5)));
    }
  }, [folderNames]);

  const totalFiles = files.length;

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-10">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="space-y-3"
      >
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-4xl font-bold bg-gradient-to-r from-purple-400 to-blue-400 bg-clip-text text-transparent">
              Library
            </h1>
            <p className="text-gray-400 mt-2">Manage your downloaded tracks</p>
          </div>
          <div className="text-right">
            {loading ? (
              <Loader2 className="w-7 h-7 text-purple-400 animate-spin ml-auto" />
            ) : (
              <>
                <div className="text-3xl font-bold text-purple-400">{totalFiles}</div>
                <div className="text-sm text-gray-400">tracks</div>
              </>
            )}
          </div>
        </div>
      </motion.div>

      {/* Error banner */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/20"
          >
            <div className="flex items-center gap-3">
              <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />
              <span className="text-sm text-red-300">{error}</span>
            </div>
            <Button size="sm" variant="ghost" onClick={loadFiles} className="text-red-400 hover:text-red-300 shrink-0">
              <RotateCw className="w-3.5 h-3.5 mr-1" /> Retry
            </Button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Search */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.1 }}
        className="relative"
      >
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500 pointer-events-none" />
        <Input
          placeholder="Search tracks..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-10 bg-white/5 border-gray-700 text-white placeholder:text-gray-500"
        />
      </motion.div>

      {/* Track List */}
      <ScrollArea className="h-[600px]">
        <div className="pr-4 space-y-3">
          {loading ? (
            <>
              {[...Array(6)].map((_, i) => <FolderSkeleton key={i} />)}
            </>
          ) : folderNames.length === 0 ? (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex flex-col items-center justify-center py-12 text-center"
            >
              <Music className="w-12 h-12 text-gray-600 mb-4" />
              <p className="text-gray-400">
                {search ? 'No tracks found' : 'No tracks in library'}
              </p>
            </motion.div>
          ) : (
            <AnimatePresence>
              {folderNames.map((folder, idx) => (
                <motion.div
                  key={folder}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ delay: idx * 0.05 }}
                >
                  <Card className="bg-white/5 border-gray-700/50 overflow-hidden">
                    {/* Folder Header */}
                    <button
                      onClick={() => toggleFolder(folder)}
                      aria-expanded={expandedFolders.has(folder)}
                      aria-label={`${expandedFolders.has(folder) ? 'Collapse' : 'Expand'} ${folder}`}
                      className="w-full px-4 py-3 flex items-center justify-between hover:bg-white/[0.07] active:bg-white/10 transition-colors duration-150 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50"
                    >
                      <div className="flex items-center gap-3 flex-1">
                        {expandedFolders.has(folder) ? (
                          <ChevronDown className="w-5 h-5 text-purple-400" />
                        ) : (
                          <ChevronRight className="w-5 h-5 text-gray-500" />
                        )}
                        <span className="font-semibold text-gray-200">{folder}</span>
                      </div>
                      <Badge variant="secondary" className="bg-purple-500/20 text-purple-300">
                        {grouped[folder].length}
                      </Badge>
                    </button>

                    {/* Tracks */}
                    <AnimatePresence>
                      {expandedFolders.has(folder) && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                          className="border-t border-gray-700/50 divide-y divide-gray-700/50"
                        >
                          {grouped[folder].map((file, fileIdx) => (
                            <motion.div
                              key={file.path}
                              initial={{ opacity: 0, x: -20 }}
                              animate={{ opacity: 1, x: 0 }}
                              transition={{ delay: fileIdx * 0.02 }}
                              className="px-4 py-3 flex items-center hover:bg-white/[0.07] transition-colors duration-150 group/track"
                            >
                              <div className="flex items-center gap-3 flex-1 min-w-0">
                                <TrackArt path={file.path} />
                                <div className="min-w-0">
                                  <div className="text-sm font-medium text-gray-200 truncate">
                                    {file.name}
                                  </div>
                                  <div className="text-xs text-gray-500">
                                    {file.mtime ? new Date(file.mtime * 1000).toLocaleDateString() : 'Unknown'}
                                  </div>
                                </div>
                              </div>

                              {/* Inline genre picker or ... button */}
                              {openMenu === file.path ? (
                                <div className="flex items-center gap-2 ml-3 flex-shrink-0">
                                  <select
                                    aria-label="Select genre"
                                    value={selectedGenres[file.path] || ''}
                                    onChange={(e) =>
                                      setSelectedGenres((p) => ({ ...p, [file.path]: e.target.value }))
                                    }
                                    className="text-xs bg-gray-800 border border-gray-600 text-gray-200 rounded-lg px-2 py-1.5 cursor-pointer focus:outline-none focus:ring-2 focus:ring-purple-500/60 transition-colors hover:border-gray-500"
                                  >
                                    <option value="">Pick genre…</option>
                                    {GENRE_OPTIONS.map((g) => (
                                      <option key={g} value={g}>{g}</option>
                                    ))}
                                  </select>
                                  <Button
                                    size="sm"
                                    disabled={!selectedGenres[file.path] || moving[file.path]}
                                    onClick={() => handleMoveFromLibrary(file)}
                                    className="text-xs h-7 px-2.5 bg-purple-600 hover:bg-purple-500 active:scale-95 text-white disabled:opacity-40 transition-all duration-150"
                                  >
                                    {moving[file.path] ? (
                                      <span className="flex items-center gap-1">
                                        <Loader2 className="w-3 h-3 animate-spin" />
                                        Moving…
                                      </span>
                                    ) : (
                                      <span className="flex items-center gap-1">
                                        <FolderInput className="w-3 h-3" />
                                        Move
                                      </span>
                                    )}
                                  </Button>
                                  <button
                                    aria-label="Cancel"
                                    onClick={() => setOpenMenu(null)}
                                    className="p-1.5 text-gray-500 hover:text-gray-300 active:scale-95 transition-all duration-150 cursor-pointer rounded-lg hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
                                  >
                                    <X className="w-3.5 h-3.5" />
                                  </button>
                                </div>
                              ) : (
                                <button
                                  aria-label="Move to genre"
                                  onClick={() => setOpenMenu(file.path)}
                                  className="ml-3 p-1.5 min-w-[32px] min-h-[32px] text-gray-600 hover:text-gray-300 sm:opacity-0 sm:group-hover/track:opacity-100 focus:opacity-100 active:scale-95 transition-all duration-150 cursor-pointer rounded-lg hover:bg-white/10 flex-shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
                                >
                                  <MoreHorizontal className="w-4 h-4" />
                                </button>
                              )}
                            </motion.div>
                          ))}
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </Card>
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
