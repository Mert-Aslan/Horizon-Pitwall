// Sessions: find and open saved recordings. Replay and analysis are later milestones; this
// page only offers what works today (list, summary, labels/notes, integrity check, speed
// overview).
import { useCallback, useEffect, useRef, useState } from "react";
import { fmt } from "../format";
import { usePref } from "../prefs";
import type { Snapshot } from "../stream";
import type { Recording, RecordingDetail } from "../types";
import { Runs } from "./Runs";
import { clock } from "./TopBar";

const STATUS_TEXT: Record<Recording["status"], string> = {
  recording: "recording",
  completed: "saved",
  failed: "failed",
  recovered: "recovered after a crash",
  incomplete: "incomplete",
};

function when(utc: string): string {
  const d = new Date(utc);
  return Number.isNaN(d.getTime()) ? utc : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

async function getJSON<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, { cache: "no-store" });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

function useRecordings(active: boolean, nonce: number) {
  const [list, setList] = useState<Recording[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      const d = await getJSON<{ recordings: Recording[] }>("/api/v1/recordings");
      if (!alive) return;
      if (d) {
        setList(d.recordings);
        setFailed(false);
      } else setFailed(true);
    };
    void load();
    const id = window.setInterval(() => void load(), active ? 2000 : 10_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [active, nonce]);
  return { list, failed };
}

/** left gutter of the overview plot (speed labels); the timeline aligns its playhead to it */
export const OV_PAD_L = 34;

export interface Overview {
  duration_s: number;
  bucket_s: number;
  speed_kmh_min: (number | null)[];
  speed_kmh_max: (number | null)[];
  samples_read: number;
}

function SpeedOverview({ id, samples }: { id: string; samples: number }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const [ov, setOv] = useState<Overview | null>(null);
  useEffect(() => {
    let alive = true;
    setOv(null);
    void getJSON<Overview>(`/api/v1/recordings/${encodeURIComponent(id)}/overview?points=600`).then((d) => alive && setOv(d));
    return () => {
      alive = false;
    };
  }, [id, samples]);
  useEffect(() => {
    const c = ref.current;
    if (!c || !ov) return;
    const draw = () => drawOverview(c, ov);
    draw();
    const ro = new ResizeObserver(draw); // redraw at the new size (no stretched text)
    ro.observe(c);
    return () => ro.disconnect();
  }, [ov]);
  return (
    <figure className="overview">
      <canvas ref={ref} className="overview-canvas" role="img" aria-label={ov ? `Speed over the whole recording, up to ${fmt(Math.max(0, ...ov.speed_kmh_max.map((v) => v ?? 0)))} km/h; empty stretches are gaps without data` : "Loading speed overview"} />
      <figcaption className="muted small">
        Speed (km/h), lowest–highest per {ov ? (ov.bucket_s < 1 ? `${fmt(ov.bucket_s * 1000)} ms` : `${fmt(ov.bucket_s, 1)} s`) : "…"} · empty stretches have no data
      </figcaption>
    </figure>
  );
}

