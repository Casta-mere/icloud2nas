import type { ReactNode } from "react";

export type CardTone = "neutral" | "ok" | "warn" | "fuse" | "soft";

const SHADOW: Record<CardTone, string> = {
  neutral: "shadow-card",
  ok: "shadow-ok",
  warn: "shadow-warn",
  fuse: "shadow-fuse",
  soft: "shadow-soft",
};

export function Card({
  tone = "neutral",
  className = "",
  children,
}: {
  tone?: CardTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`relative min-w-0 rounded-card border-2 border-ink bg-[rgba(255,255,255,.62)] p-4 backdrop-blur-[10px] ${SHADOW[tone]} ${className}`}
    >
      {children}
    </div>
  );
}
