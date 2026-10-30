/**
 * file: src/hooks/useScans.ts
 * importable: useScans, SCANS_KEY
 * description: The latest scans, shared by the top bar, the scan page and the history list.
 */

import { useQuery } from "@tanstack/react-query";

import { listScans } from "../api/client";

export const SCANS_KEY = ["scans"];

export function useScans() {
  return useQuery({ queryKey: SCANS_KEY, queryFn: () => listScans() });
}
