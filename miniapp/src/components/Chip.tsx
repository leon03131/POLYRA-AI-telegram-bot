import type { ReactNode } from "react";
import type { ChipTone } from "../utils";

interface ChipProps {
  tone?: ChipTone;
  children: ReactNode;
}

export function Chip({ tone = "default", children }: ChipProps) {
  const cls = tone === "default" ? "chip" : `chip chip-${tone}`;
  return <span className={cls}>{children}</span>;
}
