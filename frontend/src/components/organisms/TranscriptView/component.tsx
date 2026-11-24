import type { Transcript } from "../../../lib/parseTranscript";
import { assignTones } from "../../../lib/tones";
import { MessageCard } from "../../molecules/MessageCard/component";

export interface TranscriptViewProps {
  transcript: Transcript;
}

export function TranscriptView({ transcript }: TranscriptViewProps) {
  const { title, meta, topic, turns, stoppedReason } = transcript;
  const tones = assignTones(turns.map((turn) => turn.speaker));

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <header className="flex flex-col gap-1 border-b border-stone-200 pb-4">
        <h1 className="text-2xl font-semibold text-stone-900">{title || "Untitled conversation"}</h1>
        <p className="text-sm text-stone-500">
          {Object.entries(meta)
            .map(([key, value]) => `${key}: ${value}`)
            .join("  ·  ")}
        </p>
        {topic && (
          <details className="mt-2 rounded-lg border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700">
            <summary className="cursor-pointer font-medium text-stone-600">Topic</summary>
            <p className="mt-2 whitespace-pre-wrap leading-relaxed">{topic}</p>
          </details>
        )}
      </header>

      {turns.length === 0 && <p className="text-stone-500">No messages yet.</p>}

      {turns.map((turn) => (
        <MessageCard key={turn.turn} turn={turn} tone={tones.get(turn.speaker) ?? "neutral"} />
      ))}

      {stoppedReason && (
        <p className="text-center text-sm text-stone-500">Stopped: {stoppedReason}</p>
      )}
    </section>
  );
}
