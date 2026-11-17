// Replay uses bounded fetch windows and a recording-relative clock.
// Seek generations discard stale responses.

import { Ring, type Sample } from "./history";
import { isEmptyFrame } from "./stream";
import type { Frame } from "./types";

export const LOOKBACK_S = 22; // the trace shows 20 s before the playhead
export const AHEAD_S = 12; // fetched ahead of the playhead
export const PREFETCH_MARGIN_S = 5;
export const GAP_S = 0.25; // no sample this close to the playhead = a gap in the recording
export const RATES = [0.5, 1, 2] as const;
/** Seeks in quick succession (dragging the timeline, holding an arrow key) only load the
 *  position where the user settles. Aborting a fetch does not stop the server's work, so
 *  without this a drag queued dozens of window decodes (measured: ~5 s delay). */
export const SEEK_SETTLE_MS = 150;
export type Rate = (typeof RATES)[number];

export interface Row {
  i: number;
  el: number;
  frame: Frame;
}

export interface Window {
  rows: Row[];
  duration_s: number;
  truncated: boolean;
}

export interface ReplayEnv {
  fetchWindow(from: number, to: number, signal: AbortSignal): Promise<Window>;
  now(): number; // ms, monotonic
  raf(cb: () => void): number;
  cancelRaf(id: number): void;
  /** optional: without it a seek loads immediately (unit tests) */
  setTimeout?(cb: () => void, ms: number): number;
  clearTimeout?(id: number): void;
}

export interface ReplayState {
  position: number;
  duration: number;
  playing: boolean;
  rate: Rate;
  /** row at or before the playhead (null before the first sample or while loading) */
  frame: Frame | null;
  sampleIndex: number | null;
  /** the playhead is inside a stretch of the recording without samples */
  inGap: boolean;
  /** the row at the playhead is an empty game packet (shown: last real frame, dimmed) */
  empty: boolean;
  loading: boolean;
  error: string | null;
  ended: boolean;
  generation: number;
}

export function lastAtOrBefore(rows: Row[], t: number): number {
  let lo = 0;
  let hi = rows.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].el <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo - 1;
}

const toSample = (r: Row): Sample => ({
  t: r.el,
  speed: r.frame.derived.speed_kmh,
  rpm: r.frame.normalized.rpm,
  throttle: r.frame.normalized.inputs.throttle,
  brake: r.frame.normalized.inputs.brake,
  steer: r.frame.normalized.inputs.steer,
});

export class ReplayPlayer {
  readonly history = new Ring(64 * 2 * (LOOKBACK_S + AHEAD_S));
  private rows: Row[] = [];
  private loadedFrom = 0;
  private loadedTo = 0; // exclusive upper bound already requested
  private ctrl: AbortController | null = null;
  private rafId: number | null = null;
  private baseNow = 0;
  private basePos = 0;
  private listeners = new Set<() => void>();
  private state: ReplayState;
  private disposed = false;
  private seekTimer: number | null = null;

  constructor(
    readonly recordingId: string,
    duration: number,
    private env: ReplayEnv,
  ) {
    this.state = {
      position: 0,
      duration,
      playing: false,
      rate: 1,
      frame: null,
      sampleIndex: null,
      inGap: false,
      empty: false,
      loading: false,
      error: null,
      ended: false,
      generation: 0,
    };
  }

  // ------------------------------------------------------------ store API
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = (): ReplayState => this.state;
  /** chart clock: recording seconds at the playhead */
  now = (): number => this.state.position;

  private set(patch: Partial<ReplayState>): void {
    this.state = { ...this.state, ...patch };
    for (const fn of this.listeners) fn();
  }

  // ------------------------------------------------------------ controls
  seek(t: number): void {
    const pos = Math.max(0, Math.min(this.state.duration, t));
    this.ctrl?.abort();
    this.rows = [];
    this.history.clear();
    this.basePos = pos;
    this.baseNow = this.env.now();
    const generation = this.state.generation + 1;
    this.loadedFrom = Math.max(0, pos - LOOKBACK_S);
    this.loadedTo = this.loadedFrom;
    this.set({ position: pos, generation, frame: null, sampleIndex: null, inGap: false, empty: false, ended: pos >= this.state.duration, error: null, loading: true });
    const go = () => {
      this.seekTimer = null;
      void this.load(this.loadedFrom, Math.min(this.state.duration + 0.001, pos + AHEAD_S), generation);
    };
    if (this.seekTimer !== null) this.env.clearTimeout?.(this.seekTimer);
    if (this.env.setTimeout) this.seekTimer = this.env.setTimeout(go, SEEK_SETTLE_MS);
    else go();
  }

  play(): void {
    if (this.state.playing || this.disposed) return;
    let pos = this.state.position;
    if (pos >= this.state.duration) {
      this.seek(0);
      pos = 0;
    }
    this.basePos = pos;
    this.baseNow = this.env.now();
    this.set({ playing: true, ended: false });
    this.schedule();
  }

  pause(): void {
    if (!this.state.playing) return;
    this.advance();
    this.set({ playing: false });
    if (this.rafId !== null) this.env.cancelRaf(this.rafId);
    this.rafId = null;
  }

  setRate(rate: Rate): void {
    this.advance();
    this.basePos = this.state.position;
    this.baseNow = this.env.now();
    this.set({ rate });
  }

  dispose(): void {
    this.disposed = true;
    if (this.seekTimer !== null) this.env.clearTimeout?.(this.seekTimer);
    this.ctrl?.abort();
    if (this.rafId !== null) this.env.cancelRaf(this.rafId);
    this.rafId = null;
    this.listeners.clear();
  }

