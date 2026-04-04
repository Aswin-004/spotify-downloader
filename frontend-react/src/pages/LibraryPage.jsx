import { useState, useMemo, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Music,
  Search,
  ChevronRight,
  ChevronDown,
  Play,
  Loader2,
  CheckCircle2,
  AlertCircle,
  AlertTriangle,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { cn } from '@/lib/utils';

// OrganizePanel Component
function OrganizePanel() {
  const [isExpanded, setIsExpanded] = useState(false);
  const [selectedMode, setSelectedMode] = useState('artist');
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const modes = [
    { value: 'artist', label: 'By artist', description: 'downloads/Artist/Song.mp3' },
    { value: 'genre', label: 'By genre', description: 'downloads/Genre/Song.mp3' },
    { value: 'artist_genre', label: 'Genre → Artist', description: 'downloads/Genre/Artist/Song.mp3' },
  ];

  async function handleOrganize() {
    setIsLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await api.organize({ mode: selectedMode });
      setResult(data);
    } catch (err) {
      setError(err.message || 'Failed to organize library');
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="mb-6"
    >
      <Card className="bg-gradient-to-r from-purple-500/10 to-blue-500/10 border-purple-500/30">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors"
        >
          <div className="flex items-center gap-3">
            {isExpanded ? (
              <ChevronDown className="w-5 h-5 text-purple-400" />
            ) : (
              <ChevronRight className="w-5 h-5 text-purple-400" />
            )}
            <span className="font-semibold text-purple-300">Organize Library</span>
          </div>
          <div className="text-sm text-gray-400">
            {result && `${result.moved} moved · ${result.skipped} skipped · ${result.errors.length} errors`}
          </div>
        </button>

        <AnimatePresence>
          {isExpanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.3 }}
              className="border-t border-purple-500/30 px-6 py-4 space-y-4"
            >
              {/* Mode Selection */}
              <div className="space-y-3">
                <label className="text-sm font-medium text-gray-300">Organization Mode:</label>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {modes.map((mode) => (
                    <button
                      key={mode.value}
                      onClick={() => setSelectedMode(mode.value)}
                      className={cn(
                        'p-3 rounded-lg border-2 transition-all text-left',
                        selectedMode === mode.value
                          ? 'border-purple-500 bg-purple-500/20 text-purple-300'
                          : 'border-gray-700 bg-transparent text-gray-400 hover:border-purple-400'
                      )}
                    >
                      <div className="font-medium text-sm">{mode.label}</div>
                      <div className="text-xs text-gray-500 mt-1">{mode.description}</div>
                    </button>
                  ))}
                </div>
              </div>

              {/* Action Button */}
              <Button
                onClick={handleOrganize}
                disabled={isLoading}
                className="w-full bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700 disabled:opacity-50"
              >
                {isLoading ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Organizing...
                  </>
                ) : (
                  'Run now'
                )}
              </Button>

              {/* Result Display */}
              {result && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30"
                >
                  <div className="flex items-start gap-3">
                    <CheckCircle2 className="w-5 h-5 text-emerald-400 mt-0.5 flex-shrink-0" />
                    <div className="space-y-1">
                      <div className="text-sm font-medium text-emerald-300">Organization Complete</div>
                      <div className="text-sm text-emerald-200/80">
                        {result.moved} moved · {result.skipped} skipped · {result.errors.length} errors
                      </div>
                      {result.errors.length > 0 && (
                        <div className="mt-2 text-xs text-red-300">
                          {result.errors.slice(0, 2).map((err, i) => (
                            <div key={i}>{err.file}: {err.error}</div>
                          ))}
                          {result.errors.length > 2 && (
                            <div>... and {result.errors.length - 2} more errors</div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </motion.div>
              )}

              {/* Error Display */}
              {error && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="p-3 rounded-lg bg-red-500/10 border border-red-500/30"
                >
                  <div className="flex items-start gap-3">
                    <AlertCircle className="w-5 h-5 text-red-400 mt-0.5 flex-shrink-0" />
                    <div>
                      <div className="text-sm font-medium text-red-300">Organization Failed</div>
                      <div className="text-sm text-red-200/80">{error}</div>
                    </div>
                  </div>
                </motion.div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </motion.div>
  );
}

// Main LibraryPage Component
export default function LibraryPage() {
  const { files: socketFiles } = useSocket();
  const [files, setFiles] = useState([]);
  const [search, setSearch] = useState('');
  const [expandedFolders, setExpandedFolders] = useState(new Set());

  useEffect(() => {
    api.getFiles().then((data) => {
      if (data.files) setFiles(data.files);
    }).catch(() => {});
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
            <div className="text-3xl font-bold text-purple-400">{totalFiles}</div>
            <div className="text-sm text-gray-400">tracks</div>
          </div>
        </div>
      </motion.div>

      {/* Organize Panel */}
      <OrganizePanel />

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
          {folderNames.length === 0 ? (
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
                      className="w-full px-4 py-3 flex items-center justify-between hover:bg-white/5 transition-colors"
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
                              className="px-4 py-3 flex items-center justify-between hover:bg-white/5 transition-colors group"
                            >
                              <div className="flex items-center gap-3 flex-1 min-w-0">
                                <Music className="w-4 h-4 text-blue-400 flex-shrink-0" />
                                <div className="min-w-0">
                                  <div className="text-sm font-medium text-gray-200 truncate">
                                    {file.name}
                                  </div>
                                  <div className="text-xs text-gray-500">
                                    {file.mtime ? new Date(file.mtime * 1000).toLocaleDateString() : 'Unknown'}
                                  </div>
                                </div>
                              </div>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="opacity-0 group-hover:opacity-100 transition-opacity ml-2"
                              >
                                <Play className="w-4 h-4" />
                              </Button>
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
