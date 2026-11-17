import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { clockLabel, fitCanvas, niceRange, niceStep, readPalette, timeTicks, type Palette } from "../chart";
import { fmt } from "../format";
import { clockText } from "../replay";

interface ChannelMeta {
  id: string;
  label: string;
  unit: string;
  group: string;
  note: string;
  valid: number;
  valid_fraction: number | null;
}
interface LabEvent {
  type: string;
  t: number;
  t_end: number;
  label: string;
  rule: string;
  confidence: string;
  source: "auto" | "recorded";
}
interface Note {
  id: number;
  elapsed_s: number;
  text: string;
}
interface Meta {
  recording_id: string;
  title: string | null;
  started_utc: string;
  mode: string;
  status: string;
  duration_s: number;
  samples: number;
  channels: ChannelMeta[];
  events: LabEvent[];
  annotations: Note[];
}
interface Stats {
  min: number | null;
  max: number | null;
  mean: number | null;
  valid: number;
  missing: number;
}
interface SeriesCh {
  label: string;
  unit: string;
  note: string;
  min: (number | null)[];
  max: (number | null)[];
  count: number[];
  stats: Stats;
}
interface Series {
  from: number;
  to: number;
  bucket_s: number;
  points: number;
  samples_in_range: number;
  channels: Record<string, SeriesCh>;
  gaps: { from: number; to: number; kind: "no_samples" | "no_driving_data" }[];
}

const DEFAULT_CHANNELS = ["speed", "throttle", "brake", "steer"];
const MAX_CHANNELS = 6;
const PREF_KEY = "pitwall.lab.channels";
const PAD_L = 132;
const PAD_R = 12;
const EV_H = 14;
const AXIS_H = 20;
const COLORS: Record<string, keyof Palette> = { speed: "speed", throttle: "throttle", brake: "brake", steer: "steer" };

function readChannels(valid: Set<string>): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(PREF_KEY) ?? "null");
    if (Array.isArray(v)) {
      const ok = v.filter((x): x is string => typeof x === "string" && valid.has(x)).slice(0, MAX_CHANNELS);
      if (ok.length) return ok;
    }
  } catch {
    /* corrupt or blocked storage: use defaults */
  }
  return DEFAULT_CHANNELS.filter((c) => valid.has(c));
}
function writeChannels(ids: string[]): void {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify(ids));
  } catch {
    /* preference only */
  }
}

/** fetch JSON for the current range; older answers never overwrite newer ones */
function useRangeFetch<T>(url: string | null, delay = 120): { data: T | null; loading: boolean; error: string | null } {
  const [state, setState] = useState<{ data: T | null; loading: boolean; error: string | null }>({ data: null, loading: false, error: null });
  useEffect(() => {
    if (!url) return;
    const ctrl = new AbortController();
    setState((s) => ({ ...s, loading: true }));
    const timer = window.setTimeout(() => {
      fetch(url, { signal: ctrl.signal, cache: "no-store" })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`server answered ${r.status}`))))
        .then((d) => setState({ data: d as T, loading: false, error: null }))
        .catch((e) => {
          if ((e as Error).name !== "AbortError") setState((s) => ({ ...s, loading: false, error: (e as Error).message }));
        });
    }, delay);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [url, delay]);
  return state;
}

const unitText = (u: string) => (u === "raw" ? "raw (unit undocumented)" : u);
const val = (v: number | null | undefined, unit: string, d = 1) => (v === null || v === undefined ? "—" : `${fmt(v, d)}${unit && unit !== "raw" ? ` ${unit}` : ""}`);

