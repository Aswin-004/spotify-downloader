import { NavLink } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Download,
  History,
  FolderOpen,
  Radio,
  ListMusic,
  ChevronLeft,
  ChevronRight,
  Disc3,
} from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { cn, capitalize } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';

const navItems = [
  { to: '/', icon: Download, label: 'Download' },
  { to: '/history', icon: History, label: 'History' },
  { to: '/files', icon: FolderOpen, label: 'Files' },
];

export default function Sidebar({ collapsed, onToggle }) {
  const { autoStatus, queueStatus, connected } = useSocket();

  const isAutoActive =
    autoStatus.status === 'downloading' || autoStatus.status === 'checking';

  return (
    <motion.aside
      initial={false}
      animate={{ width: collapsed ? 72 : 256 }}
      transition={{ duration: 0.2, ease: 'easeInOut' }}
      className="fixed left-0 top-0 bottom-0 z-40 flex flex-col border-r border-border bg-surface"
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 h-16 border-b border-border">
        <div className="flex-shrink-0 w-9 h-9 rounded-xl bg-primary/15 flex items-center justify-center">
          <Disc3 className="w-5 h-5 text-primary" />
        </div>
        {!collapsed && (
          <motion.span
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="font-bold text-lg tracking-tight whitespace-nowrap"
          >
            Spotify<span className="text-primary">DL</span>
          </motion.span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200',
                isActive
                  ? 'bg-primary/15 text-primary'
                  : 'text-gray-400 hover:text-white hover:bg-surface-light'
              )
            }
          >
            <Icon className="w-5 h-5 flex-shrink-0" />
            {!collapsed && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* Bottom section */}
      <div className="px-3 pb-4 space-y-3">
        {/* Auto Downloader Status */}
        <div
          className={cn(
            'rounded-xl p-3 border transition-colors',
            isAutoActive
              ? 'border-primary/30 bg-primary/5'
              : 'border-border bg-surface-light/50'
          )}
        >
          <div className="flex items-center gap-2">
            <Radio
              className={cn(
                'w-4 h-4 flex-shrink-0',
                isAutoActive ? 'text-primary animate-pulse' : 'text-gray-500'
              )}
            />
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium text-gray-300">
                  Auto Sync
                </div>
                <div className="text-xs text-gray-500 truncate">
                  {capitalize(autoStatus.status || 'idle')}
                  {autoStatus.playlist_total > 0 &&
                    ` · ${autoStatus.synced_total}/${autoStatus.playlist_total}`}
                </div>
              </div>
            )}
          </div>
          {!collapsed && autoStatus.current && (
            <div className="mt-2 text-[11px] text-gray-500 truncate">
              {autoStatus.current}
            </div>
          )}
        </div>

        {/* Queue Stats */}
        {!collapsed && (
          <div className="rounded-xl p-3 border border-border bg-surface-light/50">
            <div className="flex items-center gap-2 mb-2">
              <ListMusic className="w-4 h-4 text-gray-500" />
              <span className="text-xs font-medium text-gray-300">Queue</span>
            </div>
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <span>{queueStatus.completed}/{queueStatus.total}</span>
              {queueStatus.active_workers > 0 && (
                <Badge variant="default" className="text-[10px] px-1.5 py-0">
                  {queueStatus.active_workers} active
                </Badge>
              )}
            </div>
          </div>
        )}

        {/* Connection indicator */}
        <div className="flex items-center gap-2 px-3 py-2">
          <div
            className={cn(
              'w-2 h-2 rounded-full',
              connected ? 'bg-primary animate-pulse-slow' : 'bg-red-500'
            )}
          />
          {!collapsed && (
            <span className="text-[11px] text-gray-500">
              {connected ? 'Connected' : 'Disconnected'}
            </span>
          )}
        </div>

        {/* Collapse toggle */}
        <button
          onClick={onToggle}
          className="w-full flex items-center justify-center py-2 text-gray-500 hover:text-gray-300 transition-colors"
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <ChevronLeft className="w-4 h-4" />
          )}
        </button>
      </div>
    </motion.aside>
  );
}
