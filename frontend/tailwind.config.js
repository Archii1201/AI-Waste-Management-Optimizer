/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["DM Sans", "Segoe UI", "system-ui", "sans-serif"],
      },
      colors: {
        ink: "#0b1220",
        panel: "#121a2b",
        line: "#243049",
      },
    },
  },
  plugins: [],
};
