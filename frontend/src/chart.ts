// Shared chart primitives for the live trace (Traces) and the Telemetry Lab: one palette
// from the design tokens, one time-tick rule, one canvas sizing rule.

export interface Palette {
  text: string;
  text2: string;
  text3: string;
  line: string;
  speed: string;
  throttle: string;
  brake: string;
  steer: string;
  accent: string;
  danger: string;
  surface: string;
  font: string;
}

export function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    text: v("--text"),
    text2: v("--text-2"),
    text3: v("--text-3"),
    line: v("--line"),
    speed: v("--data-speed"),
    throttle: v("--data-throttle"),
    brake: v("--data-brake"),
    steer: v("--data-steer"),
    accent: v("--accent"),
    danger: v("--danger"),
    surface: v("--surface"),
    font: v("--font-ui"),
  };
}

/** "Nice" tick step for a time span (seconds), aiming at roughly `target` ticks. */
export function niceStep(span: number, target = 8): number {
  const raw = span / Math.max(1, target);
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  return steps.find((s) => s >= raw) ?? 3600;
}

export function timeTicks(t0: number, t1: number, target = 8): number[] {
  const step = niceStep(t1 - t0, target);
  const out: number[] = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1 + 1e-9; t += step) out.push(Math.round(t * 1000) / 1000);
  return out;
}

export function clockLabel(t: number, step: number): string {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  const ss = step < 1 ? s.toFixed(1).padStart(4, "0") : String(Math.round(s)).padStart(2, "0");
  return `${m}:${ss}`;
}

/** Size a canvas backing store to its CSS box (device pixels, CSS zoom aware) and return
 *  a context scaled to CSS pixels, or null when the canvas has no size yet. */
export function fitCanvas(c: HTMLCanvasElement): { ctx: CanvasRenderingContext2D; w: number; h: number } | null {
  const w = c.clientWidth;
  const h = c.clientHeight;
  if (!w || !h) return null;
  const zoomK = c.getBoundingClientRect().width / w || 1;
  const dpr = (window.devicePixelRatio || 1) * zoomK;
  const bw = Math.round(w * dpr);
  const bh = Math.round(h * dpr);
  if (c.width !== bw || c.height !== bh) {
    c.width = bw;
    c.height = bh;
  }
  const ctx = c.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

/** Value-axis range with a little headroom; keeps 0 visible when it is near. */
export function niceRange(min: number | null, max: number | null): [number, number] {
  if (min === null || max === null) return [0, 1];
  if (min === max) return [min - 1, max + 1];
  const pad = (max - min) * 0.06;
  let lo = min - pad;
  let hi = max + pad;
  if (min >= 0 && (lo < 0 || lo < (hi - lo) * 0.25)) lo = 0; // non-negative data: no negative axis
  if (max <= 0 && (hi > 0 || -hi < (hi - lo) * 0.25)) hi = 0;
  return [lo, hi];
}
