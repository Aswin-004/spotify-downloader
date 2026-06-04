import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { SkipBack, SkipForward, Play, Pause, Volume2, Volume1, VolumeX } from 'lucide-react';
import { useHotkeys } from 'react-hotkeys-hook';
import { usePlayer } from '@/context/PlayerContext';
import { getCamelotColor } from '@/lib/tokens';

function fmt(sec) {
  const s = Math.floor(sec || 0);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const iconBtn = (enabled = true) => ({
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  width: 28, height: 28, borderRadius: 6, border: 'none',
  background: 'transparent', cursor: enabled ? 'pointer' : 'default',
  color: enabled ? 'var(--text-secondary)' : 'var(--text-muted)',
  transition: 'color 0.1s, background 0.1s',
  flexShrink: 0,
});

export default function PlayerBar() {
  const {
    nowPlaying, playing, currentTime, duration, volume,
    queue, queueIndex,
    toggle, playNext, playPrev, seek, changeVolume,
  } = usePlayer() || {};

  const [seeking,   setSeeking]   = useState(false);
  const [seekVal,   setSeekVal]   = useState(0);
  const [showVol,   setShowVol]   = useState(false);

  const hasPrev = queueIndex > 0;
  const hasNext = queueIndex < (queue?.length ?? 0) - 1;
  const pct     = duration > 0 ? ((seeking ? seekVal : currentTime) / duration) * 100 : 0;

  // Keyboard shortcuts — only when no text input has focus
  useHotkeys('space', (e) => { e.preventDefault(); toggle?.(); },
    { enabled: !!nowPlaying, enableOnFormTags: false });
  useHotkeys('left',  () => seek?.(Math.max(0, (currentTime || 0) - 5)),
    { enabled: !!nowPlaying, enableOnFormTags: false });
  useHotkeys('right', () => seek?.(Math.min(duration || 0, (currentTime || 0) + 5)),
    { enabled: !!nowPlaying, enableOnFormTags: false });

  return (
    <AnimatePresence>
      {nowPlaying && (
        <motion.div
          initial={{ y: 80, opacity: 0 }}
          animate={{ y: 0,  opacity: 1 }}
          exit={{    y: 80, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 420, damping: 42 }}
          style={{
            position: 'fixed', bottom: 0, left: 0, right: 0,
            zIndex: 45, height: 68,
            background: 'var(--void)',
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
          {/* ── Seek hairline ───────────────────────────────────────── */}
          <div
            style={{
              position: 'absolute', top: 0, left: 0, right: 0, height: 4,
              cursor: 'pointer', zIndex: 2,
            }}
          >
            {/* Track */}
            <div style={{ position: 'absolute', inset: 0, background: 'rgba(255,255,255,0.04)' }} />
            {/* Progress fill + glow */}
            <div style={{
              position: 'absolute', top: 0, left: 0, bottom: 0,
              width: `${pct}%`,
              background: 'var(--accent-violet)',
              boxShadow: '2px 0 10px rgba(139,92,246,0.6)',
              transition: seeking ? 'none' : 'width 0.1s linear',
            }} />
            {/* Invisible interactive range */}
            <input
              type="range"
              min={0}
              max={duration || 100}
              step={0.1}
              value={seeking ? seekVal : (currentTime || 0)}
              onPointerDown={() => { setSeeking(true); setSeekVal(currentTime || 0); }}
              onChange={e => setSeekVal(Number(e.target.value))}
              onPointerUp={e => { seek?.(Number(e.target.value)); setSeeking(false); }}
              style={{
                position: 'absolute', inset: 0,
                opacity: 0, cursor: 'pointer',
                width: '100%', height: '100%',
                margin: 0, padding: 0,
              }}
            />
          </div>

          {/* ── Content row ──────────────────────────────────────────── */}
          <div style={{
            display: 'flex', alignItems: 'center',
            height: '100%', padding: '4px 16px 0',
            gap: 12,
          }}>

            {/* Left — track info */}
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
                <span style={{
                  color: 'var(--text-primary)', fontSize: 13, fontWeight: 600,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {nowPlaying.title}
                </span>
                {nowPlaying.bpm && (
                  <span style={{
                    fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
                    color: 'var(--accent-violet)', background: 'var(--accent-violet-dim)',
                    padding: '1px 5px', borderRadius: 4, flexShrink: 0, lineHeight: 1.6,
                  }}>
                    {nowPlaying.bpm}
                  </span>
                )}
                {nowPlaying.camelot && (
                  <span style={{
                    fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
                    color: getCamelotColor(nowPlaying.camelot),
                    background: `${getCamelotColor(nowPlaying.camelot)}22`,
                    padding: '1px 5px', borderRadius: 4, flexShrink: 0, lineHeight: 1.6,
                  }}>
                    {nowPlaying.camelot}
                  </span>
                )}
              </div>
              <span style={{
                color: 'var(--text-tertiary)', fontSize: 11,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {nowPlaying.artist || '—'}
              </span>
            </div>

            {/* Center — controls + time */}
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              gap: 3, flexShrink: 0,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                {/* Prev */}
                <button
                  onClick={playPrev}
                  disabled={!hasPrev}
                  style={iconBtn(hasPrev)}
                  title="Previous"
                >
                  <SkipBack className="w-3.5 h-3.5" />
                </button>

                {/* Play / Pause — violet pill */}
                <button
                  onClick={toggle}
                  title={playing ? 'Pause' : 'Play'}
                  style={{
                    width: 34, height: 34, borderRadius: '50%', border: 'none',
                    background: 'var(--accent-violet)', color: '#fff',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    cursor: 'pointer', flexShrink: 0,
                    boxShadow: playing ? '0 0 14px rgba(139,92,246,0.45)' : 'none',
                    transition: 'box-shadow 0.2s',
                  }}
                >
                  {playing
                    ? <Pause className="w-3.5 h-3.5" />
                    : <Play  className="w-3.5 h-3.5" style={{ transform: 'translateX(1px)' }} />}
                </button>

                {/* Next */}
                <button
                  onClick={playNext}
                  disabled={!hasNext}
                  style={iconBtn(hasNext)}
                  title="Next"
                >
                  <SkipForward className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* Time */}
              <div style={{
                color: 'var(--text-muted)', fontSize: 10,
                fontFamily: 'monospace', letterSpacing: '0.04em',
                fontVariantNumeric: 'tabular-nums',
              }}>
                {fmt(currentTime)} / {fmt(duration)}
              </div>
            </div>

            {/* Right — volume + queue count */}
            <div style={{
              flex: 1, display: 'flex', justifyContent: 'flex-end',
              alignItems: 'center', gap: 8,
            }}>
              {/* Queue position */}
              {(queue?.length ?? 0) > 1 && (
                <span style={{
                  color: 'var(--text-muted)', fontSize: 10,
                  fontVariantNumeric: 'tabular-nums', letterSpacing: '0.02em',
                }}>
                  {(queueIndex ?? 0) + 1} / {queue.length}
                </span>
              )}

              {/* Volume */}
              <div
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                onMouseEnter={() => setShowVol(true)}
                onMouseLeave={() => setShowVol(false)}
              >
                <button
                  onClick={() => changeVolume?.(volume > 0 ? 0 : 0.7)}
                  style={{ ...iconBtn(true), color: 'var(--text-muted)' }}
                  title={volume === 0 ? 'Unmute' : 'Mute'}
                >
                  {volume === 0
                    ? <VolumeX className="w-3.5 h-3.5" />
                    : volume < 0.4
                      ? <Volume1 className="w-3.5 h-3.5" />
                      : <Volume2 className="w-3.5 h-3.5" />}
                </button>

                <AnimatePresence>
                  {showVol && (
                    <motion.div
                      initial={{ width: 0, opacity: 0 }}
                      animate={{ width: 60, opacity: 1 }}
                      exit={{   width: 0, opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      style={{ overflow: 'hidden', flexShrink: 0 }}
                    >
                      <input
                        type="range"
                        min={0} max={1} step={0.01}
                        value={volume}
                        onChange={e => changeVolume?.(Number(e.target.value))}
                        style={{ width: 60, accentColor: 'var(--accent-violet)', display: 'block' }}
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
