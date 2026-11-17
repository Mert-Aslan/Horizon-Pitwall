// Transport liveness uses message receipt time. Rendering is batched separately.
// Frame sequences are scoped to the server instance and stream segment.

import { useSyncExternalStore } from "react";
import { Ring } from "./history";
import type { Frame, Hello, RawSample, Status } from "./types";

export const CLIENT_API_VERSION = 1;
export const WATCHDOG_MS = 3000; // heartbeats are 2 Hz; 3 s without any message = link lost
const MAX_BAD_IN_ROW = 5;

export type Link = "connecting" | "open" | "closed";

export interface Snapshot {
  link: Link;
  hello: Hello | null;
  status: Status | null;
  frame: Frame | null;
  /** last raw-layer sample (inspector), with the stream it belongs to; null after a stream change */
  raw: RawSample | null;
  /** client receipt time (performance.now ms) of the last *committed* message */
  lastMessageAt: number | null;
  retryAt: number | null;
  versionMismatch: boolean;
  /** the server serves a different UI bundle than the one running in this page */
  uiBuildMismatch: boolean;
  /** increments whenever a hello reports a new server_instance_id (server restarted) */
  serverRestarts: number;
  /** set when the server keeps sending messages this page cannot read */
  protocolError: string | null;
  badMessages: number;
  /** the game is sending packets without driving data (IsRaceOn = 0, all fields zero);
   *  `frame` then keeps the last frame that had data, if it belongs to this stream */
  telemetryEmpty: boolean;
}

const INITIAL: Snapshot = {
  link: "connecting",
  hello: null,
  status: null,
  frame: null,
  raw: null,
  lastMessageAt: null,
  retryAt: null,
  versionMismatch: false,
  uiBuildMismatch: false,
  serverRestarts: 0,
  protocolError: null,
  badMessages: 0,
  telemetryEmpty: false,
};

export function backoffMs(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(10_000, 500 * 2 ** Math.min(attempt, 5));
  return Math.round(base * (0.5 + rand() * 0.5));
}

// ------------------------------------------------------------------ validation
const CONN = new Set(["starting", "waiting", "receiving", "stale", "error"]);
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isFiniteNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export type Parsed =
  | { kind: "hello"; hello: Hello }
  | { kind: "frame"; status: Status; frame: Frame | null }
  | { kind: "invalid"; reason: string };

/** Light, bounded runtime check of the server message shape (TypeScript types are not
 *  checked at runtime). Unknown extra fields are fine; missing essentials are not. */
export function parseMessage(text: string): Parsed {
  let m: unknown;
  try {
    m = JSON.parse(text);
  } catch {
    return { kind: "invalid", reason: "not JSON" };
  }
  if (!isObj(m) || typeof m.type !== "string") return { kind: "invalid", reason: "no message type" };
  if (m.type === "hello") {
    if (!isFiniteNum(m.api_version)) return { kind: "invalid", reason: "hello without api_version" };
    return { kind: "hello", hello: m as unknown as Hello };
  }
  if (m.type !== "frame") return { kind: "invalid", reason: `unknown message type ${String(m.type).slice(0, 20)}` };
  const st = m.status;
  if (!isObj(st) || typeof st.connection_state !== "string" || !CONN.has(st.connection_state) || typeof st.mode !== "string") {
    return { kind: "invalid", reason: "frame message without a valid status" };
  }
  const f = m.frame;
  if (f === null || f === undefined) return { kind: "frame", status: st as unknown as Status, frame: null };
  if (!isObj(f) || !isFiniteNum(f.sequence) || !isObj(f.normalized) || !isObj(f.derived) || !isObj(f.quality)) {
    return { kind: "invalid", reason: "frame without sequence/normalized/derived/quality" };
  }
  if (typeof (f.stream_segment_id ?? f.session_id) !== "string") return { kind: "invalid", reason: "frame without a stream id" };
  return { kind: "frame", status: st as unknown as Status, frame: f as unknown as Frame };
}

export function isEmptyFrame(frame: Frame | null | undefined): boolean {
  return !!frame?.quality?.flags?.includes("empty_packet");
}

export function streamKey(frame: Frame, helloInstance: string | null): string {
  const inst = frame.server_instance_id ?? helloInstance ?? "?";
  return `${inst}|${frame.stream_segment_id ?? frame.session_id}`;
}

// ------------------------------------------------------------------ environment
export interface SocketLike {
  readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((ev?: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev?: unknown) => void) | null;
  onerror: ((ev?: unknown) => void) | null;
}

