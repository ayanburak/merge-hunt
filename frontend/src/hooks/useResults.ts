/**
 * file: src/hooks/useResults.ts
 * importable: useResults
 * description: Scores a scan with the given settings, once the user has stopped changing them.
 */

// Scoring 54 repos takes about a second, so nothing is sent while a slider moves. The last good
// answer stays on screen in the meantime and together with the settings that made it, a row
// explaining why it failed has to quote those and not the ones still being dragged.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { scanResults } from "../api/client";
import type { ProfileSettings, ScoredRepo } from "../api/client";

const WAIT_MS = 320;

type Shown = { repos: ScoredRepo[]; settings: ProfileSettings };

export function useResults(id: number, settings: ProfileSettings) {
  const [asked, setAsked] = useState(settings);

  useEffect(() => {
    const timer = setTimeout(() => setAsked(settings), WAIT_MS);
    return () => clearTimeout(timer);
  }, [settings]);

  // A rejected setting would fail the same way twice, so there is no retry here.
  const query = useQuery({
    queryKey: ["results", id, asked],
    queryFn: () => scanResults(id, asked),
    retry: false,
  });

  const shown = useRef<Shown | null>(null);
  if (query.data) shown.current = { repos: query.data, settings: asked };

  return {
    repos: shown.current?.repos,
    applied: shown.current?.settings,
    updating: settings !== asked || query.isFetching,
    error: query.error,
  };
}
