// Live stream client: one WebSocket, reconnect with backoff + jitter, version check,
// and render batching. Incoming messages (up to the server's UI rate) are committed to
// React at most once per animation frame; a hidden tab therefore stops rendering and
// resyncs when it becomes visible again.

import { useSyncExternalStore } from "react";
import { Ring } from "./history";
import type { Frame, Hello, Status } from "./types";

export const CLIENT_API_VERSION = 1;

export type Link = "connecting" | "open" | "closed";

export interface Snapshot {
  link: Link;
  hello: Hello | null;
  status: Status | null;
  frame: Frame | null;
  /** client-side receipt time of the last message (performance.now ms) */
  lastMessageAt: number | null;
  retryAt: number | null;
  versionMismatch: boolean;
}

const INITIAL: Snapshot = {
  link: "connecting",
  hello: null,
  status: null,
  frame: null,
  lastMessageAt: null,
  retryAt: null,
  versionMismatch: false,
};

export function backoffMs(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(10_000, 500 * 2 ** Math.min(attempt, 5));
  return Math.round(base * (0.5 + rand() * 0.5));
}

class TelemetryStore {
  private snap: Snapshot = INITIAL;
  private pending: Partial<Snapshot> | null = null;
  private listeners = new Set<() => void>();
  private ws: WebSocket | null = null;
  private attempt = 0;
  private rafId: number | null = null;
  private wantRaw = false;
  private lastSeq = -1;
  private lastSession: string | null = null;
  private started = false;
  readonly history = new Ring(60 * 130);

  start(): void {
    if (this.started) return;
    this.started = true;
    this.connect();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this.send({ type: "resync" });
    });
    // Watchdog: if the server goes silent (heartbeats are 2 Hz), treat the link as lost.
    window.setInterval(() => {
      const last = this.snap.lastMessageAt;
      if (this.ws && this.snap.link === "open" && last !== null && performance.now() - last > 3000) {
        this.ws.close();
      }
    }, 1000);
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = (): Snapshot => this.snap;

  setRawSubscription(on: boolean): void {
    this.wantRaw = on;
    this.send({ type: "subscribe", raw: on });
  }

  private send(msg: object): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private connect(): void {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/v1/stream`);
    this.ws = ws;
    this.commitNow({ link: "connecting", retryAt: null });
    ws.onopen = () => {
      this.attempt = 0;
      this.commitNow({ link: "open" });
      if (this.wantRaw) this.send({ type: "subscribe", raw: true });
    };
    ws.onmessage = (ev) => this.onMessage(ev.data as string);
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      const delay = backoffMs(this.attempt++);
      this.commitNow({ link: "closed", retryAt: performance.now() + delay });
      window.setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
  }

  private onMessage(text: string): void {
    let msg: { type: string; status?: Status; frame?: Frame | null } & Partial<Hello>;
    try {
      msg = JSON.parse(text);
    } catch {
      return;
    }
    const now = performance.now();
    if (msg.type === "hello") {
      this.schedule({ hello: msg as Hello, versionMismatch: msg.api_version !== CLIENT_API_VERSION, lastMessageAt: now });
      return;
    }
    if (msg.type !== "frame" || !msg.status) return;
    const frame = msg.frame ?? null;
    const update: Partial<Snapshot> = { status: msg.status, lastMessageAt: now };
    if (frame === null) {
      update.frame = null;
      this.lastSeq = -1;
    } else {
      // Session/mode change: the old trace no longer describes the new stream.
      if (frame.session_id !== this.lastSession) {
        this.history.clear();
        this.lastSession = frame.session_id;
      }
      if (frame.sequence !== this.lastSeq) {
        this.lastSeq = frame.sequence;
        const n = frame.normalized;
        this.history.push({
          t: now / 1000,
          speed: frame.derived.speed_kmh,
          rpm: n.rpm,
          throttle: n.inputs.throttle,
          brake: n.inputs.brake,
          steer: n.inputs.steer,
        });
      }
      // Keep the last raw layer while the inspector is open and frames arrive without it.
      if (!frame.raw && this.snap.frame?.raw && this.wantRaw) frame.raw = this.snap.frame.raw;
      update.frame = frame;
    }
    this.schedule(update);
  }

  private schedule(update: Partial<Snapshot>): void {
    this.pending = { ...(this.pending ?? {}), ...update };
    if (this.rafId === null) {
      this.rafId = requestAnimationFrame(() => {
        this.rafId = null;
        const p = this.pending;
        this.pending = null;
        if (p) this.commitNow(p);
      });
    }
  }

  private commitNow(update: Partial<Snapshot>): void {
    this.snap = { ...this.snap, ...update };
    for (const fn of this.listeners) fn();
  }
}

export const store = new TelemetryStore();

export function useTelemetry(): Snapshot {
  return useSyncExternalStore(store.subscribe, store.getSnapshot);
}

export async function setDemo(enabled: boolean): Promise<void> {
  const r = await fetch("/api/v1/demo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!r.ok) {
    throw new Error(
      r.status === 403 ? "this device may only view (changes are allowed from the Pitwall PC)" : `the server answered ${r.status}`,
    );
  }
  store.history.clear();
}
