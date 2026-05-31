import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Self-hosted fonts. Imported here (not via CSS @import) so Vite's asset
// pipeline emits the .woff2/.woff files into the build and rewrites their URLs.
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/700.css";
import "./index.css";
import App from "./App";

async function prepare() {
  if (import.meta.env.DEV) {
    const { worker } = await import("./mock/browser");
    await worker.start({ onUnhandledRequest: "bypass" });
  }
}

void prepare().then(() => {
  const root = document.getElementById("root");
  if (!root) throw new Error("Root element not found");
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
