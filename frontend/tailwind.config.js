/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'cyber-bg': '#0a0a0f',
        'cyber-surface': '#111118',
        'cyber-card': '#1a1a2e',
        'cyber-border': '#2a2a3e',
        'edge-cyan': '#00f0ff',
        'edge-violet': '#a855f7',
        'edge-emerald': '#34d399',
        'edge-rose': '#f43f5e',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      boxShadow: {
        'neon-cyan': '0 0 15px rgba(0, 240, 255, 0.15)',
        'neon-violet': '0 0 15px rgba(168, 85, 247, 0.15)',
        'neon-emerald': '0 0 15px rgba(52, 211, 153, 0.15)',
      },
    },
  },
  plugins: [],
}