export interface StreamEnv {
  makeSocket(): SocketLike;
  now(): number;
  raf(cb: () => void): number;
  cancelRaf(id: number): void;
  setInterval(cb: () => void, ms: number): number;
  clearInterval(id: number): void;
  setTimeout(cb: () => void, ms: number): number;
  clearTimeout(id: number): void;
  /** subscribe to tab visibility; returns an unsubscribe function */
  onVisible(cb: () => void): () => void;
  random(): number;
  /** build id compiled into this bundle (null in tests / dev) */
  uiBuildId: string | null;
}

declare const __UI_BUILD_ID__: string | undefined;

export function browserEnv(): StreamEnv {
  return {
    makeSocket: () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      return new WebSocket(`${proto}://${location.host}/api/v1/stream`) as unknown as SocketLike;
    },
    now: () => performance.now(),
    raf: (cb) => requestAnimationFrame(cb),
    cancelRaf: (id) => cancelAnimationFrame(id),
    setInterval: (cb, ms) => window.setInterval(cb, ms),
    clearInterval: (id) => window.clearInterval(id),
    setTimeout: (cb, ms) => window.setTimeout(cb, ms),
    clearTimeout: (id) => window.clearTimeout(id),
    onVisible: (cb) => {
      const h = () => {
        if (!document.hidden) cb();
      };
      document.addEventListener("visibilitychange", h);
      return () => document.removeEventListener("visibilitychange", h);
    },
    random: Math.random,
    uiBuildId: typeof __UI_BUILD_ID__ === "string" ? __UI_BUILD_ID__ : null,
  };
}

const OPEN = 1;

export class TelemetryStore {
  private snap: Snapshot = INITIAL;
  private pending: Partial<Snapshot> | null = null;
  private listeners = new Set<() => void>();
  private ws: SocketLike | null = null;
  private attempt = 0;
  private rafId: number | null = null;
  private watchdogId: number | null = null;
  private reconnectId: number | null = null;
  private offVisible: (() => void) | null = null;
  private wantRaw = false;
  private running = false;
  private lastSeq = -1;
  private key: string | null = null;
  private instance: string | null = null;
  private badInRow = 0;
  private badTotal = 0;
  private restarts = 0;
  /** flow control: messages received on the current socket, acked every `ackEvery` */
  private rxCount = 0;
  private ackEvery = 0;
  /** transport liveness: updated synchronously on every received message */
  lastRxAt: number | null = null;
  readonly history = new Ring(60 * 130);
  private envFactory: () => StreamEnv;
  private envCache: StreamEnv | null = null;

  constructor(env?: StreamEnv | (() => StreamEnv)) {
    this.envFactory = typeof env === "function" ? env : () => env ?? browserEnv();
  }

  private get env(): StreamEnv {
    return (this.envCache ??= this.envFactory());
  }

  /** Idempotent. Returns a cleanup function (React effect friendly). */
  start = (): (() => void) => {
    if (!this.running) {
      this.running = true;
      const env = this.env;
      this.offVisible = env.onVisible(() => this.send({ type: "resync" }));
      this.watchdogId = env.setInterval(() => this.watchdog(), 1000);
      this.connect();
    }
    return this.stop;
  };

