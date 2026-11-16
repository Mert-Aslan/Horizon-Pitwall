import { useEffect, useRef, useState } from "react";
import { DASH, fmt } from "../format";
import { GAP_S, sampleAt, speedAxisMax, type Sample, type SpeedScale } from "../history";
import { usePref } from "../prefs";
import { store } from "../stream";

export const WINDOW_S = 20;
const TICKS = [-20, -15, -10, -5, 0];
const SCALE_PREFS = ["auto", "200", "300", "400"] as const;
type ScalePref = (typeof SCALE_PREFS)[number];

const PAD_L = 104; // label gutter
const PAD_R = 14;
const AXIS_H = 22;
const PAD_T = 6;
const LANE_GAP = 12;

interface Palette {
  text: string;
  text2: string;
  text3: string;
  line: string;
  speed: string;
  throttle: string;
  brake: string;
  steer: string;
  font: string;
}

function readPalette(): Palette {
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
    font: v("--font-ui"),
  };
}

interface Channel {
  name: string;
  color: keyof Palette;
  pick: (s: Sample) => number | null;
}
interface Lane {
  channels: Channel[];
  min: number;
  max: number;
  top: number;
  bottom: number;
  scaleText: string;
  centre?: boolean;
}

const pct = (v: number | null) => (v === null ? null : v * 100);

function lanes(h: number, speedMax: number): Lane[] {
  const usable = h - PAD_T - AXIS_H - 2 * LANE_GAP;
  const hs = [0.42, 0.32, 0.26].map((f) => usable * f);
  let y = PAD_T;
  const box = (i: number) => {
    const top = y;
    y += hs[i] + LANE_GAP;
    return { top, bottom: top + hs[i] };
  };
  return [
    { ...box(0), channels: [{ name: "Speed", color: "speed", pick: (s) => s.speed }], min: 0, max: speedMax, scaleText: `0–${speedMax} km/h` },
    {
      ...box(1),
      channels: [
        { name: "Throttle", color: "throttle", pick: (s) => pct(s.throttle) },
        { name: "Brake", color: "brake", pick: (s) => pct(s.brake) },
      ],
      min: 0,
      max: 100,
      scaleText: "0–100 %",
    },
    { ...box(2), channels: [{ name: "Steering", color: "steer", pick: (s) => s.steer }], min: -1, max: 1, scaleText: "R up, L down", centre: true },
  ];
}

interface View {
  samples: () => Iterable<Sample>;
  now: number;
  speedMax: number;
}

