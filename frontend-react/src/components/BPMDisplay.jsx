import { getCamelotColor } from '@/lib/tokens';
import { cn } from '@/lib/utils';

// Shows BPM + musical key + Camelot notation as a compact mono strip
export default function BPMDisplay({ bpm, musicalKey, camelot, className }) {
  if (!bpm && !musicalKey && !camelot) return null;

  const camelotColor = getCamelotColor(camelot);

  return (
    <div className={cn('flex items-center gap-1', className)}>
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
        <span
          className="font-mono text-11 font-medium px-1.5 py-0.5 rounded"
          style={{
            background: `${camelotColor}20`,
            color: camelotColor,
            border: `1px solid ${camelotColor}30`,
          }}
          title="Camelot"
        >
          {camelot}
        </span>
      )}
    </div>
  );
}
