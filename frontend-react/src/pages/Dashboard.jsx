import { useState, useMemo } from 'react';
import { motion, AnimatePresence, LayoutGroup } from 'framer-motion';
import {
  Loader2,
  CheckCircle2,
  SkipForward,
  XCircle,
  Download,
  Inbox,
} from 'lucide-react';
import DownloadInput from '@/components/DownloadInput';
import StatsCards from '@/components/StatsCards';
import QueueCard from '@/components/QueueCard';
import DownloadCard from '@/components/DownloadCard';
import ActivityFeed from '@/components/ActivityFeed';
import { useSocket } from '@/hooks/useSocket';
import { cn } from '@/lib/utils';

const tabs = [
  { id: 'downloading', label: 'Downloading', icon: Loader2, color: 'text-yellow-400', activeColor: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30' },
  { id: 'completed', label: 'Completed', icon: CheckCircle2, color: 'text-emerald-400', activeColor: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
  { id: 'skipped', label: 'Skipped', icon: SkipForward, color: 'text-amber-400', activeColor: 'bg-amber-500/15 text-amber-400 border-amber-500/30' },
  { id: 'failed', label: 'Failed', icon: XCircle, color: 'text-red-400', activeColor: 'bg-red-500/15 text-red-400 border-red-500/30' },
];

const emptyMessages = {
  downloading: { icon: Download, text: 'No active downloads', sub: 'Tracks will appear here when downloading starts' },
  completed: { icon: CheckCircle2, text: 'No completed downloads', sub: 'Successfully downloaded tracks appear here' },
  skipped: { icon: SkipForward, text: 'No skipped tracks', sub: 'Duplicate or already-downloaded tracks appear here' },
  failed: { icon: XCircle, text: 'No failed downloads', sub: 'Failed downloads will appear here for review' },
};

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('downloading');
  const { downloads, ingestProgress } = useSocket();

  const counts = useMemo(() => ({
    downloading: Object.keys(downloads.downloading).length,
    completed: Object.keys(downloads.completed).length,
    skipped: Object.keys(downloads.skipped).length,
    failed: Object.keys(downloads.failed).length,
  }), [downloads]);

  const items = useMemo(
    () => Object.values(downloads[activeTab] || {}),
    [downloads, activeTab]
  );

  const totalActive = counts.downloading + counts.completed + counts.skipped + counts.failed;
  const empty = emptyMessages[activeTab];
  const EmptyIcon = empty.icon;

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <DownloadInput />
      <StatsCards />

      {/* Manual download progress */}
      <QueueCard />

      {/* Ingest overview bar */}
      {ingestProgress.active && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          className="rounded-xl border border-primary/20 bg-primary/5 p-3"
        >
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Loader2 className="w-4 h-4 text-primary animate-spin" />
              <span className="text-sm font-medium">
                Syncing Playlist
              </span>
            </div>
            <span className="text-xs font-mono text-gray-400">
              {ingestProgress.current}/{ingestProgress.total}
            </span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-white/5 overflow-hidden">
            <motion.div
              className="h-full rounded-full bg-primary"
              animate={{ width: `${ingestProgress.percent}%` }}
              transition={{ duration: 0.5, ease: 'easeOut' }}
            />
          </div>
          <p className="text-xs text-gray-500 mt-1.5 truncate">
            {ingestProgress.currentTrack} — {ingestProgress.currentArtist}
          </p>
        </motion.div>
      )}

      {/* Tab navigation */}
      {(totalActive > 0 || ingestProgress.active) && (
        <LayoutGroup>
        <div>
          <div className="relative flex gap-1 bg-surface/80 backdrop-blur rounded-xl p-1 border border-border">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const count = counts[tab.id];
              const isActive = activeTab === tab.id;

              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={cn(
                    'relative flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-xs sm:text-sm font-medium transition-colors duration-200 z-10',
                    isActive
                      ? tab.activeColor
                      : 'text-gray-500 hover:text-gray-300'
                  )}
                >
                  {isActive && (
                    <motion.div
                      layoutId="activeTabBg"
                      className="absolute inset-0 rounded-lg bg-white/[0.04] border border-white/[0.06]"
                      transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                    />
                  )}
                  <span className="relative z-10 flex items-center gap-2">
                    <Icon className={cn('w-4 h-4', tab.id === 'downloading' && isActive && 'animate-spin')} />
                    <span className="hidden sm:inline">{tab.label}</span>
                    {count > 0 && (
                      <motion.span
                        key={count}
                        initial={{ scale: 1.3 }}
                        animate={{ scale: 1 }}
                        transition={{ duration: 0.2, type: 'spring', stiffness: 500 }}
                        className={cn(
                          'text-[10px] font-mono px-1.5 py-0.5 rounded-full',
                          isActive ? 'bg-white/10' : 'bg-surface-light text-gray-400'
                        )}
                      >
                        {count}
                      </motion.span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Tab content */}
          <AnimatePresence mode="wait">
            <motion.div
              key={activeTab}
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -12 }}
              transition={{ duration: 0.2, ease: [0.25, 0.46, 0.45, 0.94] }}
              className="mt-4"
            >
              {items.length === 0 ? (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="flex flex-col items-center justify-center py-16 rounded-2xl border border-border bg-surface/50"
                >
                  <div className="w-14 h-14 rounded-2xl bg-surface-light flex items-center justify-center mb-4">
                    <EmptyIcon className="w-7 h-7 text-gray-600" />
                  </div>
                  <p className="text-sm font-medium text-gray-400">{empty.text}</p>
                  <p className="text-xs text-gray-600 mt-1">{empty.sub}</p>
                </motion.div>
              ) : (
                <div className="space-y-2">
                  <AnimatePresence initial={false}>
                    {items.map((item, i) => (
                      <DownloadCard key={item.title} item={item} index={i} />
                    ))}
                  </AnimatePresence>
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
        </LayoutGroup>
      )}

      {/* Mobile activity feed */}
      <div className="xl:hidden">
        <div className="rounded-2xl border border-border bg-surface overflow-hidden h-[400px]">
          <ActivityFeed />
        </div>
      </div>
    </div>
  );
}