// ------------------------------------------------------------------ main chart
function LabChart({
  series,
  order,
  range,
  events,
  notes,
  cursor,
  onCursor,
  onZoom,
}: {
  series: Series | null;
  order: string[];
  range: [number, number];
  events: LabEvent[];
  notes: Note[];
  cursor: number | null;
  onCursor: (t: number | null) => void;
  onZoom: (a: number, b: number) => void;
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const pal = useMemo(() => readPalette(), []);
  const [t0, t1] = range;

  const draw = useCallback(() => {
    const c = ref.current;
    if (!c) return;
    const fit = fitCanvas(c);
    if (!fit) return;
    const { ctx, w, h } = fit;
    const x0 = PAD_L;
    const x1 = w - PAD_R;
    const x = (t: number) => x0 + ((t - t0) / Math.max(1e-9, t1 - t0)) * (x1 - x0);
    const top = EV_H + 4;
    const bottom = h - AXIS_H;
    ctx.font = `11px ${pal.font}`;
    // gaps: hatched background over all lanes
    if (series) {
      for (const g of series.gaps) {
        const a = Math.max(x0, x(g.from));
        const b = Math.min(x1, x(g.to));
        if (b <= a) continue;
        ctx.fillStyle = g.kind === "no_driving_data" ? "rgba(232,179,91,0.10)" : "rgba(239,106,98,0.10)";
        ctx.fillRect(a, top, Math.max(1, b - a), bottom - top);
      }
    }
    // time axis
    const step = niceStep(t1 - t0, Math.max(3, Math.floor((x1 - x0) / 110)));
    ctx.strokeStyle = pal.line;
    ctx.fillStyle = pal.text3;
    ctx.textBaseline = "top";
    ctx.textAlign = "center";
    for (const t of timeTicks(t0, t1, Math.max(3, Math.floor((x1 - x0) / 110)))) {
      const px = Math.round(x(t)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(px, top);
      ctx.lineTo(px, bottom);
      ctx.stroke();
      ctx.fillText(clockLabel(t, step), px, bottom + 5);
    }
    // event + note markers
    for (const e of events) {
      if (e.t_end < t0 || e.t > t1) continue;
      ctx.fillStyle = e.confidence === "heuristic" ? pal.text3 : pal.accent;
      const a = Math.max(x0, x(e.t));
      ctx.fillRect(a, 2, Math.max(2, Math.min(x1, x(e.t_end)) - a), EV_H - 6);
    }
    for (const n of notes) {
      if (n.elapsed_s < t0 || n.elapsed_s > t1) continue;
      ctx.fillStyle = pal.steer;
      const px = x(n.elapsed_s);
      ctx.beginPath();
      ctx.moveTo(px - 5, 1);
      ctx.lineTo(px + 5, 1);
      ctx.lineTo(px, EV_H - 1);
      ctx.fill();
    }
    // lanes
    if (series && order.length) {
      const laneH = (bottom - top) / order.length;
      order.forEach((cid, li) => {
        const ch = series.channels[cid];
        if (!ch) return;
        const lt = top + li * laneH + 6;
        const lb = top + (li + 1) * laneH - 4;
        const [lo, hi] = cid === "throttle" || cid === "brake" || cid === "clutch" || cid === "handbrake" ? [0, 100] : cid === "steer" ? [-100, 100] : niceRange(ch.stats.min, ch.stats.max);
        const y = (v: number) => lb - ((v - lo) / (hi - lo)) * (lb - lt);
        ctx.strokeStyle = pal.line;
        ctx.beginPath();
        ctx.moveTo(x0, Math.round(lb) + 0.5);
        ctx.lineTo(x1, Math.round(lb) + 0.5);
        ctx.stroke();
        if (lo < 0 && hi > 0) {
          ctx.setLineDash([2, 3]);
          ctx.beginPath();
          ctx.moveTo(x0, Math.round(y(0)) + 0.5);
          ctx.lineTo(x1, Math.round(y(0)) + 0.5);
          ctx.stroke();
          ctx.setLineDash([]);
        }
        const color = pal[COLORS[cid] ?? "text"];
        ctx.fillStyle = color;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        ctx.font = `600 12px ${pal.font}`;
        ctx.fillText(ch.label, 6, lt);
        ctx.font = `11px ${pal.font}`;
        ctx.fillStyle = pal.text3;
        ctx.fillText(`${fmt(lo, 0)} … ${fmt(hi, 0)} ${ch.unit === "raw" ? "(raw)" : ch.unit}`, 6, lt + 15);
        // envelope: one vertical bar per bucket, connected when buckets are adjacent
        ctx.save();
        ctx.beginPath();
        ctx.rect(x0, lt - 2, x1 - x0, lb - lt + 4);
        ctx.clip();
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 1.25;
        const bw = (x1 - x0) / series.points;
        let prev: number | null = null;
        for (let b = 0; b < series.points; b++) {
          const mn = ch.min[b];
          const mx = ch.max[b];
          if (mn === null || mx === null) {
            prev = null; // gap: the line breaks
            continue;
          }
          const px = x0 + (b + 0.5) * bw;
          const ya = y(mx);
          const yb = y(mn);
          if (prev !== null) {
            ctx.beginPath();
            ctx.moveTo(px - bw, prev);
            ctx.lineTo(px, (ya + yb) / 2);
            ctx.stroke();
          }
          ctx.fillRect(px - 0.6, ya, 1.2, Math.max(1.2, yb - ya));
          prev = (ya + yb) / 2;
        }
        ctx.restore();
      });
    }
    // cursor
    if (cursor !== null && cursor >= t0 && cursor <= t1) {
      ctx.strokeStyle = pal.text;
      ctx.lineWidth = 1;
      const px = Math.round(x(cursor)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(px, top);
      ctx.lineTo(px, bottom);
      ctx.stroke();
    }
    if (drag) {
      ctx.fillStyle = "rgba(127,167,209,0.18)";
      const a = x(Math.min(drag[0], drag[1]));
      const b = x(Math.max(drag[0], drag[1]));
      ctx.fillRect(a, top, b - a, bottom - top);
    }
  }, [series, order, t0, t1, events, notes, cursor, drag, pal]);

  useEffect(() => {
    draw();
    const c = ref.current;
    if (!c) return;
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [draw]);

  const tAt = (clientX: number): number | null => {
    const r = ref.current!.getBoundingClientRect();
    const k = ref.current!.offsetWidth ? r.width / ref.current!.offsetWidth : 1;
    const xa = r.left + PAD_L * k;
    const xb = r.right - PAD_R * k;
    if (clientX < xa - 2) return null;
    return t0 + Math.min(1, Math.max(0, (clientX - xa) / (xb - xa))) * (t1 - t0);
  };
  const onKey = (e: React.KeyboardEvent) => {
    const span = t1 - t0;
    const cur = cursor ?? (t0 + t1) / 2;
    const step = (e.shiftKey ? 0.1 : 0.01) * span;
    const act: Record<string, () => void> = {
      ArrowLeft: () => onCursor(Math.max(t0, cur - step)),
      ArrowRight: () => onCursor(Math.min(t1, cur + step)),
      "+": () => onZoom(cur - span / 4, cur + span / 4),
      "=": () => onZoom(cur - span / 4, cur + span / 4),
      "-": () => onZoom(cur - span, cur + span),
      Escape: () => onCursor(null),
    };
    const fn = act[e.key];
    if (fn) {
      e.preventDefault();
      fn();
    }
  };
  return (
    <canvas
      ref={ref}
      className="lab-canvas"
      tabIndex={0}
      role="slider"
      aria-label="Lab chart cursor. Left and right arrows move it (Shift: larger steps), plus and minus zoom around it, Escape removes it. Drag with the mouse to zoom into a range."
      aria-valuemin={Math.round(t0)}
      aria-valuemax={Math.round(t1)}
      aria-valuenow={cursor === null ? undefined : Math.round(cursor)}
      aria-valuetext={cursor === null ? "No moment selected" : clockText(cursor, true)}
      style={{ height: `${Math.max(220, order.length * 92 + EV_H + AXIS_H + 8)}px` }}
      onKeyDown={onKey}
      onPointerDown={(e) => {
        const t = tAt(e.clientX);
        if (t === null) return;
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        setDrag([t, t]);
      }}
      onPointerMove={(e) => {
        const t = tAt(e.clientX);
        if (drag && t !== null) setDrag([drag[0], t]);
        else onCursor(t);
      }}
      onPointerUp={(e) => {
        if (!drag) return;
        const t = tAt(e.clientX) ?? drag[1];
        const a = Math.min(drag[0], t);
        const b = Math.max(drag[0], t);
        setDrag(null);
        if (b - a > (t1 - t0) / 150) onZoom(a, b);
        else onCursor(t);
      }}
      onPointerLeave={() => !drag && onCursor(cursor)}
    />
  );
}

// ------------------------------------------------------------------ side panels
function GG({ url }: { url: string }) {
  const { data } = useRangeFetch<{ points: [number, number][]; combined_g_max: number | null; combined_g_p95: number | null }>(url);
  const ref = useRef<HTMLCanvasElement | null>(null);
  const pal = useMemo(() => readPalette(), []);
  useEffect(() => {
    const c = ref.current;
    if (!c || !data) return;
    const draw = () => {
      const fit = fitCanvas(c);
      if (!fit) return;
      const { ctx, w, h } = fit;
      const lim = Math.max(1.5, Math.ceil((data.combined_g_p95 ?? 1) + 0.5));
      const r = Math.min(w, h) / 2 - 18;
      const cx = w / 2;
      const cy = h / 2;
      ctx.strokeStyle = pal.line;
      ctx.fillStyle = pal.text3;
      ctx.font = `11px ${pal.font}`;
      const every = lim > 6 ? 4 : lim > 3 ? 2 : 1; // keep ring labels readable
      for (let g = 1; g <= lim; g++) {
        ctx.beginPath();
        ctx.arc(cx, cy, (g / lim) * r, 0, Math.PI * 2);
        ctx.stroke();
        // labels just below the horizontal axis, right of each ring (clear of the axis titles)
        if (g % every === 0 || g === lim) ctx.fillText(`${g} g`, cx + (g / lim) * r + 3, cy + 12);
      }
      ctx.beginPath();
      ctx.moveTo(cx - r, cy);
      ctx.lineTo(cx + r, cy);
      ctx.moveTo(cx, cy - r);
      ctx.lineTo(cx, cy + r);
      ctx.stroke();
      ctx.textAlign = "center";
      ctx.fillText("accelerate", cx, 2 + 9);
      ctx.fillText("brake", cx, h - 4);
      ctx.fillStyle = pal.steer;
      ctx.globalAlpha = 0.35;
      for (const [gx, gy] of data.points) {
        const px = cx + Math.max(-1.1, Math.min(1.1, gx / lim)) * r;
        const py = cy - Math.max(-1.1, Math.min(1.1, gy / lim)) * r;
        ctx.fillRect(px - 1, py - 1, 2, 2);
      }
      ctx.globalAlpha = 1;
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [data, pal]);
  return (
    <section className="panel lab-panel" aria-labelledby="gg-title">
      <h3 id="gg-title" className="panel-title">
        G-G (lateral × longitudinal)
      </h3>
      <canvas ref={ref} className="lab-square" role="img" aria-label={data ? `Acceleration scatter: peak combined ${fmt(data.combined_g_max, 1)} g, 95th percentile ${fmt(data.combined_g_p95, 1)} g` : "Loading"} />
      {data && <p className="muted small">Peak {fmt(data.combined_g_max, 1)} g · 95th percentile {fmt(data.combined_g_p95, 1)} g</p>}
    </section>
  );
}

function Route({ url }: { url: string }) {
  const { data } = useRangeFetch<{ lines: [number, number][][]; bounds: { x_min: number; x_max: number; z_min: number; z_max: number } | null }>(url);
  const ref = useRef<HTMLCanvasElement | null>(null);
  const pal = useMemo(() => readPalette(), []);
  useEffect(() => {
    const c = ref.current;
    if (!c || !data) return;
    const draw = () => {
      const fit = fitCanvas(c);
      if (!fit || !data.bounds) return;
      const { ctx, w, h } = fit;
      const b = data.bounds;
      const span = Math.max(b.x_max - b.x_min, b.z_max - b.z_min, 1);
      const s = (Math.min(w, h) - 24) / span;
      const ox = (w - (b.x_max - b.x_min) * s) / 2;
      const oy = (h - (b.z_max - b.z_min) * s) / 2;
      const P = (p: [number, number]): [number, number] => [ox + (p[0] - b.x_min) * s, h - (oy + (p[1] - b.z_min) * s)];
      ctx.strokeStyle = pal.speed;
      ctx.lineWidth = 1.5;
      for (const l of data.lines) {
        ctx.beginPath();
        l.forEach((p, i) => (i ? ctx.lineTo(...P(p)) : ctx.moveTo(...P(p))));
        ctx.stroke();
      }
      const first = data.lines[0]?.[0];
      const lastL = data.lines[data.lines.length - 1];
      const last = lastL?.[lastL.length - 1];
      if (first) {
        ctx.fillStyle = pal.throttle;
        ctx.beginPath();
        ctx.arc(...P(first), 4, 0, Math.PI * 2);
        ctx.fill();
      }
      if (last) {
        ctx.fillStyle = pal.brake;
        ctx.fillRect(P(last)[0] - 4, P(last)[1] - 4, 8, 8);
      }
      ctx.fillStyle = pal.text3;
      ctx.font = `11px ${pal.font}`;
      const bar = Math.pow(10, Math.floor(Math.log10(span / 4)));
      ctx.fillRect(10, h - 10, bar * s, 2);
      ctx.fillText(`${bar >= 1000 ? `${bar / 1000} km` : `${bar} m`}`, 10, h - 14);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [data, pal]);
  return (
    <section className="panel lab-panel" aria-labelledby="route-title">
      <h3 id="route-title" className="panel-title">
        Route (top view)
      </h3>
      <canvas ref={ref} className="lab-square" role="img" aria-label="Driven path in game world coordinates; green circle is the start of the range, red square the end" />
      <p className="muted small">
        Green marks the start, red marks the end. Breaks mark gaps or jumps.
      </p>
    </section>
  );
}

function Power({ url }: { url: string }) {
  const { data } = useRangeFetch<{ bins: { rpm_from: number; rpm_to: number; n: number; power_kw_median: number; torque_median: number | null }[] }>(url);
  const bins = (data?.bins ?? []).filter((b) => b.n >= 20);
  const peak = bins.reduce((m, b) => (b.power_kw_median > (m?.power_kw_median ?? -Infinity) ? b : m), null as (typeof bins)[number] | null);
  const max = Math.max(1, ...bins.map((b) => b.power_kw_median));
  return (
    <section className="panel lab-panel" aria-labelledby="power-title">
      <h3 id="power-title" className="panel-title">
        Power by engine speed
      </h3>
      {!data ? (
        <p className="muted small">Loading…</p>
      ) : bins.length === 0 ? (
        <p className="muted small">Too few full-throttle samples in this range.</p>
      ) : (
        <>
          <div className="power-bars" role="img" aria-label={`Median power per rpm band; highest ${fmt(peak?.power_kw_median)} kW at ${peak?.rpm_from}-${peak?.rpm_to} rpm`}>
            {bins.map((b) => (
              <span key={b.rpm_from} className={`power-bar${b === peak ? " is-peak" : ""}`} style={{ height: `${Math.max(2, (Math.max(0, b.power_kw_median) / max) * 100)}%` }} title={`${b.rpm_from}–${b.rpm_to} rpm: ${fmt(b.power_kw_median)} kW, ${fmt(b.torque_median)} N·m, ${b.n} samples`} />
            ))}
          </div>
          <p className="small num">
            {bins[0].rpm_from} … {bins[bins.length - 1].rpm_to} rpm · highest median {fmt(peak?.power_kw_median)} kW at {peak?.rpm_from}–{peak?.rpm_to} rpm
          </p>
        </>
      )}
      {bins.length > 0 && <p className="muted small">Median power at full throttle. At least 20 samples per band.</p>}
    </section>
  );
}

interface Summary {
  samples: number;
  samples_without_driving_data: number;
  duration_s: number;
  covered_s: number;
  distance_m: number;
  speed_kmh: { max: number | null; mean: number | null };
  g_lat_abs_max: number | null;
  g_long_max: number | null;
  g_long_min: number | null;
  full_throttle_s: number;
  braking_s: number;
  grip_lost_s: number;
  gear_changes: number;
}

function RangeSummary({ url }: { url: string }) {
  const { data: s } = useRangeFetch<Summary>(url);
  const pct = (x: number) => (s && s.covered_s > 0 ? ` (${fmt((x / s.covered_s) * 100)} %)` : "");
  const rows: [string, string][] = s
    ? [
        ["Duration", `${clockText(s.duration_s, true)} (${fmt(s.covered_s, 1)} s with data)`],
        ["Samples", `${s.samples.toLocaleString()}${s.samples_without_driving_data ? `, ${s.samples_without_driving_data.toLocaleString()} without driving data` : ""}`],
        ["Distance", `${fmt(s.distance_m / 1000, 2)} km`],
        ["Top / average speed", `${val(s.speed_kmh.max, "km/h")} / ${val(s.speed_kmh.mean, "km/h")}`],
        ["Full throttle", `${fmt(s.full_throttle_s, 1)} s${pct(s.full_throttle_s)}`],
        ["Braking (≥ 10 %)", `${fmt(s.braking_s, 1)} s${pct(s.braking_s)}`],
        ["Grip lost", `${fmt(s.grip_lost_s, 1)} s${pct(s.grip_lost_s)}`],
        ["Peak lateral / accel / braking", `${val(s.g_lat_abs_max, "g")} / ${val(s.g_long_max, "g")} / ${val(s.g_long_min, "g")}`],
        ["Gear changes", String(s.gear_changes)],
      ]
    : [];
  return (
    <section className="panel lab-panel" aria-labelledby="sum-title">
      <h3 id="sum-title" className="panel-title">
        Selected range
      </h3>
      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className="num">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

// ------------------------------------------------------------------ page
export function Lab({ id, onClose, onReplayAt }: { id: string; onClose: () => void; onReplayAt: (id: string, t: number) => void }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [range, setRange] = useState<[number, number] | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [evFilter, setEvFilter] = useState("all");
  const [noteText, setNoteText] = useState("");
  const [noteMsg, setNoteMsg] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`/api/v1/recordings/${encodeURIComponent(id)}/lab`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`server answered ${r.status}`))))
      .then((m: Meta) => {
        if (!alive) return;
        const channels = m.channels.filter((channel) => channel.id !== "fuel");
        setMeta({ ...m, channels });
        setNotes(m.annotations);
        setSelected(readChannels(new Set(channels.map((c) => c.id))));
        setRange([0, Math.max(0.1, m.duration_s)]);
      })
      .catch((e) => alive && setMetaError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [id]);

  const dur = meta?.duration_s ?? 0;
  const zoom = useCallback(
    (a: number, b: number) => {
      const span = Math.max(0.5, b - a);
      let lo = Math.max(0, a);
      let hi = Math.min(dur, lo + span);
      lo = Math.max(0, hi - span);
      if (hi - lo < 0.5) hi = Math.min(dur, lo + 0.5);
      setRange([lo, hi]);
    },
    [dur],
  );
  const base = `/api/v1/recordings/${encodeURIComponent(id)}`;
  // round outwards so a range that ends at the last sample still includes it
  const rq = range ? `from=${(Math.floor(range[0] * 1000) / 1000).toFixed(3)}&to=${(Math.ceil(range[1] * 1000) / 1000).toFixed(3)}` : "";
  const seriesUrl = range && selected.length ? `${base}/lab/series?channels=${selected.join(",")}&${rq}&points=1000` : null;
  const { data: series, loading, error } = useRangeFetch<Series>(seriesUrl);

  if (metaError) {
    return (
      <div className="empty">
        <h1>This recording could not be analysed</h1>
        <p className="error-text">{metaError}</p>
        <button type="button" className="btn" onClick={onClose}>
          Back to Sessions
        </button>
      </div>
    );
  }
  if (!meta || !range) return <p className="muted pad">Loading analysis…</p>;

  const toggle = (cid: string) => {
    const next = selected.includes(cid) ? selected.filter((c) => c !== cid) : [...selected, cid].slice(-MAX_CHANNELS);
    setSelected(next);
    writeChannels(next);
  };
  const groups = [...new Set(meta.channels.map((c) => c.group))];
  const events = meta.events.filter((e) => evFilter === "all" || e.type === evFilter);
  const evTypes = [...new Set(meta.events.map((e) => e.type))];
  const at = (t: number) => (cursor !== null ? cursor : t);
  const bucketAt = (t: number) => (series ? Math.min(series.points - 1, Math.max(0, Math.floor((t - series.from) / series.bucket_s))) : -1);

  const addNote = async () => {
    const t = at(range[0]);
    setNoteMsg(null);
    try {
      const r = await fetch(`${base}/annotations`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ elapsed_s: t, text: noteText }) });
      if (!r.ok) throw new Error(r.status === 403 ? "this device may only view" : `server answered ${r.status}`);
      const n = (await r.json()) as Note;
      setNotes([...notes, n].sort((a, b) => a.elapsed_s - b.elapsed_s));
      setNoteText("");
      setNoteMsg(`Note added at ${clockText(n.elapsed_s, true)}.`);
    } catch (e) {
      setNoteMsg(`Not saved: ${(e as Error).message}.`);
    }
  };
  const delNote = async (n: Note) => {
    const r = await fetch(`${base}/annotations/${n.id}`, { method: "DELETE" }).catch(() => null);
    if (r?.ok) setNotes(notes.filter((x) => x.id !== n.id));
    else setNoteMsg("The note could not be deleted.");
  };

  return (
    <div className="lab">
      <section className="lab-bar" aria-label="Lab controls">
        <div className="replay-title">
          <span className="mode-badge mode-lab">Lab</span>
          <strong>{meta.title || new Date(meta.started_utc).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</strong>
          {meta.mode === "demo" && <span className="tag">demo recording</span>}
          <span className="muted small num">
            {clockText(dur)} · {meta.samples.toLocaleString()} samples
          </span>
          <button type="button" className="btn btn-small btn-quiet replay-close" onClick={onClose}>
            Close Lab
          </button>
        </div>
        <div className="replay-transport">
          <span className="num replay-time">
            {clockText(range[0], true)} – {clockText(range[1], true)}
          </span>
          <button type="button" className="btn btn-small btn-quiet" onClick={() => zoom((range[0] + range[1]) / 2 - (range[1] - range[0]), (range[0] + range[1]) / 2 + (range[1] - range[0]))} disabled={range[1] - range[0] >= dur}>
            Zoom out
          </button>
          <button type="button" className="btn btn-small btn-quiet" onClick={() => setRange([0, dur])} disabled={range[0] === 0 && range[1] === dur}>
            Whole recording
          </button>
          <button type="button" className="btn btn-small btn-quiet" onClick={() => onReplayAt(id, at(range[0]))}>
            Replay from {clockText(at(range[0]))}
          </button>
          <a className="btn btn-small btn-quiet" href={`${base}/lab/export.csv?channels=${selected.join(",") || "speed"}&${rq}`} download>
            Export CSV
          </a>
          <span className="muted small">
            {loading ? "Loading…" : error ? <span className="text-danger">Could not load: {error}</span> : series ? `${series.samples_in_range.toLocaleString()} samples in range` : ""}
          </span>
        </div>
      </section>

      <div className="lab-grid">
        <aside className="panel lab-channels" aria-labelledby="ch-title">
          <h3 id="ch-title" className="panel-title">
            Channels <span className="muted small">up to {MAX_CHANNELS}</span>
          </h3>
          {groups.map((g) => (
            <fieldset key={g} className="ch-group">
              <legend>{g}</legend>
              {meta.channels
                .filter((c) => c.group === g)
                .map((c) => (
                  <label key={c.id} className="check ch-item" title={c.note || undefined}>
                    <input type="checkbox" checked={selected.includes(c.id)} onChange={() => toggle(c.id)} />
                    <span>{c.label}</span>
                    <span className="muted small">
                      {[c.unit ? unitText(c.unit) : c.id === "gear" ? "code" : "", c.valid_fraction !== null && c.valid_fraction < 0.999 ? `${fmt(c.valid_fraction * 100)} % with data` : ""].filter(Boolean).join(" · ")}
                    </span>
                  </label>
                ))}
            </fieldset>
          ))}
        </aside>

        <div className="lab-main">
          <section className="panel" aria-labelledby="lab-chart-title">
            <h2 id="lab-chart-title" className="panel-title">
              Channels over time <span className="muted small">Drag to zoom · Shading marks gaps</span>
            </h2>
            {selected.length === 0 ? (
              <p className="muted pad">Choose at least one channel.</p>
            ) : (
              <LabChart series={series} order={selected} range={range} events={meta.events} notes={notes} cursor={cursor} onCursor={setCursor} onZoom={zoom} />
            )}
            <div className="lab-readout" aria-live="off">
              {cursor === null ? (
                <p className="muted small">Hover or use the arrow keys to inspect the min–max values.</p>
              ) : (
                <dl>
                  <div>
                    <dt>At</dt>
                    <dd className="num">{clockText(cursor, true)}</dd>
                  </div>
                  {series &&
                    selected.map((cid) => {
                      const ch = series.channels[cid];
                      if (!ch) return null;
                      const b = bucketAt(cursor);
                      const mn = ch.min[b];
                      const mx = ch.max[b];
                      const txt = mn === null || mx === null ? "no data" : Math.abs(mx - mn) < 1e-6 ? val(mn, ch.unit) : `${fmt(mn, 1)} … ${val(mx, ch.unit)}`;
                      return (
                        <div key={cid}>
                          <dt>{ch.label}</dt>
                          <dd className="num">{txt}</dd>
                        </div>
                      );
                    })}
                </dl>
              )}
            </div>
            {series && (
              <table className="lab-stats">
                <caption className="sub-title">Range statistics</caption>
                <thead>
                  <tr>
                    <th scope="col">Channel</th>
                    <th scope="col">Min</th>
                    <th scope="col">Mean</th>
                    <th scope="col">Max</th>
                    <th scope="col">Valid samples</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.map((cid) => {
                    const ch = series.channels[cid];
                    if (!ch) return null;
                    return (
                      <tr key={cid}>
                        <th scope="row" title={ch.note || undefined}>
                          {ch.label} <span className="muted small">{unitText(ch.unit)}</span>
                        </th>
                        <td className="num">{fmt(ch.stats.min, 1)}</td>
                        <td className="num">{fmt(ch.stats.mean, 1)}</td>
                        <td className="num">{fmt(ch.stats.max, 1)}</td>
                        <td className="num">
                          {ch.stats.valid.toLocaleString()}
                          {ch.stats.missing ? <span className="muted"> (+{ch.stats.missing.toLocaleString()} without data)</span> : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>

          <div className="lab-panels">
            <RangeSummary url={`${base}/lab/summary?${rq}`} />
            <GG url={`${base}/lab/gg?${rq}`} />
            <Route url={`${base}/lab/route?${rq}`} />
            <Power url={`${base}/lab/power?${rq}`} />
          </div>

          <section className="panel" aria-labelledby="ev-title">
            <div className="panel-head">
              <h2 id="ev-title" className="panel-title">
                Events &amp; notes
              </h2>
              <label className="inline-field">
                <span>Show</span>
                <select value={evFilter} onChange={(e) => setEvFilter(e.target.value)}>
                  <option value="all">all events ({meta.events.length})</option>
                  {evTypes.map((t) => (
                    <option key={t} value={t}>
                      {t.replace(/_/g, " ")} ({meta.events.filter((e) => e.type === t).length})
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <form
              className="note-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (noteText.trim()) void addNote();
              }}
            >
              <label className="field">
                <span>Note at {clockText(at(range[0]), true)}</span>
                <input value={noteText} maxLength={500} onChange={(e) => setNoteText(e.target.value)} />
              </label>
              <button type="submit" className="btn btn-small" disabled={!noteText.trim()}>
                Add note
              </button>
              <span className="small" role="status">
                {noteMsg}
              </span>
            </form>
            <ol className="events lab-events">
              {notes.map((n) => (
                <li key={`n${n.id}`} className="ev-note">
                  <span className="mono">{clockText(n.elapsed_s, true)}</span> <span className="tag">note</span> {n.text}{" "}
                  <button type="button" className="btn btn-small btn-quiet" onClick={() => zoom(n.elapsed_s - 10, n.elapsed_s + 10)}>
                    Show
                  </button>
                  <button type="button" className="btn btn-small btn-quiet" onClick={() => void delNote(n)} aria-label={`Delete note at ${clockText(n.elapsed_s)}`}>
                    Delete
                  </button>
                </li>
              ))}
              {events.slice(0, 300).map((e, i) => (
                <li key={i}>
                  <span className="mono">{clockText(e.t, true)}</span> <span className={`tag${e.confidence === "heuristic" ? "" : " tag-certain"}`}>{e.confidence}</span> {e.label}{" "}
                  <span className="muted small">— {e.rule}</span>{" "}
                  <button type="button" className="btn btn-small btn-quiet" onClick={() => zoom(e.t - 5, e.t_end + 5)}>
                    Show
                  </button>
                  <button type="button" className="btn btn-small btn-quiet" onClick={() => onReplayAt(id, Math.max(0, e.t - 3))}>
                    Replay
                  </button>
                </li>
              ))}
            </ol>
            {events.length > 300 && <p className="muted small">First 300 of {events.length} shown; narrow with the filter.</p>}
          </section>
        </div>
      </div>
    </div>
  );
}
