import { useState } from "react";
import { ApiError } from "../../api/client";
import { useApprovePlan, usePlanDetail, useRejectPlan } from "../../api/queries";
import type { Library, Plan } from "../../api/types";
import { useT } from "../../i18n";
import { fmtTime } from "../../lib/format";
import { useRetentionText } from "../../lib/retention";
import { Button } from "../../ui/Button";

const MARK = "\u0000";

/** The pending reconcile plan of a library; `approved` means the apply run is under way. */
export function PlanCard({ plan }: { plan: Plan; library: Library }) {
  const t = useT();
  const retention = useRetentionText();
  const detail = usePlanDetail(plan.id);
  const approve = useApprovePlan();
  const reject = useRejectPlan();
  const [wasApproved, setWasApproved] = useState(plan.status === "approved");
  if (plan.status === "approved" && !wasApproved) setWasApproved(true);

  const applying = plan.status === "approved" || approve.isPending;
  const failed = plan.status === "pending" && wasApproved && !approve.isPending;
  const paths = detail.data?.paths ?? [];
  const total = detail.data?.total ?? plan.extraneous_count;
  const [before = "", after = ""] = t("plan-text", { n: MARK, retention }).split(MARK);
  const error = [approve.error, reject.error].map((e) => (e instanceof ApiError ? e.detail : null)).find(Boolean);

  return (
    <section className="mt-3 border-t-2 border-[rgba(17,17,17,.12)] pt-3">
      <p className="m-0 text-[13px] text-[#374151]">
        {before}
        <b>{plan.extraneous_count}</b>
        {after}
      </p>
      {paths.length > 0 ? (
        <ul className="m-0 mt-2 max-h-[120px] list-none overflow-auto rounded-[10px] border-2 border-[rgba(17,17,17,.15)] bg-white p-2 font-mono text-[12px]">
          {paths.map((p) => (
            <li key={p} className="truncate">
              {p}
            </li>
          ))}
          {total > paths.length ? <li className="text-[#6b7280]">{t("plan-more", { n: total - paths.length })}</li> : null}
        </ul>
      ) : null}
      {failed ? (
        <div role="alert" className="mt-2 text-[12px] text-[#991b1b]">
          {t("apply-failed")}
        </div>
      ) : null}
      {error ? (
        <div role="alert" className="mt-2 text-[12px] text-[#9a3412]">
          {error}
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={applying || reject.isPending}
          onClick={() => {
            setWasApproved(false);
            approve.mutate(plan.id);
          }}
        >
          {applying ? t("plan-applying") : t("plan-approve", { n: plan.extraneous_count })}
        </Button>
        <Button disabled={applying || reject.isPending} onClick={() => reject.mutate(plan.id)}>
          {t("plan-reject")}
        </Button>
        <span className="ml-auto text-[12px] text-[#6b7280] tabular-nums">
          {t("plan-created", { time: fmtTime(plan.created_at) })}
        </span>
      </div>
    </section>
  );
}
