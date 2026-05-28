import { useEffect, useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { FolderOpen, Music, Search, ChevronRight, HardDrive, Tags, Loader2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { ease } from '@/lib/motion';

export default function Files() {
  const { files: socketFiles, retagProgress } = useSocket();
  const [files, setFiles] = useState([]);
  const [search, setSearch] = useState('');
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [retagging, setRetagging] = useState(false);

  useEffect(() => {
    api.getFiles().then(data => { if (data.files) setFiles(data.files); }).catch(() => {});
  }, []);

  useEffect(() => {
    if (socketFiles.length > 0) setFiles(socketFiles);
  }, [socketFiles]);

  const grouped = useMemo(() => {
    const q = search.toLowerCase();
    const filtered = search ? files.filter(f => f.name.toLowerCase().includes(q)) : files;
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
  }

  useEffect(() => {
    if (folderNames.length > 0 && expandedFolders.size === 0)
      setExpandedFolders(new Set(folderNames.slice(0, 5)));
  }, [folderNames]);

  async function handleRetag() {
    setRetagging(true);
    try { await api.retagLibrary(); } catch { setRetagging(false); }
  }

  useEffect(() => {
    if (retagProgress?.status === 'complete' || retagProgress?.status === 'error')
      setRetagging(false);
  }, [retagProgress]);

  const pct = retagProgress?.percentage ?? 0;

  return (
    <div className="max-w-4xl mx-auto space-y-5 pb-10">

      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={ease}
        className="flex items-center justify-between"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center"
               style={{ background: 'var(--accent-cyan-dim)' }}>
            <HardDrive className="w-4.5 h-4.5" style={{ color: 'var(--accent-cyan)' }} />
          </div>
          <div>
            <h1 className="font-display text-22 font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
              Music Library
            </h1>
            <p className="text-11 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
              {files.length} files · {folderNames.length} folders
            </p>
          </div>
        </div>
        <Button variant="secondary" size="sm" onClick={handleRetag} disabled={retagging}>
          {retagging
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <Tags className="w-3.5 h-3.5" />
          }
          {retagging ? 'Retagging…' : 'Retag Library'}
        </Button>
      </motion.div>

      {/* Retag progress bar */}
      <AnimatePresence>
        {retagProgress?.status === 'processing' && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
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
              <motion.div
                className="h-full rounded-full"
                style={{ background: 'var(--accent-violet)' }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.5, ease: 'easeOut' }}
              />
            </div>
            {retagProgress.current_file && (
              <p className="text-10 truncate" style={{ color: 'var(--text-tertiary)' }}>
                {retagProgress.current_file}
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Search */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ ...ease, delay: 0.08 }}
        className="relative"
      >
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 pointer-events-none"
                style={{ color: 'var(--text-muted)' }} />
        <Input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search files..."
          className="pl-9"
        />
      </motion.div>

      {/* File tree */}
      <div className="rounded-xl overflow-hidden"
           style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}>
        <ScrollArea className="max-h-[calc(100vh-280px)]">
          {folderNames.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-3"
                   style={{ background: 'var(--surface-1)' }}>
                <FolderOpen className="w-5 h-5" style={{ color: 'var(--text-muted)' }} />
              </div>
              <p className="text-13 font-medium" style={{ color: 'var(--text-tertiary)' }}>No files found</p>
              <p className="text-11 mt-1" style={{ color: 'var(--text-muted)' }}>
                {search ? 'Try a different search term' : 'Your music library appears empty'}
              </p>
            </div>
          ) : (
            <div>
              {folderNames.map((folder, fi) => {
                const items = grouped[folder];
                const isExpanded = expandedFolders.has(folder);

                return (
                  <motion.div
                    key={folder}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: Math.min(fi * 0.03, 0.2) }}
                    style={{ borderTop: fi > 0 ? '1px solid var(--border-subtle)' : 'none' }}
                  >
                    {/* Folder header */}
                    <button
                      onClick={() => toggleFolder(folder)}
                      className="w-full flex items-center gap-3 px-5 py-3 transition-colors duration-150 cursor-pointer focus-ring"
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-1)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <motion.div animate={{ rotate: isExpanded ? 90 : 0 }} transition={{ duration: 0.15 }}>
                        <ChevronRight className="w-4 h-4"
                          style={{ color: isExpanded ? 'var(--accent-violet)' : 'var(--text-muted)' }} />
                      </motion.div>
                      <FolderOpen className="w-4 h-4 flex-shrink-0"
                        style={{ color: isExpanded ? 'var(--accent-violet)' : 'var(--text-tertiary)' }} />
                      <span className="text-13 font-medium flex-1 text-left truncate"
                            style={{ color: isExpanded ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                        {folder}
                      </span>
                      <span className="text-10 font-mono tabular-nums px-2 py-0.5 rounded-full flex-shrink-0"
                            style={{ background: 'var(--surface-2)', color: 'var(--text-tertiary)' }}>
                        {items.length}
                      </span>
                    </button>

                    {/* Folder contents */}
                    <AnimatePresence>
                      {isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                          className="overflow-hidden"
                          style={{ borderTop: '1px solid var(--border-subtle)' }}
                        >
                          {items.map((file, i) => (
                            <div
                              key={`${file.name}-${i}`}
                              className="flex items-center gap-2.5 pl-14 pr-5 py-2 transition-colors duration-100 group"
                              style={{ borderTop: i > 0 ? '1px solid var(--border-subtle)' : 'none' }}
                              onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-1)'}
                              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                            >
                              <Music className="w-3.5 h-3.5 flex-shrink-0 transition-colors duration-100"
                                     style={{ color: 'var(--text-muted)' }} />
                              <span className="text-12 truncate flex-1"
                                    style={{ color: 'var(--text-secondary)' }}>
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
      </div>
    </div>
  );
}
