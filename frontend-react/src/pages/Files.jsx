import { useEffect, useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FolderOpen,
  Music,
  Search,
  ChevronRight,
  ChevronDown,
  HardDrive,
  Tags,
  Loader2,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { cn } from '@/lib/utils';

export default function Files() {
  const { files: socketFiles, retagProgress } = useSocket();
  const [files, setFiles] = useState([]);
  const [search, setSearch] = useState('');
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [retagging, setRetagging] = useState(false); // MUSICBRAINZ

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

  // MUSICBRAINZ — handle retag button click
  async function handleRetag() {
    setRetagging(true);
    try {
      await api.retagLibrary();
    } catch {
      setRetagging(false);
    }
  }

  // MUSICBRAINZ — stop spinner when retag completes
  useEffect(() => {
    if (retagProgress?.status === 'complete' || retagProgress?.status === 'error') {
      setRetagging(false);
    }
  }, [retagProgress]);

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center justify-between"
      >
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-primary/10">
            <HardDrive className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Music Library</h1>
            <p className="text-sm text-gray-500">
              {files.length} files · {folderNames.length} folders
            </p>
          </div>
        </div>
        {/* MUSICBRAINZ — Retag Library button */}
        <Button
          variant="secondary"
          size="sm"
          onClick={handleRetag}
          disabled={retagging}
        >
          {retagging ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Tags className="w-3.5 h-3.5" />
          )}
          {retagging ? 'Retagging...' : 'Retag Library'}
        </Button>
      </motion.div>

      {/* MUSICBRAINZ — Retag progress bar */}
      {retagProgress && retagProgress.status === 'processing' && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          className="rounded-xl border border-primary/20 bg-primary/5 p-3 space-y-2"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Loader2 className="w-4 h-4 text-primary animate-spin" />
              <span className="text-sm font-medium">Retagging Library</span>
            </div>
            <span className="text-xs font-mono text-gray-400">
              {retagProgress.current}/{retagProgress.total}
            </span>
          </div>
          <Progress value={retagProgress.percentage || 0} />
          <p className="text-xs text-gray-500 truncate">
            {retagProgress.current_file || 'Starting...'}
          </p>
        </motion.div>
      )}

      {/* Search */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
      >
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search files..."
            className="pl-9"
          />
        </div>
      </motion.div>

      {/* File tree */}
      <Card>
        <ScrollArea className="max-h-[calc(100vh-280px)]">
          {folderNames.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-gray-600">
              <FolderOpen className="w-10 h-10 mb-3" />
              <p className="text-sm">No files found</p>
            </div>
          ) : (
            <div className="divide-y divide-border">
              {folderNames.map((folder, fi) => {
                const items = grouped[folder];
                const isExpanded = expandedFolders.has(folder);

                return (
                  <motion.div
                    key={folder}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: Math.min(fi * 0.03, 0.3) }}
                  >
                    {/* Folder header */}
                    <button
                      onClick={() => toggleFolder(folder)}
                      className="w-full flex items-center gap-3 px-5 py-3 hover:bg-surface-light/30 transition-colors"
                    >
                      {isExpanded ? (
                        <ChevronDown className="w-4 h-4 text-gray-500" />
                      ) : (
                        <ChevronRight className="w-4 h-4 text-gray-500" />
                      )}
                      <FolderOpen
                        className={cn(
                          'w-4 h-4',
                          isExpanded ? 'text-primary' : 'text-gray-400'
                        )}
                      />
                      <span className="text-sm font-medium flex-1 text-left truncate">
                        {folder}
                      </span>
                      <Badge variant="secondary">{items.length}</Badge>
                    </button>

                    {/* Folder contents */}
                    <AnimatePresence>
                      {isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                          className="overflow-hidden"
                        >
                          {items.map((file, i) => (
                            <div
                              key={`${file.name}-${i}`}
                              className="flex items-center gap-3 pl-14 pr-5 py-2 hover:bg-surface-light/20 transition-colors group"
                            >
                              <Music className="w-3.5 h-3.5 text-gray-600 group-hover:text-primary transition-colors" />
                              <span className="text-sm text-gray-300 truncate flex-1">
                                {file.name}
                              </span>
                            </div>
                          ))}
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </motion.div>
                );
              })}
            </div>
          )}
        </ScrollArea>
      </Card>
    </div>
  );
}
