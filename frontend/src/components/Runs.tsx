// Performance runs found in all saved recordings, and a time-aligned comparison of two.
import { useEffect, useMemo, useRef, useState } from "react";
import { fitCanvas, niceStep, readPalette, timeTicks } from "../chart";
import { fmt } from "../format";
import { carDisplayName, useCars, type CarName } from "../cars";
import type { RunRow } from "../types";
import { RUN_KINDS } from "./PerfRun";

interface Trace {
  run: RunRow;
  t: number[];
  speed: (number | null)[];
  throttle: (number | null)[];
  brake: (number | null)[];
}

const key = (r: RunRow) => `${r.recording_id}#${r.run_index}`;
const when = (utc: string) => {
  const d = new Date(utc);
  return Number.isNaN(d.getTime()) ? utc : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
};
const carText = (r: RunRow, names: Map<number, CarName>) => {
  const n = r.car_ordinal !== null ? names.get(r.car_ordinal) : undefined;
  return r.car_label || (n ? carDisplayName(n) : r.car_ordinal !== null ? `Car #${r.car_ordinal}` : "unknown car");
};

function Overlay({ a, b }: { a: Trace; b: Trace }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const pal = useMemo(() => readPalette(), []);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const draw = () => {
      const fit = fitCanvas(c);
      if (!fit) return;
      const { ctx, w, h } = fit;
      const L = 44;
      const B = 22;
      const t0 = Math.min(a.t[0] ?? 0, b.t[0] ?? 0);
      const t1 = Math.max(a.t[a.t.length - 1] ?? 1, b.t[b.t.length - 1] ?? 1);
      const vmax = Math.max(100, ...[...a.speed, ...b.speed].map((v) => v ?? 0));
      const top = Math.ceil(vmax / 50) * 50;
      const x = (t: number) => L + ((t - t0) / (t1 - t0)) * (w - L - 8);
      const y = (v: number) => h - B - (v / top) * (h - B - 8);
      ctx.font = `11px ${pal.font}`;
      ctx.strokeStyle = pal.line;
      ctx.fillStyle = pal.text3;
      for (const v of [0, top / 2, top]) {
        ctx.beginPath();
        ctx.moveTo(L, Math.round(y(v)) + 0.5);
        ctx.lineTo(w - 8, Math.round(y(v)) + 0.5);
        ctx.stroke();
        ctx.fillText(`${v}`, 4, y(v) + 4);
      }
      const step = niceStep(t1 - t0, 6);
      ctx.textAlign = "center";
      for (const t of timeTicks(t0, t1, 6)) ctx.fillText(t === 0 ? "start" : `${t > 0 ? "+" : "−"}${fmt(Math.abs(t), step < 1 ? 1 : 0)} s`, x(t), h - 6);
      ctx.strokeStyle = pal.text3;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x(0), 8);
      ctx.lineTo(x(0), h - B);
      ctx.stroke();
      ctx.setLineDash([]);
      const line = (tr: Trace, color: string) => {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        let pen = false;
        tr.t.forEach((t, i) => {
          const v = tr.speed[i];
          if (v === null || (i > 0 && t - tr.t[i - 1] > 0.25)) {
            pen = false;
            if (v === null) return;
          }
          if (pen) ctx.lineTo(x(t), y(v));
          else ctx.moveTo(x(t), y(v));
          pen = true;
        });
        ctx.stroke();
        if (tr.run.valid && tr.run.time_s !== null) {
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(x(tr.run.time_s), y(tr.run.kind === "brake_100_0" ? 1 : tr.run.kind === "accel_0_100" ? 100 : 200), 4, 0, Math.PI * 2);
          ctx.fill();
        }
      };
      line(a, pal.speed);
      line(b, pal.steer);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [a, b, pal]);
  return (
    <canvas
      ref={ref}
      className="runs-overlay"
      role="img"
      aria-label={`Speed against time since the start of each run. A: ${fmt(a.run.time_s, 2)} s, B: ${fmt(b.run.time_s, 2)} s`}
    />
  );
}

