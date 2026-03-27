import { motion } from 'framer-motion';
import { AlertTriangle, RefreshCw, Menu } from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { Button } from '@/components/ui/button';

export default function Header({ onMenuToggle }) {
  const { connected, autoStatus } = useSocket();

  // Rate-limit status derived from auto status (pushed via socket)
  const rateLimited =
    autoStatus.current?.toLowerCase().includes('rate limited') ||
    autoStatus.status === 'idle' && autoStatus.current?.toLowerCase().includes('cooldown');

  return (
    <header className="sticky top-0 z-30 flex items-center justify-between h-14 px-6 border-b border-border bg-background/80 backdrop-blur-xl">
      <div className="flex items-center gap-3">
        <button
          onClick={onMenuToggle}
          className="lg:hidden p-2 text-gray-400 hover:text-white"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>

      <div className="flex items-center gap-3">
        {rateLimited && (
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-warning-muted border border-amber-500/20"
          >
            <AlertTriangle className="w-4 h-4 text-amber-400" />
            <span className="text-xs text-amber-400">
              Rate limited
            </span>
          </motion.div>
        )}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => api.refreshPlaylist().catch(() => {})}
          title="Refresh playlist"
        >
          <RefreshCw className="w-4 h-4" />
        </Button>
      </div>
    </header>
  );
}
