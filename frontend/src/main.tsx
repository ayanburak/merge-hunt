/**
 * file: src/main.tsx
 * description: Mounts the app and sets up what every page needs, the font, the styles and the query cache.
 */

// The wdth file is the one that carries the width axis, the plain import only has weight and the
// narrow numbers of the design would silently fall back to normal width.

import "@fontsource-variable/archivo/wdth.css";
import "./styles/tokens.css";
import "./styles/base.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { shouldRetry } from "./api/client";
import { App } from "./App";

// One retry is enough, the backend runs on the same machine. With the default three a closed
// backend would take seven seconds to show its error.
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: shouldRetry } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