export function drawOverview(c: HTMLCanvasElement, ov: Overview): void {
  {
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth;
    const h = c.clientHeight;
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    const ctx = c.getContext("2d");
    if (!ctx || w === 0) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const css = getComputedStyle(c);
    const line = css.getPropertyValue("--line").trim() || "#2c3136";
    const speed = css.getPropertyValue("--data-speed").trim() || "#dcdfe2";
    const text3 = css.getPropertyValue("--text-3").trim() || "#858d95";
    ctx.clearRect(0, 0, w, h);
    const top = Math.max(100, ...ov.speed_kmh_max.filter((v): v is number => v !== null));
    const max = Math.ceil(top / 50) * 50;
    ctx.strokeStyle = line;
    ctx.fillStyle = text3;
    ctx.font = "11px system-ui, sans-serif";
    ctx.lineWidth = 1;
    for (const v of [0, max / 2, max]) {
      const y = Math.round(h - 14 - (v / max) * (h - 24)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(OV_PAD_L, y);
      ctx.lineTo(w, y);
      ctx.stroke();
      ctx.fillText(String(v), 0, y + 4);
    }
    const n = ov.speed_kmh_max.length;
    const bw = (w - OV_PAD_L) / n;
    ctx.fillStyle = speed;
    for (let i = 0; i < n; i++) {
      const lo = ov.speed_kmh_min[i];
      const hi = ov.speed_kmh_max[i];
      if (lo === null || hi === null) continue; // gap stays empty
      const y1 = h - 14 - (hi / max) * (h - 24);
      const y2 = h - 14 - (lo / max) * (h - 24);
      ctx.fillRect(OV_PAD_L + i * bw, y1, Math.max(1, bw), Math.max(1.5, y2 - y1));
    }
    ctx.fillStyle = text3;
    ctx.fillText("0:00", OV_PAD_L, h - 1);
    const end = clock(ov.duration_s);
    ctx.fillText(end, w - ctx.measureText(end).width, h - 1);
  }
}

function Detail({ id, onChanged, onReplay, onAnalyze }: { id: string; onChanged: () => void; onReplay?: (id: string) => void; onAnalyze?: (id: string) => void }) {
  const [d, setD] = useState<RecordingDetail | null>(null);
  const [form, setForm] = useState({ title: "", car_label: "", setup_label: "", notes: "" });
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [verify, setVerify] = useState<{ ok: boolean; samples_in_files: number; problems: string[] } | null>(null);
  const [checking, setChecking] = useState(false);

  const load = useCallback(async () => {
    const r = await getJSON<RecordingDetail>(`/api/v1/recordings/${encodeURIComponent(id)}`);
    setD(r);
    if (r) setForm({ title: r.title ?? "", car_label: r.car_label ?? "", setup_label: r.setup_label ?? "", notes: r.notes ?? "" });
  }, [id]);

  useEffect(() => {
    setVerify(null);
    setSaveMsg(null);
    void load();
  }, [load]);

  if (!d) return <p className="muted pad">Loading…</p>;

  const save = async () => {
    setSaveMsg(null);
    try {
      const r = await fetch(`/api/v1/recordings/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      setSaveMsg(r.ok ? "Saved." : r.status === 403 ? "This device may only view." : `Not saved (server answered ${r.status}).`);
      if (r.ok) {
        await load();
        onChanged();
      }
    } catch {
      setSaveMsg("Not saved: the server could not be reached.");
    }
  };
  const check = async () => {
    setChecking(true);
    setVerify(await getJSON(`/api/v1/recordings/${encodeURIComponent(id)}/verify`));
    setChecking(false);
  };
  const s = d.summary;
  const cars = s ? Object.entries(s.cars_confirmed) : [];
  return (
    <article className="session-detail" aria-labelledby="session-title">
      <header className="session-head">
        <h2 id="session-title">{d.title || when(d.started_utc)}</h2>
        <span className={`tag${d.status === "failed" || d.status === "incomplete" ? " tag-danger" : ""}`}>{STATUS_TEXT[d.status]}</span>
        {d.mode === "demo" && <span className="mode-badge">Demo</span>}
        {onReplay && d.samples > 0 && (
          <button type="button" className="btn session-replay" onClick={() => onReplay(d.id)}>
            Replay
          </button>
        )}
        {onAnalyze && d.samples > 0 && (
          <button type="button" className="btn session-analyze" onClick={() => onAnalyze(d.id)}>
            Analyze
          </button>
        )}
      </header>
      {d.status === "recording" && <p className="note">Still recording. Figures below grow every 30 s as chunks are saved.</p>}
      {d.error && <p className="error-text small">{d.error}</p>}
      {d.samples > 0 && <SpeedOverview id={d.id} samples={d.samples} />}
      <div className="session-cols">
        <dl className="kv">
          {(
            [
              ["Started", when(d.started_utc)],
              ["Duration", d.duration_s !== null ? clock(d.duration_s) : "—"],
              ["Samples saved", d.samples.toLocaleString()],
              ["Dropped (queue full)", d.dropped],
              ["Lost when stopping", d.lost_on_stop],
              ["Stream segments", d.segments],
              ["Data coverage", s?.coverage !== null && s?.coverage !== undefined ? `${fmt(s.coverage * 100, 1)} %` : "—"],
              ["Gaps over 250 ms", s ? `${s.gaps_over_250ms} (${fmt(s.gap_time_s, 1)} s)` : "—"],
              ["Packets without driving data", s?.empty_samples !== undefined ? s.empty_samples.toLocaleString() : "not counted (older recording)"],
              ["Top speed", s?.speed_kmh_max !== null && s?.speed_kmh_max !== undefined ? `${fmt(s.speed_kmh_max, 1)} km/h` : "—"],
              ["Average speed", s?.speed_kmh_mean !== null && s?.speed_kmh_mean !== undefined ? `${fmt(s.speed_kmh_mean, 1)} km/h` : "—"],
              ["Cars (confirmed id: samples)", cars.length ? cars.map(([k, v]) => `#${k}: ${v}`).join(", ") : "none confirmed"],
              ["Stopped because", d.stop_reason ? d.stop_reason.replace(/_/g, " ") : "—"],
              ["Format", `${d.chunk_format}, ${d.decoder}, app ${d.app_version}`],
            ] as [string, string | number][]
          ).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd className="num">{v}</dd>
            </div>
          ))}
        </dl>
        <form
          className="session-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {(
            [
              ["title", "Title"],
              ["car_label", "Car (your label)"],
              ["setup_label", "Setup / tune (your label)"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="field">
              <span>{label}</span>
              <input value={form[k]} maxLength={120} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
            </label>
          ))}
          <label className="field">
            <span>Notes</span>
            <textarea rows={3} value={form.notes} maxLength={2000} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </label>
          <p className="muted small">The game does not send tuning settings; these labels are what you type.</p>
          <div className="row-actions">
            <button type="submit" className="btn btn-small">
              Save labels
            </button>
            <span className="small" role="status">
              {saveMsg}
            </span>
          </div>
        </form>
      </div>
      <section aria-labelledby="integrity-title">
        <h3 id="integrity-title" className="sub-title">
          Integrity
        </h3>
        <div className="row-actions">
          <button type="button" className="btn btn-small btn-quiet" onClick={() => void check()} disabled={checking}>
            {checking ? "Checking…" : "Check files"}
          </button>
          <span className="small" role="status">
            {verify &&
              (verify.ok
                ? `All ${d.chunks.length} chunk files match their checksums; ${verify.samples_in_files.toLocaleString()} samples read back.`
                : `Problems: ${verify.problems.join("; ")}`)}
          </span>
        </div>
      </section>
      {d.events.length > 0 && (
        <section aria-labelledby="sess-events-title">
          <h3 id="sess-events-title" className="sub-title">
            Events during the recording
          </h3>
          <ol className="events">
            {d.events.slice(0, 40).map((e, i) => (
              <li key={i}>
                <span className="mono muted">{e.elapsed_s !== null ? clock(e.elapsed_s) : "—"}</span> {e.type.replace(/_/g, " ")} <span className="muted">{e.detail}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}

export function Sessions(props: { snap: Snapshot; onReplay?: (id: string) => void; onAnalyze?: (id: string) => void }) {
  const [view, setView] = usePref("sessionsView", ["recordings", "runs"] as const, "recordings");
  return (
    <>
      <div className="seg" role="group" aria-label="Sessions view">
        <button type="button" className="btn btn-small btn-quiet" aria-pressed={view === "recordings"} onClick={() => setView("recordings")}>
          Recordings
        </button>
        <button type="button" className="btn btn-small btn-quiet" aria-pressed={view === "runs"} onClick={() => setView("runs")}>
          Runs &amp; comparison
        </button>
      </div>
      {view === "runs" ? <Runs /> : <Recordings {...props} />}
    </>
  );
}

function Recordings({ snap, onReplay, onAnalyze }: { snap: Snapshot; onReplay?: (id: string) => void; onAnalyze?: (id: string) => void }) {
  const rec = snap.status?.recorder;
  const active = rec?.state === "recording" || rec?.state === "stopping";
  const [nonce, setNonce] = useState(0);
  const { list, failed } = useRecordings(active, nonce + (rec?.state === "completed" ? 1 : 0));
  const [sel, setSel] = useState<string | null>(null);
  const selected = sel ?? list?.[0]?.id ?? null;

  if (list === null) return <p className="muted pad">{failed ? "Recordings could not be loaded from the server." : "Loading recordings…"}</p>;
  if (list.length === 0) {
    return (
      <div className="empty">
        <h1>No recordings yet</h1>
        <p className="muted">
          Press <strong>Record</strong> in the top bar before you drive. Pitwall saves every accepted packet to this PC until you press Stop, even
          if this page is closed. Recordings appear here.
        </p>
      </div>
    );
  }
  return (
    <div className="sessions">
      <section className="panel session-list" aria-labelledby="sessions-title">
        <h2 id="sessions-title" className="panel-title">
          Recordings <span className="muted small">{list.length} on this PC</span>
        </h2>
        <ul>
          {list.map((r) => (
            <li key={r.id}>
              <button type="button" className="session-row" aria-current={r.id === selected ? "true" : undefined} onClick={() => setSel(r.id)}>
                <span className="session-row-title">
                  {r.title || when(r.started_utc)}
                  {r.mode === "demo" && <span className="tag">demo</span>}
                  {r.status !== "completed" && <span className={`tag${r.status === "failed" || r.status === "incomplete" ? " tag-danger" : ""}`}>{STATUS_TEXT[r.status]}</span>}
                </span>
                <span className="session-row-meta num">
                  {r.title ? `${when(r.started_utc)} · ` : ""}
                  {r.duration_s !== null ? clock(r.duration_s) : "—"} · {r.samples.toLocaleString()} samples
                  {r.summary?.speed_kmh_max !== null && r.summary?.speed_kmh_max !== undefined ? ` · top ${fmt(r.summary.speed_kmh_max)} km/h` : ""}
                  {r.car_label ? ` · ${r.car_label}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className="panel">{selected && <Detail key={selected} id={selected} onChanged={() => setNonce((n) => n + 1)} onReplay={onReplay} onAnalyze={onAnalyze} />}</section>
    </div>
  );
}