function Compare({ a, b, names }: { a: RunRow; b: RunRow; names: Map<number, CarName> }) {
  const [ta, setTa] = useState<Trace | null>(null);
  const [tb, setTb] = useState<Trace | null>(null);
  useEffect(() => {
    let alive = true;
    const get = (r: RunRow) => fetch(`/api/v1/recordings/${encodeURIComponent(r.recording_id)}/runs/${r.run_index}/trace`).then((x) => (x.ok ? x.json() : null));
    void Promise.all([get(a), get(b)]).then(([x, y]) => {
      if (!alive) return;
      setTa(x);
      setTb(y);
    });
    return () => {
      alive = false;
    };
  }, [a, b]);
  const warnings: string[] = [];
  if (a.car_ordinal !== b.car_ordinal) warnings.push("Different cars (different CarOrdinal).");
  else if (a.pi !== b.pi) warnings.push(`Performance index differs (${fmt(a.pi)} vs ${fmt(b.pi)}): upgrades or tuning changed.`);
  if (a.mode !== b.mode) warnings.push("One run is from demo data.");
  if (!a.setup_label && !b.setup_label) warnings.push("No setup labels: add them in Sessions so you know what changed. The game does not send tuning settings.");
  warnings.push("Road, surface, weather and slope are not in the data; runs from different places may not be comparable.");
  const dt = a.time_s !== null && b.time_s !== null ? b.time_s - a.time_s : null;
  const dd = a.distance_m !== null && b.distance_m !== null ? b.distance_m - a.distance_m : null;
  const rows: [string, string, string][] = [
    ["Time", `${fmt(a.time_s, 2)} s`, `${fmt(b.time_s, 2)} s`],
    ["Distance", `${fmt(a.distance_m, 1)} m`, `${fmt(b.distance_m, 1)} m`],
    ["Car", carText(a, names), carText(b, names)],
    ["Setup (your label)", a.setup_label || "—", b.setup_label || "—"],
    ["PI", fmt(a.pi), fmt(b.pi)],
    ["Recorded", when(a.started_utc), when(b.started_utc)],
    ["Largest sample gap", `${fmt(a.max_dt_s * 1000)} ms`, `${fmt(b.max_dt_s * 1000)} ms`],
  ];
  return (
    <section className="panel runs-compare" aria-labelledby="cmp-title">
      <h3 id="cmp-title" className="panel-title">
        Comparison · {a.label}
      </h3>
      <p className="runs-delta num" role="status">
        {dt === null ? "—" : `B is ${dt === 0 ? "equal" : `${fmt(Math.abs(dt), 2)} s ${dt < 0 ? "faster" : "slower"}`}`}
        {dd !== null && <span className="muted"> · {fmt(Math.abs(dd), 1)} m {dd < 0 ? "shorter" : "longer"}</span>}
      </p>
      {ta && tb ? <Overlay a={ta} b={tb} /> : <p className="muted small">Loading the runs…</p>}
      <p className="small">
        <span className="legend-a">━ A</span> <span className="legend-b">━ B</span> <span className="muted">speed (km/h) against time from each run's start; dot = finish</span>
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col"></th>
            <th scope="col">A</th>
            <th scope="col">B</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, x, y]) => (
            <tr key={k}>
              <th scope="row">{k}</th>
              <td className="num">{x}</td>
              <td className="num">{y}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="runs-warnings small">
        {warnings.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
    </section>
  );
}

export function Runs() {
  const { names } = useCars(true);
  const [rows, setRows] = useState<RunRow[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [kind, setKind] = useState(RUN_KINDS[0].id);
  const [validOnly, setValidOnly] = useState(true);
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    fetch("/api/v1/runs", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => alive && setRows(d.runs))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);
  if (rows === null) return <p className="muted pad">{failed ? "Runs could not be loaded." : "Looking for runs in your recordings (first time may take a moment)…"}</p>;
  const shown = rows.filter((r) => r.kind === kind && (!validOnly || r.valid));
  const best = shown.filter((r) => r.valid && r.time_s !== null).reduce<RunRow | null>((m, r) => (m === null || r.time_s! < m.time_s! ? r : m), null);
  const pick = (r: RunRow) => {
    const k = key(r);
    setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k].slice(-2)));
  };
  const chosen = picked.map((k) => rows.find((r) => key(r) === k)).filter((r): r is RunRow => !!r && r.kind === kind);
  return (
    <div className="runs">
      <section className="panel" aria-labelledby="runs-title">
        <div className="panel-head">
          <h2 id="runs-title" className="panel-title">
            Runs found in your recordings
          </h2>
          <div className="row-actions">
            <label className="inline-field">
              <span>Type</span>
              <select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  setPicked([]);
                }}
              >
                {RUN_KINDS.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.label} ({rows.filter((r) => r.kind === k.id && r.valid).length})
                  </option>
                ))}
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={validOnly} onChange={(e) => setValidOnly(e.target.checked)} /> Valid only
            </label>
          </div>
        </div>
        <p className="muted small">
          Found automatically in every recording, so many are simply part of normal driving. A run is valid only with reliable samples at both speed crossings
          and no gap, empty packet, jump, rewind, reversing or car change in between. Pick two to compare.
        </p>
        {shown.length === 0 ? (
          <p className="muted pad">No {validOnly ? "valid " : ""}runs of this type yet. Arm one in Live → Performance run while recording, or drive one and record it.</p>
        ) : (
          <div className="table-wrap">
            <table className="runs-table">
              <thead>
                <tr>
                  <th scope="col">Compare</th>
                  <th scope="col">Time</th>
                  <th scope="col">Distance</th>
                  <th scope="col">Car</th>
                  <th scope="col">Setup</th>
                  <th scope="col">Recording</th>
                  <th scope="col">At</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={key(r)} className={best && key(best) === key(r) ? "is-best" : undefined}>
                    <td>
                      <input type="checkbox" aria-label={`Compare run at ${fmt(r.t_start, 1)} s in ${r.recording_title || when(r.started_utc)}`} checked={picked.includes(key(r))} onChange={() => pick(r)} />
                    </td>
                    <td className="num">{r.valid ? `${fmt(r.time_s, 2)} s` : "—"}</td>
                    <td className="num">{r.valid ? `${fmt(r.distance_m, 1)} m` : "—"}</td>
                    <td>
                      {carText(r, names)}
                      {r.pi !== null && <span className="muted small"> PI {fmt(r.pi)}</span>}
                    </td>
                    <td>{r.setup_label || <span className="muted">—</span>}</td>
                    <td>
                      {r.recording_title || when(r.started_utc)}
                      {r.mode === "demo" && <span className="tag">demo</span>}
                    </td>
                    <td className="num">{fmt(r.t_start, 1)} s</td>
                    <td>{r.valid ? (best && key(best) === key(r) ? <span className="tag tag-certain">best</span> : "valid") : <span className="muted">{r.reason}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {chosen.length === 2 ? <Compare a={chosen[0]} b={chosen[1]} names={names} /> : <p className="muted small pad">Select two runs of the same type to compare them.</p>}
    </div>
  );
}
