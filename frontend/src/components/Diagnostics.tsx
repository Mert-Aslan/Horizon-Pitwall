import { useEffect, useMemo, useState } from "react";
import { ageText, fmt } from "../format";
import { store, type Snapshot } from "../stream";
import { DemoButton } from "./DemoButton";
import type { Capabilities, Frame } from "../types";
import type { ViewState } from "../viewstate";

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
  duplicate_analysis?: {
    identical: number;
    changed: number;
    race_on: number;
    race_off: number;
    changed_fields_top: Record<string, number>;
    changed_samples: { is_race_on: number | null; fields: string[]; n_fields: number }[];
  };
  ui_clients: { id: number; peer: string; frames_sent: number; frames_skipped: number }[];
  notes: string[];
}

function useDiagnostics(): Diag | null {
  const [d, setD] = useState<Diag | null>(null);
  useEffect(() => {
    let alive = true;
    let ctrl: AbortController | null = null;
    const tick = async () => {
      ctrl?.abort();
      ctrl = new AbortController();
      const timeout = window.setTimeout(() => ctrl?.abort(), 2000);
      try {
        const r = await fetch("/api/v1/diagnostics", { signal: ctrl.signal, cache: "no-store" });
        if (r.ok && alive) setD(await r.json());
      } catch {
        /* shown via connection state */
      } finally {
        window.clearTimeout(timeout);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1000);
    return () => {
      alive = false;
      ctrl?.abort();
      window.clearInterval(id);
    };
  }, []);
  return d;
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

function RawTable({ caps, frame }: { caps: Capabilities | null; frame: Frame | null }) {
  const [filter, setFilter] = useState("");
  const raw = frame?.raw;
  const quality = frame?.quality.fields;
  const body = useMemo(() => {
    if (!caps) return null;
    const f = filter.trim().toLowerCase();
    return caps.schema.fields
      .filter((s) => !f || s.name.toLowerCase().includes(f))
      .map((s) => {
        const q = quality?.[s.name];
        const v = raw ? raw[s.name] : undefined;
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caps, raw, filter]);

  return (
    <section className="panel inspector" aria-labelledby="inspector-title">
      <div className="inspector-head">
        <h2 id="inspector-title" className="panel-title">
          Raw packet inspector <span className="muted small">full precision, refreshed ~4×/s</span>
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
        {!raw && <p className="muted pad">No packet decoded yet.</p>}
      </div>
    </section>
  );
}

export function Diagnostics({ snap, view, caps }: { snap: Snapshot; view: ViewState; caps: Capabilities | null }) {
  const d = useDiagnostics();
  useEffect(() => {
    store.setRawSubscription(true);
    return () => store.setRawSubscription(false);
  }, []);
  const st = snap.status;
  const c = d?.counters ?? {};
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
            ["Session", st?.session_id],
            ["Last valid packet", ageText(st?.packet_age_ms)],
            ["Observed rate", st?.observed_hz ? `${fmt(st.observed_hz, 1)} Hz` : null],
            ["Game Δt (avg)", d?.game_dt_ms_ema ? `${fmt(d.game_dt_ms_ema, 1)} ms` : null],
            ["Server uptime", d ? `${fmt(d.uptime_s)} s` : null],
            ["Schema", caps ? `${caps.schema.id} v${caps.schema.version}, ${caps.schema.documented_packet_length} bytes` : null],
            ["App", caps?.app_version],
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
            ["Rejected", c.rejected ?? 0],
            ["Same timestamp (rejected)", c.duplicates ?? 0],
            ["… identical bytes", d?.duplicate_analysis?.identical ?? 0],
            ["… changed content", d?.duplicate_analysis?.changed ?? 0],
            ["… while IsRaceOn = 0", d?.duplicate_analysis?.race_off ?? 0],
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
        {d?.duplicate_analysis && d.duplicate_analysis.changed > 0 && (
          <>
            <h3 className="sub-title">Fields that changed under an unchanged timestamp</h3>
            <p className="mono small">
              {Object.entries(d.duplicate_analysis.changed_fields_top)
                .map(([k, v]) => `${k}: ${v}`)
                .join(", ")}
            </p>
          </>
        )}
        <h3 className="sub-title">Lengths seen (bytes: count)</h3>
        <p className="mono">
          {d && Object.keys(d.length_histogram).length
            ? Object.entries(d.length_histogram)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")
            : "—"}
        </p>
        <h3 className="sub-title">Trailing byte 324 (hex: count)</h3>
        <p className="mono">
          {d && Object.keys(d.tail_values_hex).length
            ? Object.entries(d.tail_values_hex)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")
            : "—"}
        </p>
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
            <h3 className="sub-title">Ignored senders</h3>
            <ul className="events">
              {Object.entries(d.other_sources).map(([k, v]) => (
                <li key={k} className="mono small">
                  {k}: {v.packets} packets
                </li>
              ))}
            </ul>
          </>
        )}
        {caps && (
          <>
            <h3 className="sub-title">Protocol verification</h3>
            <KV rows={Object.entries(caps.verification).map(([k, v]) => [k.replace(/_/g, " "), v])} />
          </>
        )}
      </section>

      <RawTable caps={caps} frame={snap.frame} />
    </div>
  );
}
