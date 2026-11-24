import type { HTMLAttributes } from "react";
import { cn } from "../../../lib/cn";
import { badgeVariants, type BadgeVariants } from "./variants";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, BadgeVariants {}

export function Badge({ tone, className, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
