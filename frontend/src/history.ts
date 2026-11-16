// Fixed-size ring buffer for the short live traces. Samples are keyed by client
// arrival time (performance.now seconds). A gap larger than GAP_S breaks the line
// instead of drawing an invented continuous segment.

export const GAP_S = 0.3;

export interface Sample {
  t: number;
  speed: number | null;
  rpm: number | null;
  throttle: number | null;
  brake: number | null;
  steer: number | null;
}

export class Ring {
  private buf: (Sample | undefined)[];
  private head = 0;
  private size = 0;
  /** increments on every change; lets the chart skip redraws when nothing is new */
  version = 0;
  /** highest finite speed since the last clear (drives the expand-only speed scale) */
  peakSpeed = 0;

  constructor(readonly capacity: number) {
    this.buf = new Array(capacity);
  }
  push(s: Sample): void {
    this.buf[this.head] = s;
    this.head = (this.head + 1) % this.capacity;
    this.size = Math.min(this.size + 1, this.capacity);
    if (s.speed !== null && Number.isFinite(s.speed) && s.speed > this.peakSpeed) this.peakSpeed = s.speed;
    this.version++;
  }
  clear(): void {
    this.head = 0;
    this.size = 0;
    this.peakSpeed = 0;
    this.version++;
  }
  get length(): number {
    return this.size;
  }
  private at(i: number): Sample {
    return this.buf[(this.head - this.size + i + this.capacity * 2) % this.capacity]!;
  }
  /** Oldest to newest, only samples with t >= since. Times are non-decreasing, so the
   *  start is found by binary search: cost is O(window), not O(capacity). */
  *since(since: number): Generator<Sample> {
    let lo = 0;
    let hi = this.size;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.at(mid).t < since) lo = mid + 1;
      else hi = mid;
    }
    for (let i = lo; i < this.size; i++) yield this.at(i);
  }
  last(): Sample | null {
    return this.size ? this.at(this.size - 1) : null;
  }
  /** Copy of the samples in [since, until] — used to freeze the chart so later
   *  overwrites of the ring cannot remove what the user is inspecting. */
  snapshot(since: number, until = Infinity): Sample[] {
    const out: Sample[] = [];
    for (const s of this.since(since)) if (s.t <= until) out.push(s);
    return out;
  }
}

/** Split a channel into drawable runs; null values and time gaps end a run. */
export function segments(samples: Iterable<Sample>, pick: (s: Sample) => number | null): { t: number; v: number }[][] {
  const out: { t: number; v: number }[][] = [];
  let cur: { t: number; v: number }[] = [];
  let lastT = -Infinity;
  for (const s of samples) {
    const v = pick(s);
    if (v === null || !Number.isFinite(v) || s.t - lastT > GAP_S) {
      if (cur.length) out.push(cur);
      cur = [];
    }
    if (v !== null && Number.isFinite(v)) cur.push({ t: s.t, v });
    lastT = s.t;
  }
  if (cur.length) out.push(cur);
  return out;
}

/** Index of the sample closest to t in a time-sorted array, or -1. */
export function nearestIndex(samples: Sample[], t: number): number {
  if (!samples.length) return -1;
  let lo = 0;
  let hi = samples.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].t < t) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(samples[lo - 1].t - t) <= Math.abs(samples[lo].t - t)) lo--;
  return lo;
}

/** Sample at t, or null when the closest one is further than GAP_S away (a data gap:
 *  the chart says "no data" instead of borrowing a value from another moment). */
export function sampleAt(samples: Sample[], t: number): Sample | null {
  const i = nearestIndex(samples, t);
  if (i < 0) return null;
  return Math.abs(samples[i].t - t) <= GAP_S / 2 ? samples[i] : null;
}

export type SpeedScale = "auto" | 200 | 300 | 400;

/** Speed axis maximum. "auto" only ever expands (in 50 km/h steps, minimum 100) until
 *  the history is cleared, so a slowing car never looks like it is accelerating
 *  because the axis shrank underneath it. */
export function speedAxisMax(peak: number, scale: SpeedScale): number {
  if (scale !== "auto") return scale;
  return Math.max(100, Math.ceil(peak / 50) * 50);
}
