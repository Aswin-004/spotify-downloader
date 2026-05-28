import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Command } from 'cmdk';
import {
  Download, History, FolderOpen, ListMusic, BarChart3,
  AlertTriangle, Wrench, Settings, BookOpen, Search,
  Zap, Layers,
} from 'lucide-react';
import { fadeBackdrop, scaleModal } from '@/lib/motion';

const PAGES = [
  { label: 'Download',    to: '/',                icon: Download,      group: 'Navigate' },
  { label: 'History',     to: '/history',         icon: History,       group: 'Navigate' },
  { label: 'Files',       to: '/files',           icon: FolderOpen,    group: 'Navigate' },
  { label: 'Library',     to: '/library',         icon: ListMusic,     group: 'Navigate' },
  { label: 'Analytics',   to: '/analytics',       icon: BarChart3,     group: 'Navigate' },
  { label: 'Review',      to: '/review',          icon: AlertTriangle, group: 'Navigate' },
  { label: 'Maintenance', to: '/maintenance',     icon: Wrench,        group: 'Navigate' },
  { label: 'Settings',    to: '/settings',        icon: Settings,      group: 'Navigate' },
  { label: 'Guide',       to: '/getting-started', icon: BookOpen,      group: 'Navigate' },
];

export default function CommandPalette({ open, onClose }) {
  const navigate = useNavigate();

  function runAction(to) {
    navigate(to);
    onClose();
  }

  // Close on outside click handled by backdrop

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            {...fadeBackdrop}
            className="fixed inset-0 z-50"
            style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(8px)' }}
            onClick={onClose}
          />

          {/* Panel */}
          <div className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh] pointer-events-none">
            <motion.div
              {...scaleModal}
              className="pointer-events-auto w-full max-w-lg rounded-2xl overflow-hidden shadow-modal"
              style={{
                background: 'var(--surface-2)',
                border: '1px solid var(--border-default)',
              }}
            >
              <Command className="w-full" loop>
                {/* Search input */}
                <div className="flex items-center gap-3 px-4 py-3"
                     style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <Search className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                  <Command.Input
                    placeholder="Go to page, search actions..."
                    className="flex-1 bg-transparent text-14 outline-none placeholder:text-[var(--text-tertiary)]"
                    style={{ color: 'var(--text-primary)' }}
                    autoFocus
                  />
                  <kbd className="text-10 px-1.5 py-0.5 rounded"
                       style={{ background: 'var(--surface-3)', color: 'var(--text-tertiary)', border: '1px solid var(--border-subtle)' }}>
                    ESC
                  </kbd>
                </div>

                <Command.List className="p-2 max-h-80 overflow-y-auto scrollbar-thin">
                  <Command.Empty>
                    <div className="py-6 text-center text-13" style={{ color: 'var(--text-tertiary)' }}>
                      No results found
                    </div>
                  </Command.Empty>

                  <Command.Group heading="Navigate"
                    className="mb-1 text-label px-2 py-1">
                    {PAGES.map(({ label, to, icon: Icon }) => (
                      <Command.Item
                        key={to}
                        value={label}
                        onSelect={() => runAction(to)}
                        className="flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer text-13 transition-colors"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        <Icon className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                        <span>{label}</span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                </Command.List>

                {/* Footer hint */}
                <div className="flex items-center gap-4 px-4 py-2"
                     style={{ borderTop: '1px solid var(--border-subtle)' }}>
                  {[
                    { keys: ['↑', '↓'], desc: 'navigate' },
                    { keys: ['↵'], desc: 'select' },
                    { keys: ['Esc'], desc: 'close' },
                  ].map(({ keys, desc }) => (
                    <div key={desc} className="flex items-center gap-1">
                      {keys.map(k => (
                        <kbd key={k} className="text-10 px-1 py-0.5 rounded"
                             style={{ background: 'var(--surface-3)', color: 'var(--text-tertiary)', border: '1px solid var(--border-subtle)' }}>
                          {k}
                        </kbd>
                      ))}
                      <span className="text-10 ml-1" style={{ color: 'var(--text-muted)' }}>{desc}</span>
                    </div>
                  ))}
                </div>
              </Command>
            </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>
  );
}
