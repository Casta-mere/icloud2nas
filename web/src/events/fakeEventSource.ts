/**
 * A minimal EventSource stand-in for tests (MSW 2 does not mock EventSource).
 * `setup.ts` installs it on `globalThis`; tests drive it with `emit`/`fail`.
 */
export class FakeEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readonly withCredentials: boolean;
  readyState = FakeEventSource.CONNECTING;
  onerror: ((ev: Event) => void) | null = null;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;

  constructor(url: string | URL, init?: EventSourceInit) {
    super();
    this.url = String(url);
    this.withCredentials = init?.withCredentials ?? false;
    FakeEventSource.instances.push(this);
  }

  static latest(): FakeEventSource {
    const last = FakeEventSource.instances.at(-1);
    if (!last) throw new Error("no EventSource was opened");
    return last;
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }

  /** Deliver one SSE frame `event: <type>\ndata: <json>`. */
  emit(type: string, data: unknown = {}): void {
    if (this.readyState === FakeEventSource.CLOSED) return;
    this.readyState = FakeEventSource.OPEN;
    this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) }));
  }

  /** Simulate a dropped connection. */
  fail(): void {
    if (this.readyState === FakeEventSource.CLOSED) return;
    const ev = new Event("error");
    this.onerror?.(ev);
    this.dispatchEvent(ev);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }
}
