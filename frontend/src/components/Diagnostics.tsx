import { useEffect, useMemo, useState } from "react";
import { ageText, fmt } from "../format";
import { store, type Snapshot } from "../stream";
import { DemoButton } from "./DemoButton";
import type { Capabilities, Frame, RawSample, RecorderStatus } from "../types";
import type { ViewState } from "../viewstate";

interface CaptureSummary {
  file: string;
  started_utc: string;
  duration_s: number;
  stop_reason: string;
  datagrams: number;
  outcomes: Record<string, number>;
  equal_timestamp: { identical: number; tail_only: number; non_finite: number; changed: number };
  equal_timestamp_race_off: number;
  changed_fields_top: Record<string, number>;
  bytes_on_disk: number;
}

interface CaptureState {
  state: "idle" | "capturing" | "writing" | "error";
  datagrams: number | null;
  remaining_s: number | null;
  error: string | null;
}

interface Diag {
  uptime_s: number;
  counters: Record<string, number>;
  reject_reasons: Record<string, number>;
  length_histogram: Record<string, number>;
  tail_values_hex: Record<string, number>;
  other_sources: Record<string, { packets: number }>;
  rejected_samples: { reason: string; length: number; source: string; head_hex: string }[];
  events: { t_wall: number; type: string; detail: string }[];
  game_dt_ms_ema: number | null;
  segments_started?: number;
  equal_timestamp_policy?: string;
  car_identity?: { status: string; confirmed_ordinal: number | null; zero_periods: number };
  duplicate_analysis?: {
    identical: number;
    tail_only?: number;
    non_finite?: number;
    changed: number;
    race_on: number;
    race_off: number;
    accepted_under_policy?: number;
    changed_fields_top: Record<string, number>;
  };
  ui_clients: { id: number; peer: string; frames_sent: number; ui_rate_limited_frames?: number; flow_withheld_frames?: number; slow_sends?: number; send_ms_max?: number }[];
  stream?: { ws_send_timeouts: number; ws_closed: number; ws_errors: number; asyncio_close_resets: number };
  recorder?: RecorderStatus & { mode_mismatch: number; flush_ms_max: number; chunks_written: number };
  build?: { build_id: string; server_instance_id: string; started_at_utc: string; ui: { ui_version: string; ui_build_id: string } | null };
  paths?: { data_dir?: string; logs?: string };
  notes: string[];
}

function usePoll<T>(url: string, everyMs: number): [T | null, () => void] {
  const [d, setD] = useState<T | null>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    let ctrl: AbortController | null = null;
    const tick = async () => {
      ctrl?.abort();
      ctrl = new AbortController();
      const timeout = window.setTimeout(() => ctrl?.abort(), 2000);
      try {
        const r = await fetch(url, { signal: ctrl.signal, cache: "no-store" });
        if (r.ok && alive) setD(await r.json());
      } catch {
        /* shown via connection state */
      } finally {
        window.clearTimeout(timeout);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), everyMs);
    return () => {
      alive = false;
      ctrl?.abort();
      window.clearInterval(id);
    };
  }, [url, everyMs, nonce]);
  return [d, () => setNonce((n) => n + 1)];
}

