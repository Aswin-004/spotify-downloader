/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Base layers
        void:   'var(--void)',
        base:   'var(--base)',
        s0:     'var(--surface-0)',
        s1:     'var(--surface-1)',
        s2:     'var(--surface-2)',
        s3:     'var(--surface-3)',

        // Glass surfaces
        'glass-subtle':  'var(--glass-subtle)',
        'glass-default': 'var(--glass-default)',
        'glass-strong':  'var(--glass-strong)',

        // Borders
        'border-subtle':  'var(--border-subtle)',
        'border-default': 'var(--border-default)',
        'border-strong':  'var(--border-strong)',
        'border-focus':   'var(--border-focus)',

        // Accent — violet (AI, interactive primary)
        violet:         'var(--accent-violet)',
        'violet-dim':   'var(--accent-violet-dim)',
        'violet-glow':  'var(--accent-violet-glow)',

        // Accent — cyan (audio, active downloads)
        cyan:           'var(--accent-cyan)',
        'cyan-dim':     'var(--accent-cyan-dim)',
        'cyan-glow':    'var(--accent-cyan-glow)',

        // Accent — emerald (success, completed)
        emerald:        'var(--accent-emerald)',
        'emerald-dim':  'var(--accent-emerald-dim)',

        // Accent — amber (BPM, camelot, warnings)
        amber:          'var(--accent-amber)',
        'amber-dim':    'var(--accent-amber-dim)',

        // Accent — rose (errors, destructive)
        rose:           'var(--accent-rose)',
        'rose-dim':     'var(--accent-rose-dim)',

        // Accent — slate (skipped, neutral)
        slate:          'var(--accent-slate)',
        'slate-dim':    'var(--accent-slate-dim)',

        // Text hierarchy
        t1: 'var(--text-primary)',
        t2: 'var(--text-secondary)',
        t3: 'var(--text-tertiary)',
        tm: 'var(--text-muted)',
        'text-accent': 'var(--text-accent)',
        'text-code':   'var(--text-code)',
      },
      fontFamily: {
        display: ['Geist', 'Inter', 'system-ui', 'sans-serif'],
        sans:    ['Geist', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono:    ['JetBrains Mono', 'SF Mono', 'Cascadia Code', 'Roboto Mono', 'Consolas', 'monospace'],
      },
      fontSize: {
        '10': ['10px', { lineHeight: '1.4' }],
        '11': ['11px', { lineHeight: '1.4' }],
        '12': ['12px', { lineHeight: '1.5' }],
        '13': ['13px', { lineHeight: '1.5' }],
        '14': ['14px', { lineHeight: '1.5' }],
        '16': ['16px', { lineHeight: '1.5' }],
        '18': ['18px', { lineHeight: '1.35' }],
        '22': ['22px', { lineHeight: '1.2' }],
        '28': ['28px', { lineHeight: '1.2' }],
        '36': ['36px', { lineHeight: '1.1' }],
      },
      spacing: {
        '0.5': '2px',
        '1':   '4px',
        '1.5': '6px',
        '2':   '8px',
        '3':   '12px',
        '4':   '16px',
        '5':   '20px',
        '6':   '24px',
        '8':   '32px',
        '10':  '40px',
        '12':  '48px',
        '16':  '64px',
      },
      borderRadius: {
        sm:  '4px',
        md:  '6px',
        lg:  '8px',
        xl:  '12px',
        '2xl': '16px',
        '3xl': '20px',
      },
      animation: {
        'pulse-slow':    'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'shimmer':       'shimmer 2s linear infinite',
        'glow-pulse':    'glowPulse 2s ease-in-out infinite',
        'slide-up':      'slideUp 0.2s cubic-bezier(0.22, 1, 0.36, 1)',
        'fade-in':       'fadeIn 0.2s ease-out',
        'ai-pulse':      'aiPulse 2s ease-in-out infinite',
      },
      keyframes: {
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        glowPulse: {
          '0%, 100%': { opacity: '0.6' },
          '50%':      { opacity: '1' },
        },
        slideUp: {
          '0%':   { transform: 'translateY(8px)', opacity: '0' },
          '100%': { transform: 'translateY(0)',   opacity: '1' },
        },
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        aiPulse: {
          '0%, 100%': { boxShadow: '0 0 0 0 var(--accent-violet-glow)' },
          '50%':      { boxShadow: '0 0 12px 3px var(--accent-violet-glow)' },
        },
      },
      boxShadow: {
        'glow-violet': '0 0 20px var(--accent-violet-glow)',
        'glow-cyan':   '0 0 20px var(--accent-cyan-glow)',
        'glow-sm':     '0 2px 8px rgba(0,0,0,0.4)',
        'panel':       '0 8px 32px rgba(0,0,0,0.5)',
        'modal':       '0 24px 64px rgba(0,0,0,0.7)',
      },
      backdropBlur: {
        xs:  '4px',
        sm:  '8px',
        md:  '12px',
        lg:  '16px',
      },
      transitionTimingFunction: {
        'spring': 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
    },
  },
  plugins: [
    // Scrollbar utility
    function({ addUtilities }) {
      addUtilities({
        '.scrollbar-thin': {
          'scrollbar-width': 'thin',
          'scrollbar-color': 'var(--surface-2) transparent',
          '&::-webkit-scrollbar': { width: '4px', height: '4px' },
          '&::-webkit-scrollbar-track': { background: 'transparent' },
          '&::-webkit-scrollbar-thumb': {
            background: 'var(--surface-2)',
            borderRadius: '2px',
          },
          '&::-webkit-scrollbar-thumb:hover': {
            background: 'var(--surface-3)',
          },
        },
        '.scrollbar-none': {
          'scrollbar-width': 'none',
          '&::-webkit-scrollbar': { display: 'none' },
        },
        // Glass panel utility
        '.glass': {
          background: 'var(--glass-default)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          border: '1px solid var(--glass-border)',
        },
        '.glass-subtle': {
          background: 'var(--glass-subtle)',
          backdropFilter: 'blur(8px)',
          WebkitBackdropFilter: 'blur(8px)',
          border: '1px solid var(--border-subtle)',
        },
        // Focus ring utility
        '.focus-ring': {
          '&:focus-visible': {
            outline: 'none',
            boxShadow: '0 0 0 2px var(--border-focus)',
          },
        },
        // Glow utilities
        '.glow-violet': { boxShadow: '0 0 20px var(--accent-violet-glow)' },
        '.glow-cyan':   { boxShadow: '0 0 20px var(--accent-cyan-glow)' },
      });
    },
  ],
};
