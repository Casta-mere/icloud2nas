import { useLang, useT, type Lang } from "../i18n";

const LANGS: Lang[] = ["zh", "en"];

export function LangSwitch() {
  const [lang, setLang] = useLang();
  const t = useT();
  return (
    <div className="flex gap-2 font-mono text-xs">
      {LANGS.map((l) => (
        <button
          key={l}
          type="button"
          aria-pressed={lang === l}
          onClick={() => setLang(l)}
          className={`whitespace-nowrap ${lang === l ? "font-extrabold underline" : "opacity-60"}`}
        >
          {t(l === "zh" ? "lang-zh" : "lang-en")}
        </button>
      ))}
    </div>
  );
}
