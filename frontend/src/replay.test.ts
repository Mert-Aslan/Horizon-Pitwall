// ReplayPlayer: seek accuracy, generation safety, 2x order, gaps, bounded memory.
import { describe, expect, it } from "vitest";
import { ReplayPlayer, lastAtOrBefore, type ReplayEnv, type Row, type Window } from "./replay";
import type { Frame } from "./types";

function frame(i: number, _el: number, speed: number | null, opts: { empty?: boolean; seg?: string } = {}): Frame {
  return {
    sequence: i + 1,
    sample_index: i,
    stream_segment_id: opts.seg ?? "s0",
    session_id: opts.seg ?? "s0",
    game_timestamp_ms: 1000 + i * 16,
    normalized: { rpm: 5000, inputs: { throttle: 1, brake: 0, steer: 0 }, car: { ordinal: 7 } },
    derived: { speed_kmh: speed },
    quality: { fields: {}, invalid_field_count: 0, flags: opts.empty ? ["empty_packet"] : [], tail_hex: "00", packet_length: 324 },
  } as unknown as Frame;
}

/** Recording: 60 Hz for 0..30 s, then a gap 30..35 s, then 35..60 s. Speed = el*10. */
function makeRecording(): Row[] {
  const rows: Row[] = [];
  let i = 0;
  for (let t = 0; t < 60; t += 1 / 60) {
    if (t >= 30 && t < 35) continue;
    const el = Math.round(t * 1e4) / 1e4;
    rows.push({ i, el, frame: frame(i, el, el * 10) });
    i++;
  }
  return rows;
}

