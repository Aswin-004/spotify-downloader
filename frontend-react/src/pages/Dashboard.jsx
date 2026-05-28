import { useState, useMemo, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { motion, AnimatePresence, LayoutGroup } from 'framer-motion';
import {
  Loader2, CheckCircle2, SkipForward, XCircle,
  Download, Inbox, BookOpen, X, Settings, AlertTriangle, StopCircle,
} from 'lucide-react';
import DownloadInput from '@/components/DownloadInput';
import StatsCards from '@/components/StatsCards';
import DownloadCard from '@/components/DownloadCard';
import ActivityFeed from '@/components/ActivityFeed';
import { useSocket } from '@/hooks/useSocket';
import { cn } from '@/lib/utils';
import { api } from '@/services/api';
import { ease } from '@/lib/motion';

const TABS = [
  { id: 'downloading', label: 'Downloading', icon: Loader2,      spin: true  },
  { id: 'completed',   label: 'Completed',   icon: CheckCircle2               },
  { id: 'skipped',     label: 'Skipped',     icon: SkipForward                },
  { id: 'failed',      label: 'Failed',      icon: XCircle                    },
];

const TAB_ACCENT = {
  downloading: 'var(--accent-cyan)',
  completed:   'var(--accent-emerald)',
  skipped:     'var(--accent-slate)',
  failed:      'var(--accent-rose)',
};

const EMPTY = {
  downloading: { icon: Download,      text: 'No active downloads',   sub: 'Tracks appear here when downloading starts' },
  completed:   { icon: CheckCircle2,  text: 'Nothing downloaded yet', sub: 'Paste a Spotify URL above to get started'   },
  skipped:     { icon: SkipForward,   text: 'No skipped tracks',     sub: 'Duplicates appear here'                      },
  failed:      { icon: XCircle,       text: 'No failed downloads',   sub: 'Failed tracks appear here for review'        },
};

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('downloading');
  const { downloads, ingestProgress, clearDownloads } = useSocket();
  const [showGuide,    setShowGuide]    = useState(() => !localStorage.getItem('onboarding_seen'));
  const [setupComplete, setSetupComplete] = useState(null);

  useEffect(() => {
    api.getAppConfig()
      .then(cfg => setSetupComplete(cfg._setup_complete !== false))
      .catch(() => setSetupComplete(true));
  }, []);

  const counts = useMemo(() => ({
    downloading: Object.keys(downloads.downloading).length,
    completed:   Object.keys(downloads.completed).length,
    skipped:     Object.keys(downloads.skipped).length,
    failed:      Object.keys(downloads.failed).length,
  }), [downloads]);

  const items = useMemo(
    () => Object.values(downloads[activeTab] || {}),
    [downloads, activeTab]
  );

  // Auto-switch to Completed when downloads finish
  useEffect(() => {
    if (counts.downloading === 0 && activeTab === 'downloading' && counts.completed > 0)
      setActiveTab('completed');
  }, [counts.downloading]);

  const totalActivity = counts.downloading + counts.completed + counts.skipped + counts.failed;
  const accent        = TAB_ACCENT[activeTab];
  const empty         = EMPTY[activeTab];
  const EmptyIcon     = empty.icon;

  return (
    <div className="max-w-3xl mx-auto space-y-5">

      {/* Setup banner */}
      <AnimatePresence>
        {setupComplete === false && (
          <motion.div
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
            className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl"
            style={{ background: 'var(--accent-amber-dim)', border: '1px solid rgba(245,158,11,0.25)' }}
          >
            <div className="flex items-center gap-3">
              <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--accent-amber)' }} />
              <span className="text-13" style={{ color: 'var(--text-secondary)' }}>
                <strong style={{ color: 'var(--accent-amber)' }}>Setup required:</strong>{' '}
                Add Spotify credentials and music folder in{' '}
                <Link to="/settings" className="underline" style={{ color: 'var(--text-accent)' }}>Settings</Link>.
              </span>
            </div>
            <Link to="/settings"
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-12 font-medium flex-shrink-0 transition-opacity hover:opacity-80"
                  style={{ background: 'var(--accent-amber-dim)', color: 'var(--accent-amber)', border: '1px solid rgba(245,158,11,0.2)' }}>
              <Settings className="w-3.5 h-3.5" />
              Open Settings
            </Link>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Guide banner */}
      <AnimatePresence>
        {showGuide && (
          <motion.div
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
            className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl"
            style={{ background: 'var(--accent-violet-dim)', border: '1px solid rgba(139,92,246,0.2)' }}
          >
            <div className="flex items-center gap-3">
              <BookOpen className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--accent-violet)' }} />
              <span className="text-13" style={{ color: 'var(--text-secondary)' }}>
                New here? Read the{' '}
                <Link to="/getting-started" className="underline" style={{ color: 'var(--text-accent)' }}>
                  Getting Started guide
                </Link>{' '}
                to set up your API keys and understand the workflow.
              </span>
            </div>
            <button
              onClick={() => { setShowGuide(false); localStorage.setItem('onboarding_seen', '1'); }}
              className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg cursor-pointer focus-ring transition-colors"
              style={{ color: 'var(--text-tertiary)' }}
              aria-label="Dismiss guide"
            >
              <X className="w-4 h-4" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Download input */}
      <DownloadInput />

      {/* KPI strip */}
      <StatsCards />

      {/* Ingest progress bar */}
      <AnimatePresence>
        {ingestProgress.active && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="rounded-xl p-3"
            style={{ background: 'var(--accent-cyan-dim)', border: '1px solid rgba(6,182,212,0.2)' }}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--accent-cyan)' }} />
                <span className="text-13 font-medium" style={{ color: 'var(--accent-cyan)' }}>
                  Auto-syncing playlist
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-12 tabular-nums" style={{ color: 'var(--text-tertiary)' }}>
                  {ingestProgress.current}/{ingestProgress.total}
                </span>
                <button
                  onClick={() => api.stopSync().catch(() => {})}
                  className="flex items-center gap-1 text-12 px-2 py-1 rounded cursor-pointer transition-all focus-ring"
                  style={{ color: 'var(--accent-rose)', border: '1px solid rgba(244,63,94,0.2)' }}
                  aria-label="Stop sync"
                >
                  <StopCircle className="w-3.5 h-3.5" /> Stop
                </button>
              </div>
            </div>
            {/* Progress bar */}
            <div className="w-full h-1 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
              <motion.div
                className="h-full rounded-full"
                style={{ background: 'var(--accent-cyan)' }}
                animate={{ width: `${ingestProgress.percent ?? 0}%` }}
                transition={{ duration: 0.5, ease: 'easeOut' }}
              />
            </div>
            {ingestProgress.currentTrack && (
              <p className="text-11 truncate mt-1.5" style={{ color: 'var(--text-tertiary)' }}>
                {ingestProgress.currentTrack} — {ingestProgress.currentArtist}
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Tabs + track list */}
      {(totalActivity > 0 || ingestProgress.active) && (
        <LayoutGroup>
          <div>
            {/* Tab bar */}
            <div className="flex items-center gap-1 p-1 rounded-xl"
                 style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}>
              {TABS.map(tab => {
                const Icon    = tab.icon;
                const count   = counts[tab.id];
                const isActive = activeTab === tab.id;
                const tabAccent = TAB_ACCENT[tab.id];

                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className="relative flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-12 font-medium cursor-pointer transition-colors duration-150 focus-ring z-10"
                    style={{
                      color: isActive ? tabAccent : 'var(--text-tertiary)',
                    }}
                  >
                    {isActive && (
                      <motion.div
                        layoutId="tab-indicator"
                        className="absolute inset-0 rounded-lg"
                        style={{ background: `${tabAccent}12`, border: `1px solid ${tabAccent}25` }}
                        transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                      />
                    )}
                    <span className="relative flex items-center gap-1.5 z-10">
                      <Icon className={cn('w-3.5 h-3.5', tab.spin && isActive && count > 0 && 'animate-spin')} />
                      <span className="hidden sm:inline">{tab.label}</span>
                      {count > 0 && (
                        <motion.span
                          key={count}
                          initial={{ scale: 1.3 }}
                          animate={{ scale: 1 }}
                          transition={{ type: 'spring', stiffness: 500 }}
                          className="font-mono text-10 px-1.5 py-0.5 rounded-full tabular-nums"
                          style={{ background: 'rgba(255,255,255,0.08)', color: isActive ? tabAccent : 'var(--text-muted)' }}
                        >
                          {count}
                        </motion.span>
                      )}
                    </span>
                  </button>
                );
              })}

              {/* Clear button */}
              {activeTab !== 'downloading' && counts[activeTab] > 0 && (
                <button
                  onClick={() => clearDownloads(activeTab)}
                  className="ml-auto text-11 px-2 py-1 rounded cursor-pointer transition-all focus-ring"
                  style={{ color: 'var(--text-muted)' }}
                  aria-label={`Clear ${activeTab}`}
                >
                  Clear
                </button>
              )}
            </div>

            {/* Tab content */}
            <AnimatePresence mode="wait">
              <motion.div
                key={activeTab}
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0  }}
                exit={{ opacity: 0, x: -12   }}
                transition={ease}
                className="mt-3"
              >
                {items.length === 0 ? (
                  <motion.div
                    initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                    className="flex flex-col items-center justify-center py-14 rounded-xl"
                    style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}
                  >
                    {/* Waveform animation for downloading empty state */}
                    {activeTab === 'downloading' ? (
                      <div className="flex items-end gap-1 h-6 mb-4">
                        {[1,2,3,4,5,6,7].map(i => (
                          <div key={i} className="waveform-bar" style={{ height: 10 }} />
                        ))}
                      </div>
                    ) : (
                      <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-4"
                           style={{ background: 'var(--surface-1)' }}>
                        <EmptyIcon className="w-5 h-5" style={{ color: 'var(--text-muted)' }} />
                      </div>
                    )}
                    <p className="text-13 font-medium" style={{ color: 'var(--text-tertiary)' }}>{empty.text}</p>
                    <p className="text-11 mt-1" style={{ color: 'var(--text-muted)' }}>{empty.sub}</p>
                  </motion.div>
                ) : (
                  <div className="space-y-2">
                    <AnimatePresence initial={false}>
                      {items.map((item, i) => (
                        <DownloadCard key={`${item.title}__${item.artist}`} item={item} index={i} />
                      ))}
                    </AnimatePresence>
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </LayoutGroup>
      )}

      {/* Mobile activity feed (xl+ shows in right panel via Layout) */}
      <div className="xl:hidden rounded-xl overflow-hidden" style={{ height: 360, border: '1px solid var(--border-subtle)', background: 'var(--void)' }}>
        <ActivityFeed />
      </div>
    </div>
  );
}
