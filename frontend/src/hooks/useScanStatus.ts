/**
 * file: src/hooks/useScanStatus.ts
 * importable: useScanStatus
 * description: Follows one scan, asking the backend every two seconds until it ends.
 */

import { useQuery } from "@tanstack/react-query";

import { ApiError, getScan } from "../api/client";

const POLL_MS = 2000;

// A closed backend is no reason to stop, the asking goes on while there is no answer and the
// last known state stays on the screen. It only ends once a scan is seen to be over, or when
// the backend says there is no such scan.
export function useScanStatus(id: number | null) {
  return useQuery({
    queryKey: ["scan", id],
    queryFn: () => getScan(id!),
    enabled: id !== null,
    refetchInterval: (query) => {
      const { error } = query.state;
      if (error instanceof ApiError && error.status === 404) return false;
      const status = query.state.data?.status;
      return status === undefined || status === "RUNNING" ? POLL_MS : false;
    },
  });
}
