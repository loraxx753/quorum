import { useEffect, useState } from "react";
import { parseTranscript, type Transcript } from "./parseTranscript";

/** Poll `/transcripts/latest.md` so the page updates while the conversation runs. */
export function useTranscript(intervalMs = 2000): { transcript: Transcript | null; error: string | null } {
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch(`/transcripts/latest.md?t=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const parsed = parseTranscript(await res.text());
        if (!cancelled) {
          setTranscript(parsed);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    }

    load();
    const id = setInterval(load, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return { transcript, error };
}
