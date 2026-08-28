import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { SkipBack, SkipForward, Play, Pause, Volume2, Volume1, VolumeX, Disc3 } from 'lucide-react';
import { useHotkeys } from 'react-hotkeys-hook';
import { usePlayer } from '@/context/PlayerContext';
import { api } from '@/services/api';
import { getCamelotColor } from '@/lib/tokens';

function fmt(sec) {
  const s = Math.floor(sec || 0);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export default function PlayerBar() {
  const player = usePlayer();
  if (!player) return null;

  const {
    nowPlaying, playing, currentTime, duration, volume,
    queue, queueIndex,
    toggle, playNext, playPrev, seek, changeVolume,
  } = player;

  const [seeking,  setSeeking]  = useState(false);
  const [seekVal,  setSeekVal]  = useState(0);
  const [showVol,  setShowVol]  = useState(false);
  const [artFailed, setArtFailed] = useState(false);

  useEffect(() => { setArtFailed(false); }, [nowPlaying?.path]);

  const hasPrev = queueIndex > 0;
  const hasNext = queueIndex < (queue?.length ?? 0) - 1;
  const pct     = duration > 0 ? ((seeking ? seekVal : currentTime) / duration) * 100 : 0;

  useHotkeys('space', e => { e.preventDefault(); toggle?.(); },
    { enabled: !!nowPlaying, enableOnFormTags: false });
  useHotkeys('left',  () => seek?.(Math.max(0, (currentTime || 0) - 5)),
    { enabled: !!nowPlaying, enableOnFormTags: false });
  useHotkeys('right', () => seek?.(Math.min(duration || 0, (currentTime || 0) + 5)),
    { enabled: !!nowPlaying, enableOnFormTags: false });

  return (
    <AnimatePresence>
      {nowPlaying && (
        <motion.div
          initial={{ y: 100, opacity: 0 }}
          animate={{ y: 0,   opacity: 1 }}
          exit={{    y: 100, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 380, damping: 38 }}
          style={{
            /* ── Floating pill — sits just inside the glass container edges ── */
            position: 'fixed',
            bottom: '28px',
            left:   '28px',
            right:  '28px',
            zIndex: 45,
            /* Glass */
            background: 'rgba(10,10,18,0.88)',
            backdropFilter: 'blur(24px) saturate(1.3)',
            WebkitBackdropFilter: 'blur(24px) saturate(1.3)',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: '16px',
            boxShadow: '0 12px 48px rgba(0,0,0,0.5), 0 0 0 0.5px rgba(255,255,255,0.06) inset',
          }}
        >
          <div style={{ padding: '10px 18px', display: 'flex', flexDirection: 'column', gap: 8 }}>

            {/* ── Row 1: track info · controls · vol ─────────────── */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>

              {/* Album art */}
              {nowPlaying.path && !artFailed ? (
                <div className="tbl-thumb" style={{ width: 36, height: 36, flexShrink: 0, overflow: 'hidden' }}>
                  <img
                    src={api.artworkUrl(nowPlaying.path)}
                    alt=""
                    className="w-full h-full object-cover"
                    onError={() => setArtFailed(true)}
                  />
                </div>
              ) : (
                <div className="tbl-thumb" style={{
                  width: 36, height: 36, flexShrink: 0,
                  background: `linear-gradient(135deg, ${getCamelotColor(nowPlaying.camelot)}55, ${getCamelotColor(nowPlaying.camelot)}15)`,
                }}>
                  <motion.div
                    animate={playing ? { rotate: 360 } : { rotate: 0 }}
                    transition={playing ? { duration: 6, ease: 'linear', repeat: Infinity } : { duration: 0.3 }}
                  >
                    <Disc3 style={{ width: 16, height: 16, color: getCamelotColor(nowPlaying.camelot) }} />
                  </motion.div>
                </div>
              )}

              {/* LEFT — title + badges + artist */}
              <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
                  {/* Waveform bars — only when playing */}
                  <AnimatePresence>
                    {playing && (
                      <motion.div
                        initial={{ opacity: 0, width: 0 }}
                        animate={{ opacity: 1, width: 'auto' }}
                        exit={{ opacity: 0, width: 0 }}
                        transition={{ duration: 0.2 }}
                        style={{
                          display: 'flex', alignItems: 'flex-end', gap: 1.5,
                          flexShrink: 0, height: 12, overflow: 'hidden',
                        }}
                      >
                        {[8, 12, 6, 10, 7].map((h, i) => (
                          <div
                            key={i}
                            className="waveform-bar"
                            style={{ width: 2, height: h, animationDelay: `${i * 0.12}s` }}
                          />
                        ))}
                      </motion.div>
                    )}
                  </AnimatePresence>

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
                      padding: '1px 5px', borderRadius: 3, flexShrink: 0, lineHeight: 1.6,
                    }}>
                      {nowPlaying.bpm}
                    </span>
                  )}
                  {nowPlaying.camelot && (
                    <span style={{
                      fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
                      color: getCamelotColor(nowPlaying.camelot),
                      background: `${getCamelotColor(nowPlaying.camelot)}22`,
                      padding: '1px 5px', borderRadius: 3, flexShrink: 0, lineHeight: 1.6,
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

              {/* CENTER — playback controls */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                <button
                  onClick={playPrev} disabled={!hasPrev}
                  style={{
                    width: 30, height: 30, border: 'none', background: 'transparent',
                    color: hasPrev ? 'var(--text-secondary)' : 'var(--text-muted)',
                    cursor: hasPrev ? 'pointer' : 'default',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                  }}
                  title="Previous"
                >
                  <SkipBack style={{ width: 15, height: 15 }} />
                </button>

                {/* Play/Pause with ring pulse */}
                <div style={{ position: 'relative', flexShrink: 0 }}>
                  {playing && (
                    <motion.div
                      style={{
                        position: 'absolute', inset: -6, borderRadius: '50%',
                        border: '1.5px solid rgba(139,92,246,0.4)',
                        pointerEvents: 'none',
                      }}
                      animate={{ scale: [1, 1.5], opacity: [0.55, 0] }}
                      transition={{ duration: 1.8, repeat: Infinity, ease: 'easeOut' }}
                    />
                  )}
                  <button
                    onClick={toggle}
                    title={playing ? 'Pause' : 'Play'}
                    style={{
                      width: 38, height: 38, borderRadius: '50%', border: 'none',
                      background: 'var(--accent-violet)', color: '#fff',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'pointer', flexShrink: 0,
                      boxShadow: playing ? '0 0 20px rgba(139,92,246,0.6)' : 'none',
                      transition: 'box-shadow 0.25s',
                    }}
                  >
                    {playing
                      ? <Pause style={{ width: 15, height: 15 }} />
                      : <Play  style={{ width: 15, height: 15, transform: 'translateX(1px)' }} />}
                  </button>
                </div>

                <button
                  onClick={playNext} disabled={!hasNext}
                  style={{
                    width: 30, height: 30, border: 'none', background: 'transparent',
                    color: hasNext ? 'var(--text-secondary)' : 'var(--text-muted)',
                    cursor: hasNext ? 'pointer' : 'default',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                  }}
                  title="Next"
                >
                  <SkipForward style={{ width: 15, height: 15 }} />
                </button>
              </div>

              {/* RIGHT — queue position + volume */}
              <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 10 }}>
                {(queue?.length ?? 0) > 1 && (
                  <span style={{
                    fontSize: 10, color: 'var(--text-muted)',
                    fontVariantNumeric: 'tabular-nums', fontFamily: 'monospace',
                    letterSpacing: '0.03em', flexShrink: 0,
                  }}>
                    {(queueIndex ?? 0) + 1} / {queue.length}
                  </span>
                )}

                <div
                  style={{ display: 'flex', alignItems: 'center', gap: 5 }}
                  onMouseEnter={() => setShowVol(true)}
                  onMouseLeave={() => setShowVol(false)}
                >
                  <button
                    onClick={() => changeVolume?.(volume > 0 ? 0 : 0.7)}
                    style={{
                      width: 28, height: 28, border: 'none', background: 'transparent',
                      color: 'var(--text-muted)', cursor: 'pointer',
                      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                    }}
                    title={volume === 0 ? 'Unmute' : 'Mute'}
                  >
                    {volume === 0
                      ? <VolumeX style={{ width: 14, height: 14 }} />
                      : volume < 0.4
                        ? <Volume1 style={{ width: 14, height: 14 }} />
                        : <Volume2 style={{ width: 14, height: 14 }} />}
                  </button>
                  <AnimatePresence>
                    {showVol && (
                      <motion.div
                        initial={{ width: 0, opacity: 0 }}
                        animate={{ width: 56, opacity: 1 }}
                        exit={{ width: 0, opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        style={{ overflow: 'hidden', flexShrink: 0 }}
                      >
                        <input
                          type="range" min={0} max={1} step={0.01}
                          value={volume}
                          onChange={e => changeVolume?.(Number(e.target.value))}
                          style={{ width: 56, accentColor: 'var(--accent-violet)', display: 'block' }}
                        />
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </div>
            </div>

            {/* ── Row 2: seek bar with diamond marker ──────────── */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <span style={{
                fontSize: 9, fontFamily: 'monospace', letterSpacing: '0.04em',
                color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums',
                flexShrink: 0, minWidth: 26, textAlign: 'right',
              }}>
                {fmt(currentTime)}
              </span>

              <div
                style={{
                  flex: 1, position: 'relative', height: 16,
                  display: 'flex', alignItems: 'center',
                  cursor: 'pointer', overflow: 'visible',
                }}
              >
                {/* Track */}
                <div style={{ position: 'absolute', left: 0, right: 0, height: 1, background: 'rgba(255,255,255,0.08)' }} />
                {/* Progress fill */}
                <div style={{
                  position: 'absolute', left: 0,
                  width: `${pct}%`, height: 2,
                  background: 'var(--accent-violet)',
                  transition: seeking ? 'none' : 'width 0.1s linear',
                  borderRadius: '0 1px 1px 0',
                }} />
                {/* Diamond playhead */}
                <div style={{
                  position: 'absolute',
                  left: `calc(${pct}% - 4px)`,
                  width: 7, height: 7,
                  background: 'var(--accent-violet)',
                  transform: 'rotate(45deg)',
                  boxShadow: playing
                    ? '0 0 10px rgba(139,92,246,0.95), 0 0 20px rgba(139,92,246,0.4)'
                    : '0 0 5px rgba(139,92,246,0.5)',
                  transition: seeking ? 'none' : 'left 0.1s linear, box-shadow 0.3s',
                  pointerEvents: 'none',
                  zIndex: 1,
                }} />
                {/* Invisible range input */}
                <input
                  type="range"
                  min={0} max={duration || 100} step={0.1}
                  value={seeking ? seekVal : (currentTime || 0)}
                  onPointerDown={() => { setSeeking(true); setSeekVal(currentTime || 0); }}
                  onChange={e => setSeekVal(Number(e.target.value))}
                  onPointerUp={e => { seek?.(Number(e.target.value)); setSeeking(false); }}
                  style={{
                    position: 'absolute', inset: 0, opacity: 0,
                    cursor: 'pointer', width: '100%', height: '100%',
                    margin: 0, padding: 0,
                  }}
                />
              </div>

              <span style={{
                fontSize: 9, fontFamily: 'monospace', letterSpacing: '0.04em',
                color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums',
                flexShrink: 0, minWidth: 26,
              }}>
                {fmt(duration)}
              </span>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
