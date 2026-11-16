// Display-only formatting. Stored/streamed values are never rounded; only these
// helpers turn them into text. Missing values render as an em dash, never as 0.

import type { N } from "./types";

export const DASH = "—";

export function isNum(v: N | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function fmt(v: N | undefined, digits = 0): string {
  if (!isNum(v)) return DASH;
  const r = v.toFixed(digits);
  // avoid "-0"
  return /^-0(\.0+)?$/.test(r) ? r.slice(1) : r;
}

export function fmtPct(fraction: N | undefined, digits = 0): string {
  return isNum(fraction) ? `${fmt(fraction * 100, digits)}%` : DASH;
}

export function fmtLap(seconds: N | undefined): string {
  if (!isNum(seconds)) return DASH;
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(3).padStart(6, "0")}`;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** Gear text: forward gears by number; 0 shows the raw code (R/N encoding unverified). */
export function gearText(raw: N | undefined, label: string | null | undefined, note?: string | null): { main: string; note: string | null } {
  if (label) return { main: label, note: note ?? null };
  if (!isNum(raw)) return { main: DASH, note: null };
  if (raw === 0) return { main: "0", note: "R/N code, unverified" };
  return { main: String(raw), note: "unrecognised gear code" };
}

export function ageText(ms: N | undefined): string {
  if (!isNum(ms)) return DASH;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const m = Math.floor(ms / 60_000);
  return `${m} min ${Math.round((ms % 60_000) / 1000)} s`;
}

/** Steering bar geometry: centre-zero, -1 (left) .. +1 (right). */
export function steerBar(steer: N | undefined): { left: number; width: number } {
  if (!isNum(steer)) return { left: 50, width: 0 };
  const s = clamp(steer, -1, 1);
  return s < 0 ? { left: 50 + s * 50, width: -s * 50 } : { left: 50, width: s * 50 };
}

export const SLIP_ON = 1.0; // documented: |slip| > 1 means loss of grip
export const SLIP_OFF = 0.9; // hysteresis so the warning does not flicker around the limit

/** Next state of a wheel's loss-of-grip warning (latched with hysteresis). */
export function gripLatch(prev: boolean, slip: N | undefined): boolean {
  if (!isNum(slip)) return false;
  const a = Math.abs(slip);
  if (a > SLIP_ON) return true;
  if (a < SLIP_OFF) return false;
  return prev;
}