function KV({ rows }: { rows: [string, string | number | null | undefined][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd className="num">{v === null || v === undefined || v === "" ? "—" : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

const pairs = (o: Record<string, number> | undefined) =>
  o && Object.keys(o).length
    ? Object.entries(o)
        .map(([k, v]) => `${k}: ${v}`)
        .join(" · ")
    : "—";

function RawTable({ caps, frame, raw }: { caps: Capabilities | null; frame: Frame | null; raw: RawSample | null }) {
  const [filter, setFilter] = useState("");
  const values = raw?.values;
  // Quality flags belong to the frame they were computed for; only show them when the
  // raw sample is that same frame.
  const quality = raw && frame && raw.sequence === frame.sequence ? frame.quality.fields : undefined;
  const body = useMemo(() => {
    if (!caps) return null;
    const f = filter.trim().toLowerCase();
    return caps.schema.fields
      .filter((s) => !f || s.name.toLowerCase().includes(f))
      .map((s) => {
        const q = quality?.[s.name];
        const v = values ? values[s.name] : undefined;
        return (
          <tr key={s.name} className={q && q.status !== "not_applicable" ? "row-flag" : undefined}>
            <td className="mono">{s.name}</td>
            <td className="num">{v === undefined ? "…" : v === null ? "null" : String(v)}</td>
            <td>{s.unit ?? ""}</td>
            <td className="mono muted">
              {s.type} @{s.offset}
            </td>
            <td>{q ? q.status.replace(/_/g, " ") : ""}</td>
          </tr>
        );
      });
  }, [caps, values, quality, filter]);

  return (
    <section className="panel inspector" aria-labelledby="inspector-title">
      <div className="inspector-head">
        <h2 id="inspector-title" className="panel-title">
          Raw packet inspector{" "}
          <span className="muted small">
            full precision, refreshed ~4×/s
            {raw ? ` · frame #${raw.sequence}, ${ageText(Math.round(performance.now() - raw.receivedAt))} old` : ""}
          </span>
        </h2>
        <label className="field">
          <span>Filter fields</span>
          <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="e.g. Tire" />
        </label>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Raw value</th>
              <th scope="col">Unit</th>
              <th scope="col">Type @ offset</th>
              <th scope="col">Quality</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
        {!raw && <p className="muted pad">No packet decoded in this stream yet.</p>}
      </div>
    </section>
  );
}

function CapturePanel() {
  const [data, refresh] = usePoll<{ capture: CaptureState; captures: CaptureSummary[] }>("/api/v1/diagnostics/capture", 1000);
  const [mask, setMask] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);
  const st = data?.capture;
  const busy = st?.state === "capturing" || st?.state === "writing";
  const start = async (seconds: number) => {
    setMsg(null);
    try {
      const r = await fetch("/api/v1/diagnostics/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seconds, mask_sources: mask }),
      });
      if (r.status === 403) setMsg("This device may only view; start the capture on the Pitwall PC.");
      else if (r.status === 409) setMsg("A capture is already running.");
      else if (!r.ok) setMsg(`The server answered ${r.status}.`);
    } catch {
      setMsg("The server could not be reached.");
    }
    refresh();
  };
  const last = data?.captures?.[0];
  return (
    <section className="panel" aria-labelledby="capture-title">
      <h2 id="capture-title" className="panel-title">Diagnostic capture</h2>
      <p className="muted small">
        Saves every datagram (accepted and rejected) for a short time, with arrival order and outcome, to the local data folder. Use it while
        driving, ideally through a menu or pause, to settle open protocol questions. Nothing is uploaded.
      </p>
      <div className="row-actions">
        <button type="button" className="btn btn-small" disabled={busy} onClick={() => void start(30)}>
          Capture 30 s
        </button>
        <button type="button" className="btn btn-small btn-quiet" disabled={busy} onClick={() => void start(60)}>
          Capture 60 s
        </button>
        <label className="check">
          <input type="checkbox" checked={mask} onChange={(e) => setMask(e.target.checked)} /> Hide sender addresses in the file
        </label>
      </div>
      <p className="small" role="status" aria-live="polite">
        {st?.state === "capturing" && `Capturing… ${st.datagrams ?? 0} datagrams, ${fmt(st.remaining_s, 0)} s left.`}
        {st?.state === "writing" && "Writing the capture file…"}
        {st?.state === "error" && <span className="text-danger">{st.error}</span>}
        {msg && <span className="text-danger">{msg}</span>}
      </p>
      {last && (
        <>
          <h3 className="sub-title">Last capture · {new Date(last.started_utc).toLocaleString()}</h3>
          <KV
            rows={[
              ["Datagrams", last.datagrams],
              ["Duration", `${fmt(last.duration_s, 1)} s (${last.stop_reason})`],
              ["Same timestamp: identical", last.equal_timestamp.identical],
              ["… only byte 324 differs", last.equal_timestamp.tail_only],
              ["… only NaN/Inf differ", last.equal_timestamp.non_finite],
              ["… new content", last.equal_timestamp.changed],
              ["… while IsRaceOn = 0", last.equal_timestamp_race_off],
              ["File", last.file],
            ]}
          />
          <p className="mono small">{pairs(last.outcomes)}</p>
          {Object.keys(last.changed_fields_top).length > 0 && <p className="mono small">changed: {pairs(last.changed_fields_top)}</p>}
        </>
      )}
    </section>
  );
}

export function Diagnostics({ snap, view, caps }: { snap: Snapshot; view: ViewState; caps: Capabilities | null }) {
  const [d] = usePoll<Diag>("/api/v1/diagnostics", 1000);
  useEffect(() => {
    store.setRawSubscription(true);
    return () => store.setRawSubscription(false);
  }, []);
  const st = snap.status;
  const c = d?.counters ?? {};
  const dup = d?.duplicate_analysis;
  const rec = d?.recorder;
  const client = d?.ui_clients?.[d.ui_clients.length - 1];
  return (
    <div className="diagnostics">
      <section className="panel" aria-labelledby="conn-title">
        <h2 id="conn-title" className="panel-title">Connection</h2>
        <KV
          rows={[
            ["State", view.label],
            ["Mode", st?.mode],
            ["UDP listen address", st?.receiver_address],
            ["Receiver", st?.receiver_state],
            ["Receiver error", st?.receiver_error],
            ["Bound sender", st?.source],
            ["Stream segment", st?.stream_segment_id ?? st?.session_id],
            ["Segments since start", d?.segments_started],
            ["Last valid packet", ageText(st?.packet_age_ms)],
            ["Observed rate", st?.observed_hz ? `${fmt(st.observed_hz, 1)} Hz` : null],
            ["Game Δt (avg)", d?.game_dt_ms_ema ? `${fmt(d.game_dt_ms_ema, 1)} ms` : null],
            ["Car identity", d?.car_identity ? `${d.car_identity.status.replace(/_/g, " ")}${d.car_identity.confirmed_ordinal !== null ? ` (#${d.car_identity.confirmed_ordinal})` : ""}` : null],
            ["Server uptime", d ? `${fmt(d.uptime_s)} s` : null],
            ["Server instance", st?.server_instance_id],
            ["Server build", d?.build?.build_id],
            ["UI served", d?.build?.ui ? `${d.build.ui.ui_version} (${d.build.ui.ui_build_id})` : "no build manifest"],
            ["Schema", caps ? `${caps.schema.id} v${caps.schema.version}, ${caps.schema.documented_packet_length} bytes` : null],
            ["Data folder", d?.paths?.data_dir],
            ["Log file", d?.paths?.logs],
          ]}
        />
        <div className="row-actions">
          <DemoButton enable={!view.demo} label={view.demo ? "Stop demo" : "Start demo"} />
        </div>
      </section>

      <section className="panel" aria-labelledby="counters-title">
        <h2 id="counters-title" className="panel-title">Packets</h2>
        <KV
          rows={[
            ["Received", c.received ?? 0],
            ["Accepted", c.accepted ?? 0],
            ["Invalid (format)", c.invalid_datagrams ?? 0],
            ["From an ignored sender", c.foreign_datagrams ?? 0],
            [`Same timestamp (${d?.equal_timestamp_policy === "accept_changed" ? "new content accepted" : "rejected"})`, (c.duplicates ?? 0) + (dup?.accepted_under_policy ?? 0)],
            ["… identical bytes", dup?.identical ?? 0],
            ["… only byte 324 differs", dup?.tail_only ?? 0],
            ["… only NaN/Inf differ", dup?.non_finite ?? 0],
            ["… new content", dup?.changed ?? 0],
            ["… while IsRaceOn = 0", dup?.race_off ?? 0],
            ["Out of order", c.out_of_order ?? 0],
            ["Timestamp resets", c.timestamp_resets ?? 0],
            ["Wraparounds", c.timestamp_wraparounds ?? 0],
            ["Estimated gaps", c.estimated_gaps ?? 0],
            ["Est. missing packets", c.estimated_missing_packets ?? 0],
            ["Interruptions (>2 s)", c.interruptions ?? 0],
            ["Frames with NaN/Inf", c.frames_with_non_finite ?? 0],
            ["Accepted with IsRaceOn = 0", c.accepted_race_off ?? 0],
            ["Accepted with CarOrdinal = 0", c.accepted_car_ordinal_zero ?? 0],
          ]}
        />
        {dup && dup.changed > 0 && (
          <>
            <h3 className="sub-title">Fields that changed under an unchanged timestamp</h3>
            <p className="mono small">{pairs(dup.changed_fields_top)}</p>
          </>
        )}
        <h3 className="sub-title">Lengths seen (bytes: count)</h3>
        <p className="mono">{pairs(d?.length_histogram)}</p>
        <h3 className="sub-title">Trailing byte 324 (hex: count)</h3>
        <p className="mono">{pairs(d?.tail_values_hex)}</p>
        {d?.notes.map((n) => (
          <p key={n} className="muted small">
            {n}
          </p>
        ))}
      </section>

      <section className="panel" aria-labelledby="events-title">
        <h2 id="events-title" className="panel-title">Events &amp; rejects</h2>
        <ol className="events">
          {d?.events
            .slice(-12)
            .reverse()
            .map((e, i) => (
              <li key={i}>
                <span className="mono muted">{new Date(e.t_wall * 1000).toLocaleTimeString()}</span> <span>{e.type.replace(/_/g, " ")}</span>{" "}
                <span className="muted">{e.detail}</span>
              </li>
            ))}
        </ol>
        {d && d.rejected_samples.length > 0 && (
          <>
            <h3 className="sub-title">Last rejected packets</h3>
            <ul className="events">
              {d.rejected_samples.map((s, i) => (
                <li key={i} className="mono small">
                  {s.reason} · {s.length} B · {s.source} · {s.head_hex.slice(0, 32)}…
                </li>
              ))}
            </ul>
          </>
        )}
        {d && Object.keys(d.other_sources).length > 0 && (
          <>
            <h3 className="sub-title">Ignored senders{c.other_sources_overflow ? ` (+${c.other_sources_overflow} not listed)` : ""}</h3>
            <ul className="events">
              {Object.entries(d.other_sources).map(([k, v]) => (
                <li key={k} className="mono small">
                  {k}: {v.packets} packets
                </li>
              ))}
            </ul>
          </>
        )}
        <h3 className="sub-title">Display stream &amp; recorder</h3>
        <KV
          rows={[
            ["Frames sent to this page", client?.frames_sent],
            ["Not sent: UI rate limit (normal)", client?.ui_rate_limited_frames],
            ["Held back: page had not caught up", client?.flow_withheld_frames],
            ["Slow sends (>250 ms)", client?.slow_sends],
            ["Send timeouts (all clients)", d?.stream?.ws_send_timeouts],
            ["Page connections closed", d?.stream?.ws_closed],
            ["… closed by an error", d?.stream?.ws_errors],
            ["Benign close resets (Windows)", d?.stream?.asyncio_close_resets],
            ["Recorder", rec?.state],
            ["Recorder queue (now / peak)", rec ? `${rec.queue} / ${rec.queue_high_watermark}` : null],
            ["Recorder dropped samples", rec?.dropped],
            ["Slowest chunk flush", rec ? `${fmt(rec.flush_ms_max, 1)} ms` : null],
          ]}
        />
        {caps && (
          <>
            <h3 className="sub-title">Protocol verification</h3>
            <KV rows={Object.entries(caps.verification).map(([k, v]) => [k.replace(/_/g, " "), v])} />
          </>
        )}
      </section>

      <CapturePanel />
      <RawTable caps={caps} frame={snap.frame} raw={snap.raw} />
    </div>
  );
}
