/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // One dark palette, defined once, so a view can never invent a shade that does not
        // exist. `surface` steps get darker as they go back, which is the direction a panel
        // recedes; `edge` is the only border colour.
        surface: {
          base: '#0b1020',
          raised: '#121a30',
          overlay: '#18213c',
          sunken: '#080c18',
        },
        edge: {
          DEFAULT: '#243050',
          strong: '#33436b',
        },
        // Severity colours are named by meaning, not by hue, so a diagnostic list reads
        // correctly even for someone who cannot distinguish the red from the amber.
        severity: {
          error: '#f87171',
          warning: '#fbbf24',
          info: '#60a5fa',
          hint: '#94a3b8',
          success: '#4ade80',
        },
        // Instruction and block accents, used by the TAC and Blocks views.
        stage: {
          token: '#38bdf8',
          ast: '#a78bfa',
          tac: '#fb923c',
          block: '#34d399',
          diagnostic: '#f472b6',
          optimizer: '#facc15',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Menlo', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
    },
  },
  plugins: [],
}
