/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'cyber-bg': '#f3f2ec',
        'cyber-surface': '#f8f7f3',
        'cyber-card': '#ffffff',
        'cyber-border': '#d4d1c8',
        'edge-cyan': '#e2231a',
        'edge-violet': '#b41414',
        'edge-emerald': '#242424',
        'edge-rose': '#c8102e',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      boxShadow: {
        'neon-cyan': '0 10px 28px rgba(226, 35, 26, 0.18)',
        'neon-violet': '0 10px 28px rgba(36, 36, 36, 0.16)',
        'neon-emerald': '0 10px 28px rgba(36, 36, 36, 0.12)',
      },
    },
  },
  plugins: [],
}
