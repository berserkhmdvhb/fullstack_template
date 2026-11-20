import type { Config } from 'tailwindcss';

export default {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './features/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Text colors — resolve from CSS variables (auto light/dark via html.dark)
        textPrimary: 'var(--color-text-primary)',
        textSecondary: 'var(--color-text-secondary)',
        textTertiary: 'var(--color-text-tertiary)',
        textMuted: 'var(--color-text-muted)',

        // Surface colors — resolve from CSS variables
        surface: 'var(--color-surface)',
        surfaceAlt: 'var(--color-surface-alt)',
        surfaceWeak: 'var(--color-surface-weak)',
        surfaceStrong: 'var(--color-surface-strong)',

        // Border colors — resolve from CSS variables
        borderSubtle: 'var(--color-border-subtle)',
        borderWeak: 'var(--color-border-weak)',
        borderStrong: 'var(--color-border-strong)',

        // Accent colors — resolve from CSS variables
        accentBlue: 'var(--color-accent-blue)',
        accentBlueHover: 'var(--color-accent-blue-hover)',
        accentPurple: 'var(--color-accent-purple)',
        accentGreen: 'var(--color-accent-green)',
        accentOrange: 'var(--color-accent-orange)',
        accentPink: 'var(--color-accent-pink)',

        // Status colors — fixed (no theme variation needed)
        statusQueued: '#eab308',
        statusRunning: '#22c55e',
        statusSucceeded: '#4ade80',
        statusFailed: '#ef4444',
        statusUnknown: '#6b7280',
      },
      backgroundImage: {
        'bg-card': 'linear-gradient(to bottom right, #1e293b, #334155)',
        'grid-pattern':
          'linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px)',
      },
      backgroundSize: {
        'grid-size': '32px 32px',
      },
      boxShadow: {
        purpleOrb: '0 0 200px 70px rgba(168, 85, 247, 0.12)',
        blueOrb: '0 0 200px 70px rgba(59, 130, 246, 0.10)',
        pinkOrb: '0 0 200px 70px rgba(236, 72, 153, 0.08)',
      },
    },
  },
  plugins: [],
} satisfies Config;
