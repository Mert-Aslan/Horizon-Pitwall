// Car names from the local catalog (Garage). Shared by the top bar, Sessions, Runs and
// the Garage itself; any edit dispatches CARS_CHANGED so every view refreshes.
import { useEffect, useState } from "react";

export interface CarName {
  ordinal: number;
  name: string;
  year: number | null;
  variant: string | null;
  notes: string | null;
  source: string;
  source_version: string | null;
  created_utc: string;
  updated_utc: string;
}

export interface SeenCar {
  ordinal: number;
  recordings: number;
  samples: number;
  last_seen_utc: string | null;
  car_labels: string[];
}

export const CARS_CHANGED = "pitwall:cars-changed";

export function useCars(enabled: boolean): { names: Map<number, CarName>; seen: SeenCar[]; loaded: boolean; failed: boolean } {
  const [state, setState] = useState<{ names: Map<number, CarName>; seen: SeenCar[]; loaded: boolean; failed: boolean }>({
    names: new Map(),
    seen: [],
    loaded: false,
    failed: false,
  });
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const load = () =>
      fetch("/api/v1/cars", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: { catalog: CarName[]; seen: SeenCar[] }) => {
          if (alive) setState({ names: new Map(d.catalog.map((c) => [c.ordinal, c])), seen: d.seen, loaded: true, failed: false });
        })
        .catch(() => alive && setState((s) => ({ ...s, loaded: true, failed: true })));
    void load();
    window.addEventListener(CARS_CHANGED, load);
    return () => {
      alive = false;
      window.removeEventListener(CARS_CHANGED, load);
    };
  }, [enabled]);
  return state;
}

export function carDisplayName(c: CarName): string {
  return [c.year, c.name, c.variant ? `(${c.variant})` : null].filter(Boolean).join(" ");
}

export function provenance(c: CarName): string {
  return c.source === "user" ? "your name" : `imported from ${c.source.replace(/^import:/, "")}${c.source_version ? ` (${c.source_version})` : ""}`;
}
