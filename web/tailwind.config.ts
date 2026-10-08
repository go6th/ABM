import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './design/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        canvas: 'var(--canvas)',
        raised: 'var(--raised)',
        sunken: 'var(--sunken)',
        glass: 'var(--glass)',
        border: 'var(--border)',
        textPrimary: 'var(--text-primary)',
        textSecondary: 'var(--text-secondary)',
        success: 'var(--success)',
      }
    },
  },
  plugins: [],
}
export default config
