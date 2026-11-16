// Live performance run: the user arms one test, the server measures it on the live
// stream (same detector as for recordings) and the result appears here.
import { useState } from "react";
import { fmt } from "../format";
import type { PerfStatus } from "../types";

export const RUN_KINDS: { id: string; label: string; hint: string }[] = [
  { id: "accel_0_100", label: "0–100 km/h", hint: "Stop the car. The timer starts when you move (1 km/h) and stops at 100 km/h." },
  { id: "accel_100_200", label: "100–200 km/h", hint: "Drop below 98 km/h. The timer runs from 100 to 200 km/h; using the brake or lifting cancels it." },
  { id: "brake_100_0", label: "100–0 km/h braking", hint: "Get above 105 km/h, then brake (at least 30 %). Timed from 100 km/h to a stop; touching the throttle cancels it." },
];

const STATE_TEXT: Record<string, string> = {
  armed: "Armed: get into position.",
  ready: "Ready: go when you want.",
  measuring: "Measuring…",
  completed: "Done.",
  invalid: "Not valid.",
  idle: "",
};

async function post(path: string, body: object = {}): Promise<Response> {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export function PerfRun({ perf }: { perf: PerfStatus | null | undefined }) {
  const [kind, setKind] = useState(RUN_KINDS[0].id);
  const [err, setErr] = useState<string | null>(null);
  if (!perf) return null;
  const active = perf.state !== "idle";
  const finished = perf.state === "completed" || perf.state === "invalid";
  const arm = async (k: string) => {
    setErr(null);
    const r = await post("/api/v1/perf/arm", { kind: k }).catch(() => null);
    if (!r?.ok) setErr(r?.status === 403 ? "This device may only view; arm the run on the Pitwall PC." : "Could not arm the run.");
  };
  const disarm = async () => {
    setErr(null);
    await post("/api/v1/perf/disarm").catch(() => null);
  };
  const last = perf.last;
  const hint = RUN_KINDS.find((k) => k.id === (perf.kind ?? kind))?.hint;
  return (
    <div className="perf-run">
      <h2 className="panel-title timing-title">Performance run</h2>
      {!active || finished ? (
        <div className="row-actions">
          <label className="inline-field">
            <select value={perf.kind && finished ? perf.kind : kind} onChange={(e) => setKind(e.target.value)} aria-label="Run type">
              {RUN_KINDS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.label}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="btn btn-small" onClick={() => void arm(perf.kind && finished ? perf.kind : kind)}>
            {finished ? "Again" : "Arm"}
          </button>
          {finished && (
            <button type="button" className="btn btn-small btn-quiet" onClick={() => void disarm()}>
              Done
            </button>
          )}
        </div>
      ) : (
        <div className="row-actions">
          <span className="perf-state" role="status">
            <strong>{perf.label}</strong> · {STATE_TEXT[perf.state]}
            {perf.state === "measuring" && perf.elapsed_s !== null && <span className="num perf-clock"> {fmt(perf.elapsed_s, 2)} s</span>}
          </span>
          <button type="button" className="btn btn-small btn-quiet" onClick={() => void disarm()}>
            Cancel
          </button>
        </div>
      )}
      {active && !finished && hint && <p className="muted small">{hint}</p>}
      {finished && last && (
        <p className={`perf-result${last.valid ? "" : " is-invalid"}`} role="status">
          {last.valid ? (
            <>
              <span className="num perf-time">{fmt(last.time_s, 2)} s</span> {last.label} · {fmt(last.distance_m, 1)} m
            </>
          ) : (
            <>
              {last.label}: not valid — {last.reason}
            </>
          )}
        </p>
      )}
      {err && (
        <p className="text-danger small" role="alert">
          {err}
        </p>
      )}
      <p className="muted small">Measured by the Pitwall server from the data the game sends (crossings interpolated between samples, ±0.02 s). Record the drive to keep the result.</p>
    </div>
  );
}
