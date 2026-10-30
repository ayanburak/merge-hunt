/**
 * file: src/pages/ScansPage.tsx
 * importable: ScansPage
 * description: Starts a scan, follows its progress and lists earlier scans.
 */

// picked is the scan on screen, one started here, a running one found in the list or one opened
// from the list. The second covers a reload and a scan started from another tab. It is kept in
// picked so the scan stays on screen after it ends, by then the list no longer calls it running.
// Only one scan can run at a time, so the form has nothing to offer meanwhile.
//
// The form stays mounted under the progress view so New scan brings back what was typed.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError, scanDefaults, startScan } from "../api/client";
import { ScanForm } from "../components/ScanForm";
import { ScanHistory } from "../components/ScanHistory";
import { ScanProgress } from "../components/ScanProgress";
import { TopBar } from "../components/TopBar";
import { useScans, SCANS_KEY } from "../hooks/useScans";
import { useScanStatus } from "../hooks/useScanStatus";
import styles from "./ScansPage.module.css";

export function ScansPage() {
  const client = useQueryClient();
  const scans = useScans();
  const defaults = useQuery({ queryKey: ["scan-defaults"], queryFn: scanDefaults, staleTime: Infinity });
  const [picked, setPicked] = useState<number | null>(null);

  const running = scans.data?.find((scan) => scan.status === "RUNNING");
  const watched = useScanStatus(picked);
  const scan = watched.data;

  useEffect(() => {
    if (running) setPicked(running.id);
  }, [running?.id]);

  const start = useMutation({
    mutationFn: startScan,
    onSuccess: ({ id }) => {
      setPicked(id);
      client.invalidateQueries({ queryKey: SCANS_KEY });
    },
    // 409 means a scan is running that this page has not seen yet, the list brings it in.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) client.invalidateQueries({ queryKey: SCANS_KEY });
    },
  });

  // The rows of the list follow the scan being watched, so the list needs no polling of its own.
  useEffect(() => {
    if (scan) client.invalidateQueries({ queryKey: SCANS_KEY });
  }, [client, scan?.id, scan?.status, scan?.candidates, scan?.done, scan?.failed]);

  const lastDone = scans.data?.find((item) => item.status === "DONE" && item.candidates > 0);

  return (
    <>
      <title>merge-hunt · Scans</title>
      <div className={styles.sheet}>
        <TopBar />
        <div className={styles.stage}>
          <section className={styles.compose} aria-label="New scan" data-off={scan || running ? "" : undefined}>
            {defaults.data && (
              <ScanForm
                defaults={defaults.data}
                lastDone={lastDone}
                error={start.error?.message}
                busy={start.isPending}
                onStart={(options) => start.mutate(options)}
              />
            )}
            {defaults.isError && <p className={styles.problem}>{defaults.error.message}</p>}
          </section>
          {scan && (
            <ScanProgress
              scan={scan}
              unreachable={watched.isError}
              onNew={() => {
                setPicked(null);
                start.reset();
              }}
            />
          )}
        </div>
      </div>
      <ScanHistory
        scans={scans.data ?? []}
        error={scans.error?.message}
        onOpen={running ? undefined : (id) => {
          setPicked(id);
          window.scrollTo({ top: 0 });
        }}
      />
    </>
  );
}
