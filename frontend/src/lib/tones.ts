import type { BadgeVariants } from "../components/atoms/Badge/variants";

export type Tone = NonNullable<BadgeVariants["tone"]>;

/** Palette handed out to speakers in order of first appearance. */
export const TONE_PALETTE: Tone[] = ["amber", "sky", "emerald", "violet", "rose"];

/** Give each speaker a stable tone, in the order they first speak. */
export function assignTones(speakers: string[]): Map<string, Tone> {
  const tones = new Map<string, Tone>();
  for (const speaker of speakers) {
    if (!tones.has(speaker)) {
      tones.set(speaker, TONE_PALETTE[tones.size % TONE_PALETTE.length]);
    }
  }
  return tones;
}
