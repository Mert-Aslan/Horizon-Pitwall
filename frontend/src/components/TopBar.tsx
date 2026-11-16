import { useState } from "react";
import { ageText, fmt, isNum } from "../format";
import { setRecording, type Snapshot } from "../stream";
import type { ViewState } from "../viewstate";

export type Tab = "live" | "sessions" | "diagnostics";
export const TABS: readonly Tab[] = ["live", "sessions", "diagnostics"];
const TAB_LABEL: Record<Tab, string> = { live: "Live", sessions: "Sessions", diagnostics: "Diagnostics" };

interface Props {
  snap: Snapshot;
  view: ViewState;
  /** null while a replay is open (no tab is current) */
  tab: Tab | null;
  /** a recording is open in Replay or Lab (the top bar then describes the live link only) */
  overlay?: "replay" | "lab" | null;
  onTab: (t: Tab) => void;
  focus: boolean;
  onFocus: (on: boolean) => void;
  demoOrdinal: number | null;
  recordingAvailable: boolean;
}

/** What to call the car. The raw CarOrdinal is never altered; the server's identity
 *  tracker says whether the id is confirmed, still being checked or currently not sent
 *  (the game sends 0 for short periods, finding D-2). */
export function carIdentityText(snap: Snapshot, demoOrdinal: number | null): { name: string; tag: string; title: string } | null {
  const f = snap.frame;
  if (!f) return null;
  const ci = f.car_identity;
  const raw = f.normalized.car.ordinal;
  if (demoOrdinal !== null && raw === demoOrdinal) return { name: "Demo car", tag: "synthetic", title: "Synthetic demo data" };
  if (!ci) return { name: `Car #${fmt(raw)}`, tag: "name not known yet", title: `CarOrdinal ${fmt(raw)}` };
  switch (ci.status) {
    case "confirmed":
      return { name: `Car #${ci.confirmed_ordinal}`, tag: "name not known yet", title: `CarOrdinal ${ci.confirmed_ordinal}: no name is known for this id yet` };
    case "temporarily_unavailable":
      return {
        name: `Car #${ci.confirmed_ordinal}`,
        tag: "id not sent right now",
        title: `The game is currently sending CarOrdinal ${fmt(raw)}; showing the last confirmed id ${ci.confirmed_ordinal}, not live-verified`,
      };
    case "candidate":
      return { name: `Car #${ci.candidate_ordinal}`, tag: "checking id", title: "A new CarOrdinal is being confirmed" };
    case "unavailable":
      return { name: "No car id", tag: "not sent", title: `The game has not sent a car id for a while (last confirmed: ${ci.confirmed_ordinal ?? "none"})` };
    default:
      return { name: "Car not identified yet", tag: "waiting for id", title: `CarOrdinal ${fmt(raw)}` };
  }
}

export function CarIdentity({ snap, demoOrdinal }: { snap: Snapshot; demoOrdinal: number | null }) {
  const car = snap.frame?.normalized.car;
  const id = carIdentityText(snap, demoOrdinal);
  if (!car || !id) return <span className="car-id muted">No car yet</span>;
  return (
    <span className="car-id" title={id.title}>
      <span className="car-name">{id.name}</span>
      <span className="tag tag-neutral">{id.tag}</span>
      <span className="car-meta">
        <span>
          <span className="meta-k">Class</span> {car.class_label ?? (isNum(car.class_code) ? car.class_code : "—")}
        </span>
        <span>
          <span className="meta-k">PI</span> {fmt(car.pi)}
        </span>
        <span>{car.drivetrain_label ?? "—"}</span>
        <span>{isNum(car.cylinders) ? `${car.cylinders} cyl` : "— cyl"}</span>
      </span>
    </span>
  );
}

