import { TranscriptView } from "../components/organisms/TranscriptView/component";
import { useTranscript } from "../lib/useTranscript";

export function ConversationPage() {
  const { transcript, error } = useTranscript();

  return (
    <main className="min-h-screen bg-stone-50 px-4 py-10">
      {transcript ? (
        <TranscriptView transcript={transcript} />
      ) : (
        <p className="text-center text-stone-500">
          {error ? `No transcript yet (${error}). Run the backend script first.` : "Loading…"}
        </p>
      )}
    </main>
  );
}
