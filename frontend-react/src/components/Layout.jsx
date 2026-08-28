import { useState, useEffect, useRef } from 'react';
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

const NAV_WIDTH_EXPANDED = 220;
const ACTIVITY_WIDTH     = 280;

export default function Layout() {
  const { nowPlaying } = usePlayer() || {};
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activityOpen,     setActivityOpen]     = useState(true);
  const [mobileMenuOpen,   setMobileMenuOpen]   = useState(false);
  const [commandOpen,      setCommandOpen]      = useState(false);
  const [isXl,             setIsXl]             = useState(false);
  const [isLg,             setIsLg]             = useState(false);
  const bgRef = useRef(null);

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

  // Parallax background on mouse move
  useEffect(() => {
    function onMouseMove(e) {
      if (!bgRef.current) return;
      const x = (e.clientX / window.innerWidth  - 0.5) * 14;
      const y = (e.clientY / window.innerHeight - 0.5) *  8;
      bgRef.current.style.transform = `translate(${x}px, ${y}px) scale(1.03)`;
    }
    document.addEventListener('mousemove', onMouseMove);
    return () => document.removeEventListener('mousemove', onMouseMove);
  }, []);

  // Keyboard shortcuts
  useHotkeys('[', () => {
    if (isLg) setSidebarCollapsed(c => !c);
    else      setMobileMenuOpen(o => !o);
  }, { preventDefault: true });

  useHotkeys('\\', () => setActivityOpen(o => !o), { preventDefault: true });

  useHotkeys('meta+k, ctrl+k', () => setCommandOpen(true), { preventDefault: true });

  useHotkeys('escape', () => {
    setCommandOpen(false);
    setMobileMenuOpen(false);
  });

  useHotkeys('d', () => {
    const current = document.documentElement.getAttribute('data-density') || 'default';
    const next    = current === 'compact' ? 'default' : 'compact';
    document.documentElement.setAttribute('data-density', next);
    localStorage.setItem('ui-density', next);
  }, { preventDefault: true });

  const showActivity = isXl && activityOpen;

  return (
    <>
      {/* ── Cinematic parallax background ─────────────────────────── */}
      <div
        ref={bgRef}
        style={{
          position: 'fixed', inset: '-4%', zIndex: 0,
          background: `
            radial-gradient(ellipse 140% 80% at 72% 38%, rgba(120,155,190,0.45) 0%, transparent 48%),
            radial-gradient(ellipse 80%  60% at 18% 78%, rgba(90, 65, 38, 0.80) 0%, transparent 50%),
            radial-gradient(ellipse 60%  90% at 88% 82%, rgba(55, 75, 50, 0.55) 0%, transparent 55%),
            linear-gradient(158deg, #687d8e 0%, #526a5c 18%, #786043 42%, #5c4a35 68%, #2d3a2a 100%)
          `,
          transition: 'transform 0.12s ease-out',
          willChange: 'transform',
        }}
      />

      {/* ── Glass app container ───────────────────────────────────── */}
      <div
        style={{
          position: 'fixed', inset: '16px', zIndex: 1,
          display: 'flex', flexDirection: 'row',
          borderRadius: '20px',
          background: 'rgba(12,12,20,0.60)',
          backdropFilter: 'blur(28px) saturate(1.4)',
          WebkitBackdropFilter: 'blur(28px) saturate(1.4)',
          border: '1px solid rgba(255,255,255,0.14)',
          boxShadow: '0 32px 80px rgba(0,0,0,0.55), inset 0 0 0 0.5px rgba(255,255,255,0.06)',
          overflow: 'hidden',
        }}
      >
        {/* Desktop sidebar — in-flow inside glass container */}
        {isLg && (
          <Sidebar
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed(c => !c)}
            inFlow
          />
        )}

        {/* Main column: header + content + footer dock */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <Header onMenuToggle={() => setMobileMenuOpen(o => !o)} onOpenCommand={() => setCommandOpen(true)} />

          {/* Content row: main page + activity panel */}
          <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>

            <main className="flex-1 overflow-y-auto scrollbar-thin">
              <div
                className="px-6 py-5 min-h-full"
                style={{ paddingBottom: nowPlaying ? '110px' : undefined }}
              >
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
                    background: 'rgba(0,0,0,0.18)',
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
      </div>

      {/* ── Mobile sidebar overlay (slides over glass container) ──── */}
      <AnimatePresence>
        {mobileMenuOpen && (
          <>
            <motion.div
              {...fadeBackdrop}
              className="fixed inset-0 lg:hidden"
              style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)', zIndex: 30 }}
              onClick={() => setMobileMenuOpen(false)}
            />
            <motion.div
              initial={{ x: -NAV_WIDTH_EXPANDED }}
              animate={{ x: 0 }}
              exit={{ x: -NAV_WIDTH_EXPANDED }}
              transition={springGentle}
              className="fixed left-0 top-0 bottom-0 lg:hidden"
              style={{ zIndex: 50 }}
            >
              <Sidebar collapsed={false} onToggle={() => setMobileMenuOpen(false)} />
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* ── Persistent floating player ─────────────────────────────── */}
      <PlayerBar />

      {/* ── Command Palette ───────────────────────────────────────── */}
      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} />
    </>
  );
}
