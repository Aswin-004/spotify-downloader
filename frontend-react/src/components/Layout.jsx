import { useState, useEffect, useCallback } from 'react';
import { Outlet } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { useHotkeys } from 'react-hotkeys-hook';
import Sidebar from '@/components/Sidebar';
import Header from '@/components/Header';
import ActivityFeed from '@/components/ActivityFeed';
import FooterDock from '@/components/FooterDock';
import PlayerBar from '@/components/PlayerBar';
import CommandPalette from '@/components/CommandPalette';
import { fadeBackdrop, springGentle } from '@/lib/motion';
import { usePlayer } from '@/context/PlayerContext';

const NAV_WIDTH_EXPANDED  = 220;
const NAV_WIDTH_COLLAPSED = 64;
const ACTIVITY_WIDTH      = 280;

export default function Layout() {
  const { nowPlaying } = usePlayer() || {};
  const [sidebarCollapsed,  setSidebarCollapsed]  = useState(false);
  const [activityOpen,      setActivityOpen]      = useState(true);
  const [mobileMenuOpen,    setMobileMenuOpen]    = useState(false);
  const [commandOpen,       setCommandOpen]       = useState(false);
  const [isXl,              setIsXl]              = useState(false);
  const [isLg,              setIsLg]              = useState(false);

  // Restore density preference
  useEffect(() => {
    const density = localStorage.getItem('ui-density') || 'default';
    document.documentElement.setAttribute('data-density', density);
  }, []);

  useEffect(() => {
    function check() {
      setIsXl(window.innerWidth >= 1280);
      setIsLg(window.innerWidth >= 1024);
    }
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  // Keyboard shortcuts
  useHotkeys('[', () => {
    if (isLg) setSidebarCollapsed(c => !c);
    else setMobileMenuOpen(o => !o);
  }, { preventDefault: true });

  useHotkeys('\\', () => setActivityOpen(o => !o), { preventDefault: true });

  useHotkeys('meta+k, ctrl+k', () => setCommandOpen(true), { preventDefault: true });

  useHotkeys('escape', () => {
    setCommandOpen(false);
    setMobileMenuOpen(false);
  });

  useHotkeys('d', () => {
    const current = document.documentElement.getAttribute('data-density') || 'default';
    const next = current === 'compact' ? 'default' : 'compact';
    document.documentElement.setAttribute('data-density', next);
    localStorage.setItem('ui-density', next);
  }, { preventDefault: true });

  const navWidth = isLg
    ? (sidebarCollapsed ? NAV_WIDTH_COLLAPSED : NAV_WIDTH_EXPANDED)
    : 0;

  const showActivity = isXl && activityOpen;

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--base)' }}>

      {/* Navigation Rail — desktop */}
      <div className="hidden lg:block flex-shrink-0">
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed(c => !c)}
        />
      </div>

      {/* Mobile sidebar overlay */}
      <AnimatePresence>
        {mobileMenuOpen && (
          <>
            <motion.div
              {...fadeBackdrop}
              className="fixed inset-0 z-30 lg:hidden"
              style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
              onClick={() => setMobileMenuOpen(false)}
            />
            <motion.div
              initial={{ x: -NAV_WIDTH_EXPANDED }}
              animate={{ x: 0 }}
              exit={{ x: -NAV_WIDTH_EXPANDED }}
              transition={springGentle}
              className="fixed left-0 top-0 bottom-0 z-40 lg:hidden"
            >
              <Sidebar collapsed={false} onToggle={() => setMobileMenuOpen(false)} />
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Main content area */}
      <div
        className="flex flex-1 flex-col overflow-hidden"
        style={{ marginLeft: isLg ? navWidth : 0, transition: 'margin-left 0.25s cubic-bezier(0.22,1,0.36,1)' }}
      >
        {/* Page Header */}
        <Header onMenuToggle={() => setMobileMenuOpen(o => !o)} />

        {/* Content row */}
        <div className="flex flex-1 overflow-hidden">

          {/* Main page */}
          <main className="flex-1 overflow-y-auto scrollbar-thin">
            <div className="px-6 py-5 min-h-full" style={{ paddingBottom: nowPlaying ? '100px' : undefined }}>
              <Outlet />
            </div>
          </main>

          {/* Activity Panel — xl+ */}
          <AnimatePresence>
            {showActivity && (
              <motion.aside
                initial={{ width: 0, opacity: 0 }}
                animate={{ width: ACTIVITY_WIDTH, opacity: 1 }}
                exit={{ width: 0, opacity: 0 }}
                transition={springGentle}
                className="flex-shrink-0 flex flex-col overflow-hidden"
                style={{
                  borderLeft: '1px solid var(--border-subtle)',
                  background: 'var(--void)',
                  width: ACTIVITY_WIDTH,
                }}
              >
                <ActivityFeed onClose={() => setActivityOpen(false)} />
              </motion.aside>
            )}
          </AnimatePresence>
        </div>

        {/* Footer Dock — appears during active downloads */}
        <FooterDock />
      </div>

      {/* Persistent music player */}
      <PlayerBar />

      {/* Command Palette */}
      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} />
    </div>
  );
}