function draw(ctx: CanvasRenderingContext2D, w: number, h: number, pal: Palette, view: View, cursorOffset: number | null, cursorSample: Sample | null) {
  ctx.clearRect(0, 0, w, h);
  const x0 = PAD_L;
  const x1 = w - PAD_R;
  const t0 = view.now - WINDOW_S;
  const x = (t: number) => x0 + ((t - t0) / WINDOW_S) * (x1 - x0);
  const ls = lanes(h, view.speedMax);
  const axisY = h - AXIS_H + 4;

  // time grid + labels
  ctx.lineWidth = 1;
  ctx.strokeStyle = pal.line;
  ctx.fillStyle = pal.text3;
  ctx.font = `11px ${pal.font}`;
  ctx.textBaseline = "top";
  for (const tick of TICKS) {
    const px = Math.round(x(view.now + tick)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(px, PAD_T);
    ctx.lineTo(px, axisY - 6);
    ctx.stroke();
    const label = tick === 0 ? "now" : `${tick} s`;
    ctx.textAlign = tick === 0 ? "right" : tick === -WINDOW_S ? "left" : "center";
    ctx.fillText(label.replace("-", "−"), px, axisY);
  }

  for (const lane of ls) {
    const y = (v: number) => lane.bottom - ((Math.min(lane.max, Math.max(lane.min, v)) - lane.min) / (lane.max - lane.min)) * (lane.bottom - lane.top);
    // baseline / centre line
    ctx.strokeStyle = pal.line;
    ctx.beginPath();
    const base = Math.round(lane.centre ? y(0) : lane.bottom) + 0.5;
    ctx.moveTo(x0, base);
    ctx.lineTo(x1, base);
    ctx.stroke();
    // gutter labels: channel names in their colour (text + colour, not colour alone)
    ctx.textAlign = "left";
    ctx.font = `600 12px ${pal.font}`;
    lane.channels.forEach((c, i) => {
      ctx.fillStyle = pal[c.color];
      ctx.fillText(c.name, 0, lane.top + i * 16);
    });
    ctx.font = `11px ${pal.font}`;
    ctx.fillStyle = pal.text3;
    ctx.fillText(lane.scaleText, 0, lane.top + lane.channels.length * 16 + 2);

    // data: lines break on gaps > GAP_S and on missing values (never drawn as zero).
    // Clipped to the plot so samples just older than the window never cross the labels.
    ctx.save();
    ctx.beginPath();
    ctx.rect(x0, lane.top - 4, x1 - x0, lane.bottom - lane.top + 8);
    ctx.clip();
    ctx.lineWidth = 1.75;
    ctx.lineJoin = "round";
    for (const c of lane.channels) {
      ctx.strokeStyle = pal[c.color];
      ctx.beginPath();
      let pen = false;
      let lastT = -Infinity;
      for (const s of view.samples()) {
        const v = c.pick(s);
        if (s.t - lastT > GAP_S) pen = false;
        lastT = s.t;
        if (v === null || !Number.isFinite(v)) {
          pen = false;
          continue;
        }
        const px = x(s.t);
        const py = y(v);
        if (pen) ctx.lineTo(px, py);
        else {
          ctx.moveTo(px, py);
          ctx.lineTo(px + 0.5, py); // a lone sample stays visible as a dot
          pen = true;
        }
      }
      ctx.stroke();
    }

    ctx.restore();

    // cursor markers
    if (cursorOffset !== null && cursorSample) {
      for (const c of lane.channels) {
        const v = c.pick(cursorSample);
        if (v === null || !Number.isFinite(v)) continue;
        ctx.fillStyle = pal[c.color];
        ctx.beginPath();
        ctx.arc(x(cursorSample.t), y(v), 3.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  if (cursorOffset !== null) {
    const px = Math.round(x(view.now + cursorOffset)) + 0.5;
    ctx.strokeStyle = pal.text;
    ctx.lineWidth = 1;
    ctx.setLineDash(cursorSample ? [] : [3, 3]);
    ctx.beginPath();
    ctx.moveTo(px, PAD_T);
    ctx.lineTo(px, axisY - 6);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

interface Frozen {
  now: number;
  samples: Sample[];
  speedMax: number;
}

const READOUT_KEYS = ["time", "speed", "rpm", "throttle", "brake", "steer"] as const;
type ReadoutKey = (typeof READOUT_KEYS)[number];

function readoutValues(offset: number, s: Sample | null): Record<ReadoutKey, string> {
  const p = (v: number | null) => (v === null ? DASH : `${fmt(v * 100)}%`);
  const steer = s?.steer ?? null;
  return {
    time: offset > -0.05 ? "now" : `${fmt(offset, 1).replace("-", "−")} s`,
    speed: s ? `${fmt(s.speed)} km/h` : "no data",
    rpm: s ? fmt(s.rpm) : DASH,
    throttle: s ? p(s.throttle) : DASH,
    brake: s ? p(s.brake) : DASH,
    steer: !s || steer === null ? DASH : `${steer < 0 ? "L " : steer > 0 ? "R " : ""}${fmt(Math.abs(steer) * 100)}%`,
  };
}

function valueText(offset: number, s: Sample | null): string {
  const r = readoutValues(offset, s);
  if (!s) return `${r.time}: no data at this moment`;
  return `${r.time}: speed ${r.speed}, throttle ${r.throttle}, brake ${r.brake}, steering ${r.steer}, ${r.rpm} rpm`;
}

/** Last 20 s of speed, pedals and steering. Drawing is driven by store updates and
 *  resize events, not by React renders; a shared cursor inspects one moment and the
 *  view can be paused (a copy of the window is kept) while data keeps arriving. */
export function Traces({ live }: { live: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const valueRefs = useRef<Partial<Record<ReadoutKey, HTMLElement | null>>>({});
  const [scalePref, setScalePref] = usePref<ScalePref>("speedScale", SCALE_PREFS, "auto");
  const [frozen, setFrozen] = useState<Frozen | null>(null);
  const [cursorOn, setCursorOn] = useState(false);
  const [ariaText, setAriaText] = useState("No moment selected");

  // Mutable state read by the draw loop (kept out of React renders).
  const st = useRef({
    cursor: null as number | null,
    frozen: null as Frozen | null,
    live,
    staleNow: null as number | null,
    scale: "auto" as SpeedScale,
    request: (_force?: boolean) => {},
    lastSample: null as Sample | null,
  });
  st.current.frozen = frozen;
  st.current.live = live;
  st.current.scale = scalePref === "auto" ? "auto" : (Number(scalePref) as SpeedScale);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const pal = readPalette(); // once per mount, not per frame
    let size = { w: 0, h: 0 };
    let zoomK = 1; // CSS zoom on large monitors: backing store must include it to stay sharp
    let raf = 0;
    let drawnVersion = -1;
    let drawnKey = "";

    const currentView = (): View => {
      const s = st.current;
      if (s.frozen) {
        const f = s.frozen;
        return { samples: () => f.samples, now: f.now, speedMax: f.speedMax };
      }
      // While not live the time axis is anchored, so stale data does not drift away.
      let now = performance.now() / 1000;
      if (!s.live) now = s.staleNow ??= now;
      else s.staleNow = null;
      return { samples: () => store.history.since(now - WINDOW_S - 1), now, speedMax: speedAxisMax(store.history.peakSpeed, s.scale) };
    };

    const render = () => {
      raf = 0;
      if (document.hidden || size.w === 0) return;
      const s = st.current;
      const dpr = (window.devicePixelRatio || 1) * zoomK;
      const bw = Math.round(size.w * dpr);
      const bh = Math.round(size.h * dpr);
      const key = `${bw}x${bh}:${s.cursor}:${!!s.frozen}:${s.live}:${s.scale}`;
      // Skip when nothing that affects the picture changed (stale / paused / idle).
      if (!s.live && !s.frozen && key === drawnKey && store.history.version === drawnVersion) return;
      if (s.frozen && key === drawnKey) return;
      if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const view = currentView();
      let cs: Sample | null = null;
      if (s.cursor !== null) {
        const t = view.now + s.cursor;
        const win = s.frozen ? s.frozen.samples : store.history.snapshot(t - 1, t + 1);
        cs = sampleAt(win, t);
        updateReadout(s.cursor, cs);
      }
      draw(ctx, size.w, size.h, pal, view, s.cursor, cs);
      drawnVersion = store.history.version;
      drawnKey = key;
    };

    const request = (force?: boolean) => {
      if (force) drawnKey = "";
      if (!raf) raf = requestAnimationFrame(render);
    };
    st.current.request = request;

    const updateReadout = (offset: number, s: Sample | null) => {
      const vals = readoutValues(offset, s);
      for (const k of READOUT_KEYS) {
        const el = valueRefs.current[k];
        if (el && el.textContent !== vals[k]) el.textContent = vals[k];
      }
      st.current.lastSample = s;
    };

    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      size = { w: r.width, h: r.height };
      zoomK = r.width > 0 ? canvas.getBoundingClientRect().width / r.width : 1;
      drawnKey = "";
      request();
    });
    ro.observe(canvas);
    const unsub = store.subscribe(() => {
      if (!st.current.frozen) request();
    });
    const onVis = () => {
      drawnKey = "";
      request();
    };
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("resize", onVis); // also fires on devicePixelRatio changes
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      unsub();
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("resize", onVis);
    };
  }, []);

  // React state changes (pause, scale, live) need one redraw.
  useEffect(() => st.current.request(true), [frozen, scalePref, live, cursorOn]);

  const plotOffset = (clientX: number): number | null => {
    const c = canvasRef.current!;
    const r = c.getBoundingClientRect();
    const k = c.offsetWidth > 0 ? r.width / c.offsetWidth : 1; // CSS zoom factor
    const x0 = r.left + PAD_L * k;
    const x1 = r.right - PAD_R * k;
    if (clientX < x0) return null;
    const f = Math.min(1, (clientX - x0) / (x1 - x0));
    return -WINDOW_S * (1 - f);
  };

  const setCursor = (offset: number | null, announce: boolean) => {
    const o = offset === null ? null : Math.max(-WINDOW_S, Math.min(0, Math.round(offset * 10) / 10));
    st.current.cursor = o;
    setCursorOn(o !== null);
    st.current.request();
    if (announce && o !== null) {
      // computed after the redraw has picked the sample
      requestAnimationFrame(() => setAriaText(valueText(o, st.current.lastSample)));
    }
  };

  const pause = () => {
    const now = performance.now() / 1000;
    setFrozen({
      now,
      samples: store.history.snapshot(now - WINDOW_S - 1, now),
      speedMax: speedAxisMax(store.history.peakSpeed, st.current.scale),
    });
  };
  const resume = () => setFrozen(null);

  const onKey = (e: React.KeyboardEvent) => {
    const cur = st.current.cursor ?? 0;
    const step = e.shiftKey ? 1 : 0.1;
    let handled = true;
    switch (e.key) {
      case "ArrowLeft":
        setCursor(cur - step, true);
        break;
      case "ArrowRight":
        setCursor(cur + step, true);
        break;
      case "Home":
        setCursor(-WINDOW_S, true);
        break;
      case "End":
        setCursor(0, true);
        break;
      case " ":
      case "Enter":
        if (frozen) resume();
        else pause();
        break;
      case "Escape":
        resume();
        setCursor(null, false);
        break;
      default:
        handled = false;
    }
    if (handled) e.preventDefault();
  };

  return (
    <section className={`panel traces${frozen ? " is-paused" : ""}`} aria-labelledby="traces-title">
      <div className="traces-head">
        <h2 id="traces-title" className="panel-title">
          Last {WINDOW_S} seconds
        </h2>
        {frozen && (
          <span className="paused-note" role="status">
            Paused. Live data is still being received.
          </span>
        )}
        <div className="traces-controls">
          <label className="inline-field">
            <span>Speed scale</span>
            <select value={scalePref} onChange={(e) => setScalePref(e.target.value as ScalePref)}>
              <option value="auto">Auto (only expands)</option>
              <option value="200">0–200 km/h</option>
              <option value="300">0–300 km/h</option>
              <option value="400">0–400 km/h</option>
            </select>
          </label>
          <button type="button" className="btn" aria-pressed={!!frozen} onClick={frozen ? resume : pause}>
            {frozen ? "Back to live" : "Pause"}
          </button>
        </div>
      </div>
      <canvas
        ref={canvasRef}
        className="trace-canvas"
        tabIndex={0}
        role="slider"
        aria-label="Chart time cursor. Left and right arrows move it, Shift for 1 second steps, Space pauses, Escape returns to live."
        aria-valuemin={-WINDOW_S}
        aria-valuemax={0}
        aria-valuenow={cursorOn ? (st.current.cursor ?? 0) : 0}
        aria-valuetext={cursorOn ? ariaText : "No moment selected"}
        onKeyDown={onKey}
        onPointerMove={(e) => {
          if (e.pointerType === "mouse" || e.buttons) setCursor(plotOffset(e.clientX), false);
        }}
        onPointerLeave={() => {
          if (!frozen) setCursor(null, false);
        }}
        onClick={(e) => {
          if (plotOffset(e.clientX) === null) return;
          if (!frozen) pause();
        }}
      />
      <div className={`trace-readout${cursorOn ? "" : " is-empty"}`}>
        {cursorOn ? (
          <dl>
            {(
              [
                ["time", "At"],
                ["speed", "Speed"],
                ["rpm", "RPM"],
                ["throttle", "Throttle"],
                ["brake", "Brake"],
                ["steer", "Steering"],
              ] as const
            ).map(([k, label]) => (
              <div key={k} className={`ro ro-${k}`}>
                <dt>{label}</dt>
                <dd className="num" ref={(el) => void (valueRefs.current[k] = el)}>
                  {DASH}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="muted small">Point at the chart, or focus it and use the arrow keys, to read the values at a moment. Click or press Space to pause.</p>
        )}
      </div>
    </section>
  );
}
