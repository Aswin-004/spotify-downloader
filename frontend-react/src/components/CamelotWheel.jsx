import { CAMELOT_COLORS } from '@/lib/tokens';

const CX = 120, CY = 120;
const RI_A = 40, RO_A = 72;   // inner ring (minor / A)
const RI_B = 78, RO_B = 112;  // outer ring (major / B)

function toRad(deg) { return (deg * Math.PI) / 180; }

function donutPath(cx, cy, r1, r2, startDeg, endDeg) {
  const s = toRad(startDeg);
  const e = toRad(endDeg);
  const x1 = cx + r1 * Math.cos(s), y1 = cy + r1 * Math.sin(s);
  const x2 = cx + r2 * Math.cos(s), y2 = cy + r2 * Math.sin(s);
  const x3 = cx + r2 * Math.cos(e), y3 = cy + r2 * Math.sin(e);
  const x4 = cx + r1 * Math.cos(e), y4 = cy + r1 * Math.sin(e);
  return `M${x1.toFixed(2)},${y1.toFixed(2)} L${x2.toFixed(2)},${y2.toFixed(2)} A${r2},${r2} 0 0,1 ${x3.toFixed(2)},${y3.toFixed(2)} L${x4.toFixed(2)},${y4.toFixed(2)} A${r1},${r1} 0 0,0 ${x1.toFixed(2)},${y1.toFixed(2)}Z`;
}

// Position 8 sits at 12 o'clock (-90° SVG = 270°). Others clockwise in 30° steps.
function centerDeg(pos) {
  return ((pos - 8 + 12) % 12) * 30 - 90;
}

function getCompatible(camelot) {
  if (!camelot) return new Set();
  const m = camelot.toUpperCase().match(/^(\d+)([AB])$/);
  if (!m) return new Set();
  const n = parseInt(m[1]);
  const prev = n === 1 ? 12 : n - 1;
  const next = n === 12 ? 1 : n + 1;
  return new Set([`${n}A`, `${n}B`, `${prev}A`, `${prev}B`, `${next}A`, `${next}B`]);
}

export default function CamelotWheel({ camelot }) {
  const active = camelot ? camelot.toUpperCase() : null;
  const compatible = getCompatible(active);

  return (
    <svg
      viewBox="0 0 240 240"
      width="200"
      height="200"
      style={{ display: 'block', overflow: 'visible' }}
      aria-label={`Camelot wheel — ${active ?? 'no key'}`}
    >
      {/* Ring labels */}
      <text x={CX} y={CY - RO_B - 8} textAnchor="middle" fontSize="8" fill="rgba(255,255,255,0.3)" fontFamily="monospace">major (B)</text>
      <text x={CX} y={CY + 7} textAnchor="middle" fontSize="8" fill="rgba(255,255,255,0.3)" fontFamily="monospace">minor (A)</text>

      {/* Center circle */}
      <circle cx={CX} cy={CY} r={36} fill="rgba(255,255,255,0.04)" />

      {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].flatMap(pos =>
        ['A', 'B'].map(ring => {
          const key = `${pos}${ring}`;
          const isActive = active === key;
          const isCompat = compatible.has(key);
          const color = CAMELOT_COLORS[key] ?? '#64748B';
          const cd = centerDeg(pos);
          const r1 = ring === 'A' ? RI_A : RI_B;
          const r2 = ring === 'A' ? RO_A : RO_B;
          const rMid = (r1 + r2) / 2;
          const lx = +(CX + rMid * Math.cos(toRad(cd))).toFixed(2);
          const ly = +(CY + rMid * Math.sin(toRad(cd))).toFixed(2);
          const opacity = active ? (isActive ? 1 : isCompat ? 0.72 : 0.14) : 0.55;
          const strokeW = isActive ? 2.5 : 1;

          return (
            <g key={key}>
              <path
                d={donutPath(CX, CY, r1, r2, cd - 15, cd + 15)}
                fill={color}
                fillOpacity={opacity}
                stroke="var(--surface-0, #0C0C14)"
                strokeWidth={strokeW}
              />
              <text
                x={lx}
                y={ly}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={isActive ? 9.5 : 8.5}
                fontWeight={isActive ? '700' : '500'}
                fontFamily="monospace"
                fill={isActive ? '#fff' : `rgba(255,255,255,${isCompat ? 0.9 : 0.4})`}
                style={{ pointerEvents: 'none', userSelect: 'none' }}
              >
                {key}
              </text>
            </g>
          );
        })
      )}

      {/* Active glow ring */}
      {active && (() => {
        const m = active.match(/^(\d+)([AB])$/);
        if (!m) return null;
        const pos = parseInt(m[1]);
        const ring = m[2];
        const cd = centerDeg(pos);
        const r1 = ring === 'A' ? RI_A : RI_B;
        const r2 = ring === 'A' ? RO_A : RO_B;
        const color = CAMELOT_COLORS[active] ?? '#F59E0B';
        return (
          <path
            d={donutPath(CX, CY, r1, r2, cd - 15, cd + 15)}
            fill="none"
            stroke={color}
            strokeWidth="2.5"
            opacity="0.9"
          />
        );
      })()}
    </svg>
  );
}
