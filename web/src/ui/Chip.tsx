import type { ReactNode } from "react";

export type ChipTone = "ok" | "warn" | "fuse" | "neutral";

const TONE: Record<ChipTone, string> = {
  ok: "bg-[rgba(22,163,74,.10)] text-[#166534]",
  warn: "bg-[rgba(234,88,12,.12)] text-[#9a3412]",
  fuse: "bg-[rgba(220,38,38,.10)] text-[#991b1b]",
  neutral: "bg-[rgba(107,114,128,.12)] text-[#374151]",
};

export function Chip({ tone = "neutral", children }: { tone?: ChipTone; children: ReactNode }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 align-middle text-[11px] font-normal tracking-normal whitespace-nowrap ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}
