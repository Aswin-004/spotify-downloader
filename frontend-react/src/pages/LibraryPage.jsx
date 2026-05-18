import { useState, useMemo, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Music, Search, ChevronRight, ChevronDown } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';

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
                              className="px-4 py-3 flex items-center hover:bg-white/5 transition-colors"
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
