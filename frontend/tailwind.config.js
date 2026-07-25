/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: {
          bg: "#0B0E14",
          panel: "#131720",
          border: "#1E2530",
        },
        text: {
          primary: "#E6EDF3",
          muted: "#7D8590",
        },
        bull: "#3FB950",
        bear: "#F85149",
        signal: "#E3B341",
      },
      fontFamily: {
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
        sans: ["IBM Plex Sans", "ui-sans-serif", "system-ui"],
      },
    },
  },
  plugins: [],
};
