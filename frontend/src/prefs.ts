// Per-viewer UI preferences in localStorage. Storage can be unavailable (private mode,
// blocked site data); every access falls back to the default.
import { useState } from "react";

export function readPref<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(`pitwall.${key}`);
    return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(`pitwall.${key}`, value);
  } catch {
    /* preference is a convenience only */
  }
}

export function usePref<T extends string>(key: string, allowed: readonly T[], fallback: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => readPref(key, allowed, fallback));
  return [
    v,
    (next: T) => {
      setV(next);
      writePref(key, next);
    },
  ];
}
