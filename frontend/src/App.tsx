/**
 * file: src/App.tsx
 * importable: App
 * description: Maps each address to its page.
 */

import { Navigate, Route, Routes } from "react-router";

import { RepoDetailPage } from "./pages/RepoDetailPage";
import { ResultsPage } from "./pages/ResultsPage";
import { ScansPage } from "./pages/ScansPage";

// Results live under the scan id so a reload or a shared link lands on the same scan.
// An unknown address goes back to the scans, there is no separate not found page.
export function App() {
  return (
    <Routes>
      <Route path="/" element={<ScansPage />} />
      <Route path="/scans/:id/results" element={<ResultsPage />} />
      <Route path="/repos/:id" element={<RepoDetailPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