  /** Idempotent: closes the socket, detaches its callbacks, clears every timer. */
  stop = (): void => {
    if (!this.running) return;
    this.running = false;
    const env = this.env;
    if (this.watchdogId !== null) env.clearInterval(this.watchdogId);
    if (this.reconnectId !== null) env.clearTimeout(this.reconnectId);
    if (this.rafId !== null) env.cancelRaf(this.rafId);
    this.watchdogId = this.reconnectId = this.rafId = null;
    this.offVisible?.();
    this.offVisible = null;
    this.pending = null;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    }
  };

  dispose(): void {
    this.stop();
    this.listeners.clear();
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
    if (this.ws && this.ws.readyState === OPEN) this.ws.send(JSON.stringify(msg));
  }

  private watchdog(): void {
    const ws = this.ws;
    if (!ws || this.snap.link !== "open" || this.lastRxAt === null) return;
    if (this.env.now() - this.lastRxAt > WATCHDOG_MS) ws.close();
  }

  private connect(): void {
    if (!this.running) return;
    this.reconnectId = null;
    const ws = this.env.makeSocket();
    this.ws = ws;
    this.commitNow({ link: "connecting", retryAt: null });
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempt = 0;
      this.rxCount = 0;
      this.ackEvery = 0;
      this.lastRxAt = this.env.now(); // an open socket that never speaks is caught by the watchdog
      this.commitNow({ link: "open" });
      if (this.wantRaw) this.send({ type: "subscribe", raw: true });
    };
    ws.onmessage = (ev) => {
      if (this.ws !== ws) return; // late callback from a replaced socket
      this.onMessage(String(ev.data));
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (!this.running) return;
      const delay = backoffMs(this.attempt++, this.env.random);
      this.commitNow({ link: "closed", retryAt: this.env.now() + delay });
      this.reconnectId = this.env.setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
  }

  /** exposed for tests */
  onMessage(text: string): void {
    const now = this.env.now();
    this.lastRxAt = now;
    // Acknowledge receipt independently of rendering.
    this.rxCount++;
    if (this.ackEvery > 0 && this.rxCount % this.ackEvery === 0) this.send({ type: "ack", n: this.rxCount });
    const msg = parseMessage(text);
    if (msg.kind === "invalid") {
      this.badInRow++;
      this.badTotal++;
      if (this.badInRow >= MAX_BAD_IN_ROW) {
        this.schedule({ protocolError: `The server sent data this page cannot read (${msg.reason}).`, badMessages: this.badTotal });
      }
      return;
    }
    this.badInRow = 0;
    const base: Partial<Snapshot> = { lastMessageAt: now, protocolError: null, badMessages: this.badTotal };
    if (msg.kind === "hello") {
      const h = msg.hello;
      const inst = h.server_instance_id ?? null;
      if (inst && this.instance && inst !== this.instance) this.restarts++;
      if (inst) this.instance = inst;
      if (h.flow && h.flow.ack_every > 0) {
        this.ackEvery = h.flow.ack_every;
        this.send({ type: "flow", ack: true });
      }
      const own = this.env.uiBuildId;
      this.schedule({
        ...base,
        hello: h,
        versionMismatch: h.api_version !== CLIENT_API_VERSION,
        uiBuildMismatch: !!own && !!h.ui_build_id && own !== h.ui_build_id,
        serverRestarts: this.restarts,
      });
      return;
    }
    const update: Partial<Snapshot> = { ...base, status: msg.status };
    const frame = msg.frame;
    if (frame === null) {
      update.frame = null;
      update.raw = null;
      update.telemetryEmpty = false;
      this.lastSeq = -1;
    } else {
      const key = streamKey(frame, this.instance);
      if (key !== this.key) {
        // Reset state when the stream changes.
        this.history.clear();
        this.key = key;
        this.lastSeq = -1;
        update.raw = null;
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
      if (frame.raw) {
        update.raw = { values: frame.raw, sequence: frame.sequence, streamKey: key, receivedAt: now };
      }
      const { raw: _raw, ...rest } = frame;
      const empty = isEmptyFrame(frame);
      update.telemetryEmpty = empty;
      const prev = this.pending && "frame" in this.pending ? this.pending.frame : this.snap.frame;
      const keepPrev = empty && !!prev && !isEmptyFrame(prev) && key === streamKey(prev, this.instance);
      // An empty packet is not a measurement: keep showing the last real values (dimmed by
      // the view state) instead of zeros/dashes. The trace still records the gap.
      if (!keepPrev) update.frame = rest as Frame;
    }
    this.schedule(update);
  }

  private schedule(update: Partial<Snapshot>): void {
    this.pending = { ...(this.pending ?? {}), ...update };
    if (this.rafId === null && this.running) {
      this.rafId = this.env.raf(() => {
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

async function post(path: string, body: object): Promise<Response> {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function controlError(r: Response): Error {
  return new Error(r.status === 403 ? "this device may only view (changes are allowed from the Pitwall PC)" : `the server answered ${r.status}`);
}

export async function setDemo(enabled: boolean): Promise<void> {
  const r = await post("/api/v1/demo", { enabled });
  if (!r.ok) throw controlError(r);
  store.history.clear();
}

/** Start/stop a recording. 409 means the server already is in the requested state; the
 *  caller re-reads the server state instead of assuming success. */
export async function setRecording(on: boolean, title?: string): Promise<{ ok: boolean; conflict: boolean; body: unknown }> {
  const r = await post(on ? "/api/v1/recordings/start" : "/api/v1/recordings/stop", on ? { title } : {});
  if (r.status === 409) return { ok: false, conflict: true, body: await r.json().catch(() => null) };
  if (!r.ok) throw controlError(r);
  return { ok: true, conflict: false, body: await r.json() };
}
