import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
    globals: true
  },
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg"],
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      injectManifest: {
        injectionPoint: undefined
      },
      manifest: {
        name: "Familienplaner",
        short_name: "Familienplaner",
        description: "Kalender, Essensplan, Aufgaben und Taschengeld für die ganze Familie.",
        theme_color: "#e2793d",
        background_color: "#faf4e7",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any maskable" }
        ]
      }
    })
  ],
  server: {
    port: 5173
  }
});
