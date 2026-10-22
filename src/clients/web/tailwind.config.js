/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                'chad-bg': '#0a0b10',
                'chad-panel': 'rgba(20, 25, 30, 0.6)',
                'chad-accent': '#00f0ff',
                'chad-accent-secondary': '#7000ff',
                'chad-success': '#00ff9d',
                'chad-error': '#ff0055',
                'chad-text': '#ffffff',
                'chad-text-muted': '#8b9bb4',
                'chad-border': 'rgba(255, 255, 255, 0.1)',
            },
            fontFamily: {
                sans: ['Inter', 'system-ui', 'sans-serif'],
                mono: ['JetBrains Mono', 'monospace'],
            },
            animation: {
                'pulse-slow': 'pulse 2s infinite',
                'slide-in': 'slideIn 0.3s ease-out',
            },
            keyframes: {
                slideIn: {
                    '0%': { opacity: '0', transform: 'translateY(10px)' },
                    '100%': { opacity: '1', transform: 'translateY(0)' },
                },
            },
        },
    },
    plugins: [],
}