export function clock(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return "0:00";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** Record / stop. The button mirrors the server's recorder state from the stream; a click
 *  never marks itself as recording, it waits for the server to say so. */
function RecordControl({ snap }: { snap: Snapshot }) {
  const rec = snap.status?.recorder ?? null;
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!rec || snap.link !== "open") return null;
  const active = rec.state === "recording" || rec.state === "starting" || rec.state === "stopping";
  const click = async () => {
    setPending(true);
    setErr(null);
    try {
      await setRecording(!active);
    } catch (e) {
      setErr(`Could not ${active ? "stop" : "start"} recording: ${(e as Error).message}`);
    } finally {
      setPending(false);
    }
  };
  return (
    <span className={`rec${active ? " rec-on" : ""}`}>
      <button
        type="button"
        className={`btn btn-small rec-btn${active ? " rec-btn-on" : ""}`}
        onClick={() => void click()}
        disabled={pending || rec.state === "stopping" || rec.state === "starting"}
        aria-pressed={active}
        title={active ? "Stop and save the recording" : `Record ${snap.status?.mode === "demo" ? "the demo stream (labelled as demo)" : "the live stream"} to this PC`}
      >
        <span className="rec-dot" aria-hidden="true" />
        {rec.state === "stopping" ? "Saving…" : active ? "Stop" : "Record"}
      </button>
      {active && (
        <span className="rec-meta num" role="status" aria-live="off">
          {clock(rec.elapsed_s)} · {rec.samples.toLocaleString()} samples
          {rec.dropped > 0 && <span className="text-danger"> · {rec.dropped} dropped</span>}
        </span>
      )}
      {rec.state === "failed" && (
        <span className="rec-meta text-danger" role="alert">
          Recording failed
        </span>
      )}
      {err && (
        <span className="rec-meta text-danger" role="alert">
          {err}
        </span>
      )}
    </span>
  );
}

export function TopBar({ snap, view, tab, onTab, focus, onFocus, demoOrdinal, recordingAvailable, overlay = null }: Props) {
  const replay = overlay !== null;
  const st = snap.status;
  const tabs = TABS.filter((t) => t !== "sessions" || recordingAvailable);
  return (
    <header className="topbar">
      <div className="brand" aria-label="Horizon Pitwall">
        <svg viewBox="0 0 32 32" width="20" height="20" aria-hidden="true">
          <rect x="5" y="19" width="4" height="7" fill="currentColor" opacity=".55" />
          <rect x="11" y="15" width="4" height="11" fill="currentColor" opacity=".55" />
          <rect x="17" y="10" width="4" height="16" fill="currentColor" />
          <rect x="23" y="6" width="4" height="20" className="brand-accent" />
        </svg>
        <span>Horizon Pitwall</span>
      </div>
      <nav className="tabs" aria-label="Views">
        {tabs.map((t) => (
          <button key={t} className="tab" aria-current={tab === t ? "page" : undefined} onClick={() => onTab(t)}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </nav>
      <div className="topbar-car">
        {replay ? <span className="car-id muted">{overlay === "lab" ? "Analysing a recording" : "Replaying a recording"}</span> : <CarIdentity snap={snap} demoOrdinal={demoOrdinal} />}
      </div>
      <div className="topbar-status">
        <RecordControl snap={snap} />
        {(tab === "live" || overlay === "replay") && (
          <button type="button" className="btn btn-quiet btn-small focus-toggle" aria-pressed={focus} onClick={() => onFocus(!focus)}>
            Focus view
          </button>
        )}
        {view.demo && <span className="mode-badge">Demo</span>}
        <span className={`conn conn-${view.kind}`} role="status" aria-live="polite" title={replay ? "State of the live link (the replay does not change it)" : undefined}>
          <span className="conn-mark" aria-hidden="true" />
          {replay ? `Live: ${view.label}` : view.label}
        </span>
        <span className="conn-detail" aria-hidden={view.kind !== "receiving" && view.kind !== "no_data" && view.kind !== "stale"}>
          {(view.kind === "receiving" || view.kind === "no_data") && st && (
            <>
              <span>{isNum(st.observed_hz) ? `${fmt(st.observed_hz)} Hz` : "— Hz"}</span>
              <span>{ageText(st.packet_age_ms)} old</span>
            </>
          )}
          {view.kind === "stale" && st && <span>{ageText(st.packet_age_ms)} ago</span>}
        </span>
      </div>
    </header>
  );
}
