import { cn } from "../../../lib/cn";
import type { Turn } from "../../../lib/parseTranscript";
import type { Tone } from "../../../lib/tones";
import { Badge } from "../../atoms/Badge/component";
import { messageCardVariants } from "./variants";

export interface MessageCardProps {
  turn: Turn;
  tone: Tone;
}

export function MessageCard({ turn, tone }: MessageCardProps) {
  return (
    <article className={cn(messageCardVariants({ tone }))}>
      <header className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Badge tone={tone}>{turn.speaker}</Badge>
          <span className="text-xs text-stone-500">Turn {turn.turn}</span>
        </div>
        {turn.runId && (
          <code className="truncate text-[10px] text-stone-400" title={turn.runId}>
            {turn.runId.slice(0, 12)}
          </code>
        )}
      </header>
      {turn.parenthetical && (
        <p className="mb-1 text-sm italic text-stone-500">({turn.parenthetical})</p>
      )}
      <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-stone-800">{turn.text}</p>
    </article>
  );
}
