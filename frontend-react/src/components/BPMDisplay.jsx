import { useState, useEffect, useRef } from 'react';
import { getCamelotColor } from '@/lib/tokens';
import { cn } from '@/lib/utils';
import CamelotWheel from '@/components/CamelotWheel';

// Shows BPM + musical key + Camelot notation as a compact mono strip.
// Clicking the Camelot badge opens an inline wheel popover.
export default function BPMDisplay({ bpm, musicalKey, camelot, className }) {
  const [showWheel, setShowWheel] = useState(false);
  const ref = useRef(null);
  const camelotColor = getCamelotColor(camelot);

  useEffect(() => {
    if (!showWheel) return;
    function onOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setShowWheel(false);
    }
    document.addEventListener('mousedown', onOutside);
    return () => document.removeEventListener('mousedown', onOutside);
  }, [showWheel]);

  if (!bpm && !musicalKey && !camelot) return null;

  return (
    <div className={cn('flex items-center gap-1', className)} ref={ref}>
      {bpm && (
        <span
          className="font-mono text-11 font-bold px-1.5 py-0.5 rounded"
          style={{ background: 'var(--accent-cyan-dim)', color: 'var(--text-code)' }}
          title="BPM"
        >
          {Math.round(bpm)}
        </span>
      )}
      {musicalKey && (
        <span
          className="font-mono text-11 font-medium px-1.5 py-0.5 rounded"
          style={{ background: 'var(--accent-amber-dim)', color: 'var(--accent-amber)' }}
          title="Musical Key"
        >
          {musicalKey}
        </span>
      )}
      {camelot && (
        <div className="relative">
          <button
            onClick={() => setShowWheel(v => !v)}
            className="font-mono text-11 font-medium px-1.5 py-0.5 rounded cursor-pointer transition-opacity duration-100 hover:opacity-80 focus-ring"
            style={{
              background: `${camelotColor}20`,
              color: camelotColor,
              border: `1px solid ${camelotColor}30`,
            }}
            title="Click to show Camelot wheel"
          >
            {camelot}
          </button>

          {showWheel && (
            <div
              className="absolute z-50 rounded-xl p-3 shadow-2xl"
              style={{
                background: 'var(--surface-1, #17171F)',
                border: '1px solid rgba(255,255,255,0.1)',
                bottom: 'calc(100% + 8px)',
                left: '50%',
                transform: 'translateX(-50%)',
                minWidth: '216px',
              }}
            >
              {/* Arrow */}
              <div
                style={{
                  position: 'absolute',
                  bottom: -6,
                  left: '50%',
                  transform: 'translateX(-50%)',
                  width: 12,
                  height: 6,
                  overflow: 'hidden',
                }}
              >
                <div style={{
                  width: 10,
                  height: 10,
                  background: 'var(--surface-1, #17171F)',
                  border: '1px solid rgba(255,255,255,0.1)',
                  transform: 'rotate(45deg)',
                  transformOrigin: 'center',
                  marginTop: -5,
                  marginLeft: 1,
                }} />
              </div>

              <p className="text-10 font-mono text-center mb-2" style={{ color: 'rgba(255,255,255,0.35)' }}>
                Camelot {camelot} · compatible keys highlighted
              </p>
              <CamelotWheel camelot={camelot} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
