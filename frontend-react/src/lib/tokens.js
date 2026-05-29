// JS access to design tokens — use for Recharts themes and inline styles
// Matches CSS custom properties in index.css

export const colors = {
  void:      '#07070C',
  base:      '#0C0C14',
  surface0:  '#111119',
  surface1:  '#17171F',
  surface2:  '#1E1E28',
  surface3:  '#252530',

  violet:      '#8B5CF6',
  violetDim:   'rgba(139,92,246,0.14)',
  violetGlow:  'rgba(139,92,246,0.25)',

  cyan:        '#06B6D4',
  cyanDim:     'rgba(6,182,212,0.14)',
  cyanGlow:    'rgba(6,182,212,0.20)',

  emerald:     '#10B981',
  emeraldDim:  'rgba(16,185,129,0.12)',

  amber:       '#F59E0B',
  amberDim:    'rgba(245,158,11,0.12)',

  rose:        '#F43F5E',
  roseDim:     'rgba(244,63,94,0.12)',

  slate:       '#64748B',
  slateDim:    'rgba(100,116,139,0.14)',

  textPrimary:   'rgba(255,255,255,0.92)',
  textSecondary: 'rgba(255,255,255,0.55)',
  textTertiary:  'rgba(255,255,255,0.32)',
  textMuted:     'rgba(255,255,255,0.18)',
  textAccent:    '#A78BFA',
  textCode:      '#67E8F9',

  borderSubtle:  'rgba(255,255,255,0.04)',
  borderDefault: 'rgba(255,255,255,0.08)',
  borderStrong:  'rgba(255,255,255,0.14)',
}

// Recharts-specific theme — pass to CartesianGrid, Tooltip, Axis, etc.
export const chartTheme = {
  background:   colors.surface0,
  text:         colors.textSecondary,
  grid:         colors.borderSubtle,
  tooltip: {
    background: colors.surface2,
    border:     colors.borderDefault,
    text:       colors.textPrimary,
  },
  // Palette for multi-series charts
  palette: [
    colors.violet,
    colors.cyan,
    colors.emerald,
    colors.amber,
    colors.rose,
    colors.slate,
  ],
}

// Camelot wheel colors — 12 keys, inner (minor) + outer (major)
export const CAMELOT_COLORS = {
  '1A': '#3B6EB0', '1B': '#5B9BD5',
  '2A': '#2E8B7A', '2B': '#4DB6A3',
  '3A': '#27A84B', '3B': '#4DCA6E',
  '4A': '#6FA829', '4B': '#94D44D',
  '5A': '#A08A12', '5B': '#C9AE2B',
  '6A': '#A0521A', '6B': '#C97033',
  '7A': '#9B2A2A', '7B': '#C94545',
  '8A': '#8B2C6E', '8B': '#B04D94',
  '9A': '#6B2E9E', '9B': '#8F52C7',
  '10A':'#4A3BB0', '10B':'#6B60D4',
  '11A':'#2B4FAA', '11B':'#4A72CC',
  '12A':'#1E6BA8', '12B':'#3A8FCA',
}

export const CAMELOT_COLOR_DEFAULT = colors.amber

export function getCamelotColor(camelot) {
  if (!camelot) return CAMELOT_COLOR_DEFAULT
  const key = camelot.toString().toUpperCase().trim()
  return CAMELOT_COLORS[key] ?? CAMELOT_COLOR_DEFAULT
}

// Genre family → color mapping (matches genre_router config)
export const GENRE_COLORS = {
  'Techno':        colors.violet,
  'House':         colors.cyan,
  'Trance':        '#A855F7',
  'Drum and Bass': '#F97316',
  'Jungle':        '#84CC16',
  'Ambient':       '#60A5FA',
  'Electronica':   '#34D399',
  'Hip-Hop':       '#FBBF24',
  'Pop':           '#F472B6',
  'Rock':          '#FB7185',
  'Classical':     '#94A3B8',
  'Jazz':          '#FCD34D',
  'Electronic':    colors.slate,
  'NeedsReview':   colors.amber,
  'Unknown':       colors.slate,
}

export function getGenreColor(genre) {
  if (!genre) return colors.slate
  for (const [key, color] of Object.entries(GENRE_COLORS)) {
    if (genre.toLowerCase().includes(key.toLowerCase())) return color
  }
  return colors.slate
}

// Status → semantic color mapping
export const STATUS_COLORS = {
  downloading: colors.cyan,
  completed:   colors.emerald,
  skipped:     colors.slate,
  failed:      colors.rose,
  pending:     colors.amber,
  running:     colors.violet,
  idle:        colors.slate,
}

export function getStatusColor(status) {
  return STATUS_COLORS[status?.toLowerCase()] ?? colors.slate
}
