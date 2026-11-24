import { cva, type VariantProps } from "class-variance-authority";

export const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium tracking-wide",
  {
    variants: {
      tone: {
        amber: "bg-amber-100 text-amber-900",
        sky: "bg-sky-100 text-sky-900",
        emerald: "bg-emerald-100 text-emerald-900",
        violet: "bg-violet-100 text-violet-900",
        rose: "bg-rose-100 text-rose-900",
        neutral: "bg-stone-200 text-stone-800",
      },
    },
    defaultVariants: {
      tone: "neutral",
    },
  },
);

export type BadgeVariants = VariantProps<typeof badgeVariants>;
