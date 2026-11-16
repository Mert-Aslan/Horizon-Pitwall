import { describe, expect, it } from "vitest";
import { DASH, ageText, fmt, fmtLap, fmtPct, gearText, steerBar } from "./format";
import { Ring, segments } from "./history";
import { deriveView } from "./viewstate";
import type { Snapshot } from "./stream";
import type { Status } from "./types";

// stream.ts touches DOM globals only inside methods; import the pure helper lazily.
const { backoffMs } = await import("./stream");

describe("format", () => {
  it("renders missing values as a dash, never zero", () => {
    expect(fmt(null)).toBe(DASH);
    expect(fmt(Number.NaN)).toBe(DASH);
    expect(fmtPct(null)).toBe(DASH);
    expect(fmt(0)).toBe("0");
  });
  it("does not print negative zero", () => {
    expect(fmt(-0.0001, 1)).toBe("0.0");
  });
  it("formats laps", () => {
    expect(fmtLap(83.456)).toBe("1:23.456");
    expect(fmtLap(null)).toBe(DASH);
  });
  it("gear 0 is a raw code, not R or N", () => {
    expect(gearText(0, null)).toEqual({ main: "0", note: "R/N code, unverified" });
    expect(gearText(4, "4").main).toBe("4");
  });
  it("age text", () => {
    expect(ageText(250)).toBe("250 ms");
    expect(ageText(3200)).toBe("3.2 s");
  });
});

describe("steering bar is centre-zero and bidirectional", () => {
  it("left", () => expect(steerBar(-0.5)).toEqual({ left: 25, width: 25 }));
  it("right", () => expect(steerBar(1)).toEqual({ left: 50, width: 50 }));
  it("clamps out-of-range", () => expect(steerBar(-1.2)).toEqual({ left: 0, width: 50 }));
  it("missing", () => expect(steerBar(null)).toEqual({ left: 50, width: 0 }));
});

describe("history ring", () => {
  const s = (t: number, v: number | null) => ({ t, speed: v, rpm: null, throttle: null, brake: null, steer: null });
  it("is bounded and ordered", () => {
    const r = new Ring(3);
    [1, 2, 3, 4, 5].forEach((t) => r.push(s(t, t)));
    expect([...r.since(0)].map((x) => x.t)).toEqual([3, 4, 5]);
  });
  it("breaks lines at gaps and nulls", () => {
    const runs = segments([s(0, 1), s(0.016, 2), s(1.0, 3), s(1.016, null), s(1.032, 4)], (x) => x.speed);
    expect(runs.map((r) => r.map((p) => p.v))).toEqual([[1, 2], [3], [4]]);
  });
});

describe("reconnect backoff", () => {
  it("grows, is capped and jittered", () => {
    expect(backoffMs(0, () => 1)).toBe(500);
    expect(backoffMs(0, () => 0)).toBe(250);
    expect(backoffMs(20, () => 1)).toBe(10_000);
  });
});

describe("view state", () => {
  const status = (over: Partial<Status>): Status => ({
    connection_state: "receiving",
    packet_age_ms: 10,
    traffic_warning: null,
    last_rejected_reason: null,
    observed_hz: 60,
    mode: "live",
    receiver_state: "listening",
    receiver_error: null,
    receiver_address: "127.0.0.1:5607",
    session_id: "s",
    source: "x",
    sequence: 1,
    ...over,
  });
  const snap = (over: Partial<Snapshot>): Snapshot => ({
    link: "open",
    hello: null,
    status: status({}),
    frame: null,
    raw: null,
    lastMessageAt: 0,
    retryAt: null,
    versionMismatch: false,
    uiBuildMismatch: false,
    serverRestarts: 0,
    protocolError: null,
    badMessages: 0,
    ...over,
  });
  it("server link loss overrides the last server status", () => {
    expect(deriveView(snap({ link: "closed" })).kind).toBe("server_offline");
  });
  it("stale dims", () => {
    const v = deriveView(snap({ status: status({ connection_state: "stale" }) }));
    expect(v.kind).toBe("stale");
    expect(v.dim).toBe(true);
  });
  it("demo is flagged", () => {
    expect(deriveView(snap({ status: status({ mode: "demo" }) })).demo).toBe(true);
  });
});

describe("chart model", async () => {
  const { nearestIndex, sampleAt, speedAxisMax, Ring } = await import("./history");
  const s = (t: number, speed: number | null = 1) => ({ t, speed, rpm: null, throttle: null, brake: null, steer: null });
  it("finds the nearest sample", () => {
    const xs = [s(0), s(0.1), s(0.2), s(0.5)];
    expect(nearestIndex(xs, 0.12)).toBe(1);
    expect(nearestIndex(xs, 0.36)).toBe(3);
    expect(nearestIndex([], 1)).toBe(-1);
  });
  it("reports no data inside a gap instead of borrowing a value", () => {
    const xs = [s(0), s(0.1), s(2.0)];
    expect(sampleAt(xs, 0.11)?.t).toBe(0.1);
    expect(sampleAt(xs, 1.0)).toBeNull();
  });
  it("auto speed scale only expands and has a floor", () => {
    expect(speedAxisMax(0, "auto")).toBe(100);
    expect(speedAxisMax(162, "auto")).toBe(200);
    expect(speedAxisMax(201, "auto")).toBe(250);
    expect(speedAxisMax(162, 300)).toBe(300);
  });
  it("ring: binary-search window, snapshot copy, version and peak", () => {
    const r = new Ring(4);
    [1, 2, 3, 4, 5, 6].forEach((t) => r.push(s(t, t * 10)));
    expect([...r.since(4.5)].map((x) => x.t)).toEqual([5, 6]);
    expect([...r.since(0)].map((x) => x.t)).toEqual([3, 4, 5, 6]);
    const snap = r.snapshot(4, 5);
    r.push(s(7));
    r.push(s(8));
    expect(snap.map((x) => x.t)).toEqual([4, 5]); // frozen copy survives overwrites
    expect(r.peakSpeed).toBe(60);
    const v = r.version;
    r.clear();
    expect(r.version).toBe(v + 1);
    expect(r.peakSpeed).toBe(0);
    expect(r.last()).toBeNull();
  });
});

describe("grip warning hysteresis", async () => {
  const { gripLatch } = await import("./format");
  it("turns on above 1.0, holds between 0.9 and 1.0, turns off below 0.9", () => {
    expect(gripLatch(false, 1.05)).toBe(true);
    expect(gripLatch(true, 0.95)).toBe(true);
    expect(gripLatch(false, 0.95)).toBe(false);
    expect(gripLatch(true, 0.85)).toBe(false);
    expect(gripLatch(true, -1.2)).toBe(true);
    expect(gripLatch(true, null)).toBe(false);
  });
});
