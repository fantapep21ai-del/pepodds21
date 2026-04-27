import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          blue:  '#007AFF',
          green: '#34C759',
          red:   '#FF3B30',
          orange:'#FF9500',
          purple:'#AF52DE',
          bg:    '#F5F5F7',
          card:  '#FFFFFF',
          border:'#E5E5EA',
          text:  '#1D1D1F',
          muted: '#6E6E73',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'SF Pro Display', 'Segoe UI', 'sans-serif'],
      },
      borderRadius: {
        card: '18px',
      },
      boxShadow: {
        card: '0 2px 20px rgba(0,0,0,0.07)',
      },
    },
  },
  plugins: [],
}
export default config
