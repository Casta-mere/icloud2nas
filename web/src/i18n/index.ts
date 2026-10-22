import { createContext, createElement, useContext, useState, type ReactNode } from "react";
import { en } from "./en";
import { zh, type Key } from "./zh";

export type Lang = "zh" | "en";
export type { Key };

const STORAGE_KEY = "i2n.lang";
const dictionaries: Record<Lang, Record<Key, string>> = { zh, en };

export function defaultLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "zh" || saved === "en") return saved;
  } catch {
    // storage unavailable: fall through to the browser language
  }
  return navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}

let currentLang: Lang = defaultLang();

/** Translate with the language last selected (module-level; React code should prefer `useT`). */
export function t(key: Key, vars?: Record<string, string | number>): string {
  return translate(currentLang, key, vars);
}

export function translate(
  lang: Lang,
  key: Key,
  vars?: Record<string, string | number>,
): string {
  const text: string = dictionaries[lang][key];
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  );
}

type Ctx = { lang: Lang; setLang: (l: Lang) => void };
const I18nContext = createContext<Ctx | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    currentLang = defaultLang();
    return currentLang;
  });
  const setLang = (l: Lang) => {
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      // not persisted; still switch for this session
    }
    currentLang = l;
    setLangState(l);
  };
  return createElement(I18nContext.Provider, { value: { lang, setLang } }, children);
}

function useCtx(): Ctx {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("I18nProvider missing");
  return ctx;
}

export function useLang(): [Lang, (l: Lang) => void] {
  const { lang, setLang } = useCtx();
  return [lang, setLang];
}

export function useT(): (key: Key, vars?: Record<string, string | number>) => string {
  const { lang } = useCtx();
  return (key, vars) => translate(lang, key, vars);
}
