import { ApiError } from "../api/client";
import { useT } from "../i18n";
import { Button } from "./Button";
import { Card } from "./Card";

interface Loadable {
  data: unknown;
  error: unknown;
  refetch: () => unknown;
}

/** A page's first load: a soft card while pending, a warn card with the detail and 重试 on error. */
export function QueryState({ queries }: { queries: Loadable[] }) {
  const t = useT();
  const failed = queries.find((q) => q.data === undefined && q.error);
  if (failed) {
    const e = failed.error;
    const detail = e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e);
    return (
      <Card tone="warn">
        <div role="alert" className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 basis-[200px] text-[13px] text-[#9a3412]">
            {t("load-failed", { detail })}
          </span>
          <Button
            variant="ghost"
            onClick={() => {
              for (const q of queries) if (q.data === undefined) void q.refetch();
            }}
          >
            {t("retry")}
          </Button>
        </div>
      </Card>
    );
  }
  return (
    <Card tone="soft">
      <div role="status" className="text-[13px] text-[#6b7280]">
        {t("loading")}
      </div>
    </Card>
  );
}
