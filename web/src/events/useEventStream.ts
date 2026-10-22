import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { notifyUnauthorized, request } from "../api/client";
import { invalidate, keys } from "../api/queries";
import type { EventPayloads, EventTopic, Session } from "../api/types";

/** In-app bus: `run.event` and `run.finished` payloads are re-dispatched here for the code card and the wizard. */
export const appEvents = new EventTarget();

export const MAX_BACKOFF_MS = 30_000;
export const backoffMs = (attempt: number) => Math.min(MAX_BACKOFF_MS, 1000 * 2 ** attempt);

type Invalidations = { [T in EventTopic]: (payload: EventPayloads[T]) => readonly QueryKey[] };

/** What each topic makes stale (events are hints: re-read the resource). */
export const INVALIDATIONS: Invalidations = {
  "account.status": () => [keys.accounts],
  "account.updated": (p) => (p.deleted ? [keys.accounts, keys.libraries, keys.runsAll] : [keys.accounts]),
  "library.updated": () => [keys.libraries],
  "run.started": () => [keys.runsAll, keys.status],
  // auto-delete moves files into the trash at the end of a sync
  "run.finished": () => [keys.runsAll, keys.status, keys.libraries, keys.accounts, keys.trash],
  "run.event": () => [],
  "plan.pending": () => [keys.plansAll, keys.libraries],
  "plan.resolved": () => [keys.plansAll, keys.libraries],
  "space.low": () => [keys.status, keys.libraries],
  "space.ok": () => [keys.status, keys.libraries],
  paused: () => [keys.status, keys.libraries],
  "settings.updated": () => [keys.settings],
};

const TOPICS = Object.keys(INVALIDATIONS) as EventTopic[];

function parse(ev: Event): unknown {
  try {
    return JSON.parse((ev as MessageEvent<string>).data);
  } catch {
    return {};
  }
}

/** Opens `/api/events` once and keeps the query cache fresh; `connected` after `hello`. */
export function useEventStream(): { connected: boolean } {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let disposed = false;
    let helloSeen = false;

    const reconnectOrStop = async () => {
      try {
        const session = await request<Session>("GET", "/api/session");
        if (disposed) return;
        if (session.auth_required && !session.authenticated) {
          notifyUnauthorized();
          return;
        }
      } catch {
        // server unreachable: keep retrying on the schedule
      }
      if (disposed) return;
      timer = setTimeout(connect, backoffMs(attempt));
      attempt += 1;
    };

    function connect() {
      const es = new EventSource("/api/events");
      source = es;
      es.addEventListener("hello", () => {
        attempt = 0;
        setConnected(true);
        // a reconnect: events sent during the outage are lost, so re-read everything
        if (helloSeen) {
          void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== keys.session[0] });
        }
        helloSeen = true;
      });
      for (const topic of TOPICS) {
        es.addEventListener(topic, (ev) => {
          const payload = parse(ev);
          if (topic === "run.event" || topic === "run.finished") {
            appEvents.dispatchEvent(new CustomEvent(topic, { detail: payload }));
          }
          const stale = (INVALIDATIONS[topic] as (p: unknown) => readonly QueryKey[])(payload);
          void invalidate(qc, stale);
        });
      }
      es.onerror = () => {
        es.close();
        if (source !== es || disposed) return;
        source = null;
        setConnected(false);
        void reconnectOrStop();
      };
    }

    connect();
    return () => {
      disposed = true;
      clearTimeout(timer);
      source?.close();
    };
  }, [qc]);

  return { connected };
}
