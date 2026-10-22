import { useCallback, useState } from "react";
import { ApiError } from "../api/client";

/** Tracks one long-running action at a time: a label, a progress line, and the last error. */
export function useBusy() {
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async <T,>(label: string, fn: (report: (p: string) => void) => Promise<T>): Promise<T | undefined> => {
    setBusy(label);
    setProgress("");
    setError(null);
    try {
      return await fn(setProgress);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e));
      return undefined;
    } finally {
      setBusy(null);
      setProgress("");
    }
  }, []);

  return { busy, progress, error, setError, run };
}
