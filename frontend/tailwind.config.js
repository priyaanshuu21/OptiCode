/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // One dark palette, defined once, so a view can never invent a shade that does not
        // exist. `surface` steps get darker as they go back, which is the direction a panel
        // recedes; `edge` is the only border colour. The page sits on `base`; a panel is
        // `raised`; anything that needs to sit *above* a panel uses `overlay`; the graph canvas
        // and other recessed areas use the darker `sunken`.
        surface: {
          base: '#0b1220',
          raised: '#111c2e',
          overlay: '#18263b',
          sunken: '#080c18',
        },
        edge: {
          DEFAULT: '#26334f',
          strong: '#3b4a6b',
        },
        // Severity colours are named by meaning, not by hue, so a diagnostic list reads
        // correctly even for someone who cannot distinguish the red from the amber.
        severity: {
          error: '#fb7185',
          warning: '#fbbf24',
          info: '#60a5fa',
          hint: '#94a3b8',
          success: '#34d399',
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
      // The whole scale is nudged up a step: the previous build ran explanatory text at 12px and
      // code at 11px, which is below what a projector or a tired reader can follow. Body copy is
      // now 15-16px and code/tables 13-14px, without a class change at every call site.
      fontSize: {
        '2xs': ['0.8125rem', { lineHeight: '1.15rem' }],
        xs: ['0.875rem', { lineHeight: '1.3rem' }],
        sm: ['0.9375rem', { lineHeight: '1.45rem' }],
        base: ['1rem', { lineHeight: '1.5rem' }],
        lg: ['1.125rem', { lineHeight: '1.6rem' }],
        xl: ['1.25rem', { lineHeight: '1.75rem' }],
        '2xl': ['1.5rem', { lineHeight: '2rem' }],
        '3xl': ['1.875rem', { lineHeight: '2.25rem' }],
      },
    },
  },
  plugins: [],
}
