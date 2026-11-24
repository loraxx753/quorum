import { cva, type VariantProps } from "class-variance-authority";

export const messageCardVariants = cva(
  "rounded-2xl border p-5 shadow-sm transition-colors",
  {
    variants: {
      tone: {
        amber: "border-amber-200 bg-amber-50/60",
        sky: "border-sky-200 bg-sky-50/60",
        emerald: "border-emerald-200 bg-emerald-50/60",
        violet: "border-violet-200 bg-violet-50/60",
        rose: "border-rose-200 bg-rose-50/60",
        neutral: "border-stone-200 bg-white",
      },
    },
    defaultVariants: {
      tone: "neutral",
    },
  },
);

export type MessageCardVariants = VariantProps<typeof messageCardVariants>;