class Env implements ReplayEnv {
  t = 0;
  rafs = new Map<number, () => void>();
  id = 0;
  setTimeout?: (cb: () => void, ms: number) => number;
  clearTimeout?: (id: number) => void;
  pending: { from: number; to: number; resolve: (w: Window) => void; reject: (e: Error) => void }[] = [];
  requested: [number, number][] = [];
  constructor(
    public rows: Row[],
    public auto = true,
  ) {}
  fetchWindow(from: number, to: number, signal: AbortSignal): Promise<Window> {
    this.requested.push([from, to]);
    const make = (): Window => ({ rows: this.rows.filter((r) => r.el >= from && r.el <= to), duration_s: 60, truncated: false });
    if (this.auto) return Promise.resolve(make());
    return new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      this.pending.push({ from, to, resolve: () => resolve(make()), reject });
    });
  }
  now() {
    return this.t;
  }
  raf(cb: () => void) {
    this.rafs.set(++this.id, cb);
    return this.id;
  }
  cancelRaf(id: number) {
    this.rafs.delete(id);
  }
  /** advance wall time in display frames (16 ms), running rAF callbacks */
  run(ms: number) {
    const end = this.t + ms;
    while (this.t < end) {
      this.t += 16;
      for (const [id, cb] of [...this.rafs]) {
        this.rafs.delete(id);
        cb();
      }
    }
  }
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("replay player", () => {
  it("seek shows the recorded value at that moment", async () => {
    const env = new Env(makeRecording());
    const p = new ReplayPlayer("r", 60, env);
    p.seek(12.5);
    await flush();
    const s = p.getSnapshot();
    expect(s.frame?.derived.speed_kmh).toBeCloseTo(125, 0);
    expect(s.sampleIndex).toBe(750); // 12.5 s * 60 Hz
    expect(env.requested[0]).toEqual([0, 24.5]); // history for the trace (12.5 - 22, clamped at 0) + 12 s ahead
    expect([...p.history.since(-1)].length).toBeGreaterThan(700);
  });

  it("an older window never overwrites a newer seek", async () => {
    const env = new Env(makeRecording(), false);
    const p = new ReplayPlayer("r", 60, env);
    p.seek(10);
    const first = env.pending[0];
    p.seek(50);
    const second = env.pending[1];
    second.resolve(undefined as never);
    await flush();
    first.resolve(undefined as never); // late answer for the old position
    await flush();
    expect(p.getSnapshot().frame?.derived.speed_kmh).toBeCloseTo(500, 0);
    expect(p.getSnapshot().generation).toBe(2);
  });

  it("plays at 2x in sample order and ends at the end", async () => {
    const env = new Env(makeRecording());
    const p = new ReplayPlayer("r", 60, env);
    p.seek(40);
    await flush();
    p.setRate(2);
    p.play();
    const t0 = env.t;
    const seen: number[] = [];
    for (let k = 0; k < 60; k++) {
      env.run(100);
      await flush();
      const i = p.getSnapshot().sampleIndex;
      if (i !== null) seen.push(i);
    }
    expect(seen.every((v, k) => k === 0 || v >= seen[k - 1])).toBe(true); // never goes back
    expect(p.getSnapshot().position).toBeCloseTo(40 + ((env.t - t0) / 1000) * 2, 1); // wall time x 2
    env.run(20_000);
    await flush();
    expect(p.getSnapshot().ended).toBe(true);
    expect(p.getSnapshot().playing).toBe(false);
  });

  it("a gap in the recording is reported, not bridged", async () => {
    const env = new Env(makeRecording());
    const p = new ReplayPlayer("r", 60, env);
    p.seek(32);
    await flush();
    const s = p.getSnapshot();
    expect(s.inGap).toBe(true);
    p.seek(36);
    await flush();
    expect(p.getSnapshot().inGap).toBe(false);
  });

  it("holds a bounded number of samples during long playback", async () => {
    const env = new Env(makeRecording());
    const p = new ReplayPlayer("r", 60, env);
    p.seek(0);
    await flush();
    p.setRate(2);
    p.play();
    let peak = 0;
    for (let k = 0; k < 300; k++) {
      env.run(100);
      await flush();
      peak = Math.max(peak, p.heldRows);
    }
    expect(peak).toBeLessThan(60 * (22 + 12 + 2 + 12) + 10);
  });

  it("empty game packets keep the last real frame and are flagged", async () => {
    const rows: Row[] = [0, 1, 2, 3].map((i) => ({ i, el: i * 0.016, frame: frame(i, i * 0.016, i < 2 ? 100 : null, { empty: i >= 2 }) }));
    const env = new Env(rows);
    const p = new ReplayPlayer("r", 0.1, env);
    p.seek(0.05);
    await flush();
    const s = p.getSnapshot();
    expect(s.empty).toBe(true);
    expect(s.sampleIndex).toBe(3);
    expect(s.frame?.derived.speed_kmh).toBe(100);
  });

  it("errors are shown and retry works", async () => {
    const env = new Env(makeRecording());
    let fail = true;
    const orig = env.fetchWindow.bind(env);
    env.fetchWindow = (a, b, sig) => (fail ? Promise.reject(new Error("server answered 500")) : orig(a, b, sig));
    const p = new ReplayPlayer("r", 60, env);
    p.seek(5);
    await flush();
    expect(p.getSnapshot().error).toMatch(/500/);
    fail = false;
    p.retry();
    await flush();
    expect(p.getSnapshot().error).toBeNull();
    expect(p.getSnapshot().frame).not.toBeNull();
  });

  it("the very start shows the first sample when it is a few ms in", async () => {
    const rows: Row[] = [0.012, 0.028].map((el, i) => ({ i, el, frame: frame(i, el, 50 + i) }));
    const p = new ReplayPlayer("r", 1, new Env(rows));
    p.seek(0);
    await flush();
    expect(p.getSnapshot().sampleIndex).toBe(0);
    expect(p.getSnapshot().inGap).toBe(false);
  });

  it("rapid seeks load only the settled position", async () => {
    const env = new Env(makeRecording());
    const timers = new Map<number, () => void>();
    let tid = 0;
    env.setTimeout = (cb: () => void) => (timers.set(++tid, cb), tid);
    env.clearTimeout = (id: number) => void timers.delete(id);
    const p = new ReplayPlayer("r", 60, env);
    for (let t = 1; t <= 20; t++) p.seek(t);
    expect(env.requested.length).toBe(0);
    expect(p.getSnapshot().position).toBe(20); // the playhead moves at once
    [...timers.values()].forEach((cb) => cb());
    await flush();
    expect(env.requested.length).toBe(1);
    expect(p.getSnapshot().frame?.derived.speed_kmh).toBeCloseTo(200, 0);
  });

  it("binary search picks the row at or before t", () => {
    const rows = [0, 1, 2].map((i) => ({ i, el: i, frame: frame(i, i, 0) }));
    expect(lastAtOrBefore(rows, -0.1)).toBe(-1);
    expect(lastAtOrBefore(rows, 1)).toBe(1);
    expect(lastAtOrBefore(rows, 1.9)).toBe(1);
    expect(lastAtOrBefore(rows, 5)).toBe(2);
  });
});
