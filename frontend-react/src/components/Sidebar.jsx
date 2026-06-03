import { NavLink } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Download, History, ListMusic, BarChart3,
  AlertTriangle, Wrench, Settings, BookOpen, Disc3,
  ChevronLeft, ChevronRight, Radio, Zap, Loader2,
  CheckCircle2, SkipForward, XCircle,
} from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { cn, capitalize } from '@/lib/utils';
import { ease, springGentle } from '@/lib/motion';

const navItems = [
  { to: '/',                icon: Download,      label: 'Download',    end: true  },
  { to: '/history',         icon: History,       label: 'History'               },
  { to: '/library',         icon: ListMusic,     label: 'Library'               },
  { to: '/analytics',       icon: BarChart3,     label: 'Analytics'             },
  { to: '/review',          icon: AlertTriangle, label: 'Review',  badge: true  },
  { to: '/maintenance',     icon: Wrench,        label: 'Maintenance'           },
  { to: '/settings',        icon: Settings,      label: 'Settings'              },
  { to: '/getting-started', icon: BookOpen,      label: 'Guide'                 },
];

export default function Sidebar({ collapsed, onToggle }) {
  const {
    autoStatus, connected, downloads, needsReviewCount, maintenanceRunning,
    queueStatus,
  } = useSocket();

  const dlCounts = {
    downloading: Object.keys(downloads.downloading).length,
    completed:   Object.keys(downloads.completed).length,
    skipped:     Object.keys(downloads.skipped).length,
    failed:      Object.keys(downloads.failed).length,
  };

  const totalActivity = dlCounts.downloading + dlCounts.completed + dlCounts.skipped + dlCounts.failed;
  const isAutoActive   = autoStatus.status === 'downloading' || autoStatus.status === 'checking';
  const isAutoError    = autoStatus.current?.startsWith('Fetch error') || autoStatus.current?.startsWith('Error');
  const isAIActive     = maintenanceRunning;

  return (
    <motion.aside
      initial={false}
      animate={{ width: collapsed ? 'var(--nav-icon-width)' : 'var(--nav-width)' }}
      transition={springGentle}
      className="fixed left-0 top-0 bottom-0 z-40 flex flex-col overflow-hidden"
      style={{ background: 'var(--void)', borderRight: '1px solid var(--border-subtle)' }}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 h-14 flex-shrink-0"
           style={{ borderBottom: '1px solid var(--border-subtle)' }}>
        <motion.div
          className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center"
          style={{ background: 'var(--accent-violet-dim)' }}
          animate={isAutoActive ? { rotate: 360 } : { rotate: 0 }}
          transition={isAutoActive ? { duration: 8, ease: 'linear', repeat: Infinity } : { duration: 0.3 }}
        >
          <Disc3 className="w-4 h-4" style={{ color: 'var(--accent-violet)' }} />
        </motion.div>
        <AnimatePresence>
          {!collapsed && (
            <motion.span
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ ...ease, duration: 0.15 }}
              className="font-display font-bold text-14 whitespace-nowrap tracking-tight"
              style={{ color: 'var(--text-primary)' }}
            >
              Obsidian<span style={{ color: 'var(--accent-violet)' }}>DJ</span>
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto scrollbar-none">
        {navItems.map(({ to, icon: Icon, label, end, badge }) => {
          const showBadge = badge && needsReviewCount > 0;
          return (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  'group relative flex items-center gap-3 rounded-lg transition-all duration-150 cursor-pointer',
                  collapsed ? 'px-0 py-2.5 justify-center' : 'px-3 py-2',
                  isActive ? 'nav-active' : ''
                )
              }
              style={({ isActive }) => ({
                color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                ...(!isActive && {}),
              })}
            >
              {({ isActive }) => (
                <>
                  {/* Active left bar */}
                  {isActive && (
                    <motion.div
                      layoutId="nav-indicator"
                      className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full"
                      style={{ background: 'var(--accent-violet)' }}
                      transition={springGentle}
                    />
                  )}

                  <div className="relative flex-shrink-0">
                    <Icon className="w-4 h-4" />
                    {/* Dot badge when collapsed */}
                    {showBadge && collapsed && (
                      <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full"
                            style={{ background: 'var(--accent-amber)' }} />
                    )}
                  </div>

                  <AnimatePresence>
                    {!collapsed && (
                      <motion.span
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.1 }}
                        className="flex-1 text-13 font-medium whitespace-nowrap"
                      >
                        {label}
                      </motion.span>
                    )}
                  </AnimatePresence>

                  {/* Count badge */}
                  {!collapsed && showBadge && (
                    <span className="text-11 font-bold px-1.5 py-0.5 rounded-full tabular-nums"
                          style={{
                            background: 'var(--accent-amber-dim)',
                            color: 'var(--accent-amber)',
                            border: '1px solid rgba(245,158,11,0.25)',
                          }}>
                      {needsReviewCount}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          );
        })}
      </nav>

      {/* Bottom status section */}
      <div className="px-2 pb-3 space-y-2 flex-shrink-0"
           style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}>

        {/* Queue pill */}
        {totalActivity > 0 && (
          <div className="rounded-lg px-3 py-2 flex items-center gap-2"
               style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}>
            {dlCounts.downloading > 0 && (
              <Loader2 className="w-3.5 h-3.5 animate-spin flex-shrink-0"
                       style={{ color: 'var(--accent-cyan)' }} />
            )}
            <AnimatePresence>
              {!collapsed && (
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                            className="flex items-center gap-2 text-11 min-w-0">
                  {dlCounts.downloading > 0 && (
                    <span className="font-mono tabular-nums" style={{ color: 'var(--accent-cyan)' }}>
                      {dlCounts.downloading} active
                    </span>
                  )}
                  {dlCounts.completed > 0 && (
                    <span className="font-mono tabular-nums" style={{ color: 'var(--accent-emerald)' }}>
                      {dlCounts.completed} done
                    </span>
                  )}
                  {dlCounts.failed > 0 && (
                    <span className="font-mono tabular-nums" style={{ color: 'var(--accent-rose)' }}>
                      {dlCounts.failed} failed
                    </span>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {/* Auto-sync status */}
        <div className="rounded-lg px-3 py-2 flex items-center gap-2"
             style={{
               background: isAutoActive ? 'var(--accent-cyan-dim)' : isAutoError ? 'var(--accent-rose-dim)' : 'var(--surface-0)',
               border: `1px solid ${isAutoActive ? 'rgba(6,182,212,0.2)' : isAutoError ? 'rgba(244,63,94,0.2)' : 'var(--border-subtle)'}`,
             }}>
          <Radio className="w-3.5 h-3.5 flex-shrink-0"
                 style={{ color: isAutoActive ? 'var(--accent-cyan)' : isAutoError ? 'var(--accent-rose)' : 'var(--text-tertiary)' }} />
          <AnimatePresence>
            {!collapsed && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                          className="flex-1 min-w-0">
                <div className="text-11 font-medium truncate"
                     style={{ color: isAutoActive ? 'var(--accent-cyan)' : isAutoError ? 'var(--accent-rose)' : 'var(--text-tertiary)' }}>
                  {isAutoError ? 'Sync error' : isAutoActive ? 'Auto syncing' : `Auto sync · ${capitalize(autoStatus.status || 'idle')}`}
                </div>
                {isAutoActive && autoStatus.synced_total != null && (
                  <div className="text-10 tabular-nums" style={{ color: 'var(--text-tertiary)' }}>
                    {autoStatus.synced_total}/{autoStatus.playlist_total} tracks
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* AI status + connection */}
        <div className="flex items-center gap-2 px-2 py-1">
          {/* Connection dot */}
          <div className={cn('w-2 h-2 rounded-full flex-shrink-0', connected ? 'glow-dot-emerald' : 'animate-pulse-slow')}
               style={{ background: connected ? 'var(--accent-emerald)' : 'var(--accent-rose)' }} />
          <AnimatePresence>
            {!collapsed && (
              <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                           className="text-11 flex-1" style={{ color: 'var(--text-tertiary)' }}>
                {connected ? 'Connected' : 'Disconnected'}
              </motion.span>
            )}
          </AnimatePresence>

          {/* AI pulse when running */}
          {isAIActive && (
            <div className="w-2 h-2 rounded-full flex-shrink-0 glow-dot-violet"
                 style={{ background: 'var(--accent-violet)' }} />
          )}
          <AnimatePresence>
            {!collapsed && isAIActive && (
              <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                           className="text-11" style={{ color: 'var(--accent-violet)' }}>
                AI
              </motion.span>
            )}
          </AnimatePresence>
        </div>

        {/* Collapse toggle */}
        <button
          onClick={onToggle}
          className="w-full flex items-center justify-center py-1.5 rounded-lg transition-all duration-150 cursor-pointer focus-ring"
          style={{ color: 'var(--text-tertiary)' }}
          title={collapsed ? 'Expand sidebar ([)' : 'Collapse sidebar ([)'}
        >
          {collapsed
            ? <ChevronRight className="w-3.5 h-3.5" />
            : <ChevronLeft  className="w-3.5 h-3.5" />}
        </button>
      </div>
    </motion.aside>
  );
}