  // ------------------------------------------------------------ playback
  private schedule(): void {
    if (this.rafId === null && this.state.playing && !this.disposed) {
      this.rafId = this.env.raf(() => {
        this.rafId = null;
        this.tick();
      });
    }
  }

  /** exposed for tests */
  tick(): void {
    if (!this.state.playing) return;
    this.advance();
    this.schedule();
  }

  private advance(): void {
    if (!this.state.playing) return;
    let pos = this.basePos + ((this.env.now() - this.baseNow) / 1000) * this.state.rate;
    let playing = true;
    let ended = false;
    if (pos >= this.state.duration) {
      pos = this.state.duration;
      playing = false;
      ended = true;
    }
    this.set({ position: pos, playing, ended, ...this.resolve(pos) });
    this.trim(pos);
    this.prefetch(pos);
  }

  private resolve(pos: number): Pick<ReplayState, "frame" | "sampleIndex" | "inGap" | "empty"> {
    let k = lastAtOrBefore(this.rows, pos);
    // The first sample is a few ms after the recording started: at the very start show it
    // (within the gap threshold) instead of an empty screen.
    if (k < 0 && this.rows.length && this.rows[0].el - pos <= GAP_S) k = 0;
    if (k < 0) return { frame: this.state.loading ? this.state.frame : null, sampleIndex: null, inGap: !this.state.loading && this.rows.length > 0, empty: false };
    const row = this.rows[k];
    const atEnd = k === this.rows.length - 1 && this.loadedTo >= this.state.duration;
    const inGap = pos - row.el > GAP_S && !(atEnd && pos >= this.state.duration - GAP_S);
    const empty = isEmptyFrame(row.frame);
    let frame = row.frame;
    if (empty) {
      // Same rule as live: an empty game packet is not a measurement; keep the last real
      // frame of the same stream segment on screen (dimmed by the view).
      for (let j = k - 1; j >= 0 && j >= k - 4000; j--) {
        const f = this.rows[j].frame;
        if (f.stream_segment_id !== frame.stream_segment_id) break;
        if (!isEmptyFrame(f)) {
          frame = f;
          break;
        }
      }
    }
    return { frame, sampleIndex: row.i, inGap, empty };
  }

  private trim(pos: number): void {
    const keepFrom = pos - LOOKBACK_S - 2;
    let n = 0;
    while (n < this.rows.length && this.rows[n].el < keepFrom) n++;
    if (n > 0) {
      this.rows.splice(0, n);
      this.loadedFrom = Math.max(this.loadedFrom, keepFrom);
    }
  }

  private prefetch(pos: number): void {
    if (this.state.loading || this.state.error || this.seekTimer !== null) return;
    if (this.loadedTo >= this.state.duration) return;
    if (pos > this.loadedTo - PREFETCH_MARGIN_S) {
      void this.load(this.loadedTo, Math.min(this.state.duration + 0.001, this.loadedTo + AHEAD_S), this.state.generation);
    }
  }

  private async load(from: number, to: number, generation: number): Promise<void> {
    const ctrl = new AbortController();
    this.ctrl = ctrl;
    this.set({ loading: true });
    try {
      const w = await this.env.fetchWindow(from, to, ctrl.signal);
      if (generation !== this.state.generation || this.disposed) return; // a newer seek won
      const lastI = this.rows.length ? this.rows[this.rows.length - 1].i : -1;
      for (const r of w.rows) {
        if (r.i <= lastI) continue; // windows touch at their edges
        this.rows.push(r);
        this.history.push(toSample(r));
      }
      // A truncated window ends at its last row; the next request continues from there.
      this.loadedTo = w.truncated && w.rows.length ? w.rows[w.rows.length - 1].el : to;
      this.set({ loading: false, duration: Math.max(this.state.duration, w.duration_s), ...this.resolve(this.state.position) });
    } catch (e) {
      if (generation !== this.state.generation || this.disposed || (e as Error).name === "AbortError") return;
      this.set({ loading: false, error: `Could not load this part of the recording (${(e as Error).message}).` });
    }
  }

  /** retry after an error at the current position */
  retry(): void {
    this.seek(this.state.position);
  }

  /** samples currently held (for tests and the memory bound) */
  get heldRows(): number {
    return this.rows.length;
  }
}

export function httpFetchWindow(recordingId: string) {
  return async (from: number, to: number, signal: AbortSignal): Promise<Window> => {
    const url = `/api/v1/recordings/${encodeURIComponent(recordingId)}/frames?from=${from.toFixed(3)}&to=${to.toFixed(3)}`;
    const r = await fetch(url, { signal, cache: "no-store" });
    if (!r.ok) throw new Error(`server answered ${r.status}`);
    return (await r.json()) as Window;
  };
}

export function browserReplayEnv(recordingId: string): ReplayEnv {
  return {
    fetchWindow: httpFetchWindow(recordingId),
    now: () => performance.now(),
    raf: (cb) => requestAnimationFrame(cb),
    cancelRaf: (id) => cancelAnimationFrame(id),
    setTimeout: (cb, ms) => window.setTimeout(cb, ms),
    clearTimeout: (id) => window.clearTimeout(id),
  };
}

export function clockText(seconds: number, tenths = false): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  const ss = tenths ? rest.toFixed(1).padStart(4, "0") : String(Math.floor(rest)).padStart(2, "0");
  return `${m}:${ss}`;
}
