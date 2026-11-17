// Replay of a saved recording, inside the same cockpit panels as Live. The live stream
// keeps running in the background (and a recording in progress keeps recording).
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { fmt } from "../format";
import { RATES, ReplayPlayer, browserReplayEnv, clockText, type Rate, type ReplayState } from "../replay";
import type { Snapshot } from "../stream";
import type { RecordingDetail } from "../types";
import type { ViewState } from "../viewstate";
import { Live } from "./Live";
import { OV_PAD_L, drawOverview, type Overview } from "./Sessions";
import type { TraceSource } from "./Traces";

const IDLE: ReplayState = {
  position: 0,
  duration: 0,
  playing: false,
  rate: 1,
  frame: null,
  sampleIndex: null,
  inGap: false,
  empty: false,
  loading: true,
  error: null,
  ended: false,
  generation: 0,
};
const noop = () => () => undefined;

function when(utc: string): string {
  const d = new Date(utc);
  return Number.isNaN(d.getTime()) ? utc : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function Timeline({ id, state, events, onSeek, onToggle }: {
  id: string;
  state: ReplayState;
  events: RecordingDetail["events"];
  onSeek: (t: number) => void;
  onToggle: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  const [ov, setOv] = useState<Overview | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`/api/v1/recordings/${encodeURIComponent(id)}/overview?points=800`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => alive && d && setOv(d))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [id]);
  useEffect(() => {
    const c = canvas.current;
    if (!c || !ov) return;
    const draw = () => drawOverview(c, ov);
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [ov]);

  const d = state.duration || 1;
  const pct = (t: number) => `calc(${OV_PAD_L}px + (100% - ${OV_PAD_L}px) * ${Math.min(1, Math.max(0, t / d))})`;
  const fromPointer = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    const f = (clientX - r.left - OV_PAD_L) / Math.max(1, r.width - OV_PAD_L);
    return Math.min(1, Math.max(0, f)) * d;
  };
  const onKey = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 1 : 5;
    const keys: Record<string, () => void> = {
      ArrowLeft: () => onSeek(state.position - step),
      ArrowRight: () => onSeek(state.position + step),
      Home: () => onSeek(0),
      End: () => onSeek(d),
      " ": onToggle,
    };
    const fn = keys[e.key];
    if (fn) {
      e.preventDefault();
      fn();
    }
  };
  const marks = events.filter((e) => e.elapsed_s !== null).slice(0, 200);
  return (
    <div
      ref={box}
      className="timeline"
      role="slider"
      tabIndex={0}
      aria-label="Replay position. Left and right arrows move 5 seconds, Shift for 1 second, Home and End jump to the ends, Space plays or pauses."
      aria-valuemin={0}
      aria-valuemax={Math.round(d)}
      aria-valuenow={Math.round(state.position)}
      aria-valuetext={`${clockText(state.position)} of ${clockText(d)}`}
      onKeyDown={onKey}
      onPointerDown={(e) => {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        onSeek(fromPointer(e.clientX));
      }}
      onPointerMove={(e) => {
        if (e.buttons) onSeek(fromPointer(e.clientX));
      }}
    >
      <canvas ref={canvas} className="timeline-canvas" aria-hidden="true" />
      {marks.map((e, i) => (
        <span key={i} className={`timeline-mark mark-${e.type}`} style={{ left: pct(e.elapsed_s ?? 0) }} title={`${clockText(e.elapsed_s ?? 0)} ${e.type.replace(/_/g, " ")} ${e.detail}`} />
      ))}
      <span className="timeline-head" style={{ left: pct(state.position) }} aria-hidden="true" />
    </div>
  );
}

export function Replay({ id, liveSnap, focus, onClose, startAt = 0 }: { id: string; liveSnap: Snapshot; focus: boolean; onClose: () => void; startAt?: number }) {
  const [detail, setDetail] = useState<RecordingDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`/api/v1/recordings/${encodeURIComponent(id)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`server answered ${r.status}`))))
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setLoadError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [id]);

  const duration = detail ? (detail.duration_s ?? (detail.chunks.length ? detail.chunks[detail.chunks.length - 1].t_end_s : 0)) : 0;
  const player = useMemo(() => (detail ? new ReplayPlayer(id, duration, browserReplayEnv(id)) : null), [detail, id, duration]);
  useEffect(() => {
    if (!player) return;
    player.seek(startAt);
    return () => player.dispose();
  }, [player, startAt]);
  const state = useSyncExternalStore(player?.subscribe ?? noop, player?.getSnapshot ?? (() => IDLE));
  const source = useMemo<TraceSource | undefined>(
    () => (player ? { kind: "replay", history: player.history, now: player.now, subscribe: player.subscribe } : undefined),
    [player],
  );

  const toggle = () => (state.playing ? player?.pause() : player?.play());
  const seek = (t: number) => player?.seek(t);

  if (loadError) {
    return (
      <div className="empty">
        <h1>This recording could not be opened</h1>
        <p className="error-text">{loadError}</p>
        <button type="button" className="btn" onClick={onClose}>
          Back to Sessions
        </button>
      </div>
    );
  }

  const snap: Snapshot = { ...liveSnap, frame: state.frame, raw: null, telemetryEmpty: state.empty };
  const view: ViewState = {
    kind: state.inGap ? "stale" : state.empty ? "no_data" : "receiving",
    demo: detail?.mode === "demo",
    dim: state.inGap || state.empty,
    hasFrame: !!state.frame,
    warning: null,
    label: "Replay",
  };
  const note = state.error
    ? null
    : state.loading && !state.frame
      ? "Loading this part of the recording…"
      : state.inGap
        ? "Gap in the recording. Showing the last values."
        : state.empty
          ? "No driving data at this point."
          : state.ended
            ? "End of the recording."
            : null;

  return (
    <div className="replay">
      <section className="replay-bar" aria-label="Replay controls">
        <div className="replay-title">
          <span className="mode-badge mode-replay">Replay</span>
          <strong>{detail?.title || (detail ? when(detail.started_utc) : "…")}</strong>
          {detail?.mode === "demo" && <span className="tag">demo recording</span>}
          <button type="button" className="btn btn-small btn-quiet replay-close" onClick={onClose}>
            Close replay
          </button>
        </div>
        <div className="replay-transport">
          <button type="button" className="btn replay-play" onClick={toggle} disabled={!player} aria-label={state.playing ? "Pause replay" : "Play replay"}>
            {state.playing ? "Pause" : state.ended ? "Play again" : "Play"}
          </button>
          <span className="rate" role="group" aria-label="Playback speed">
            {RATES.map((r) => (
              <button key={r} type="button" className="btn btn-small btn-quiet" aria-pressed={state.rate === r} onClick={() => player?.setRate(r as Rate)}>
                {r}×
              </button>
            ))}
          </span>
          <span className="replay-time num" aria-live="off">
            {clockText(state.position, true)} <span className="muted">/ {clockText(state.duration, true)}</span>
          </span>
          {detail && detail.events.length > 0 && (
            <label className="inline-field">
              <span>Jump to</span>
              <select
                value=""
                onChange={(e) => {
                  const t = Number(e.target.value);
                  if (Number.isFinite(t)) seek(Math.max(0, t - 2));
                }}
              >
                <option value="">event…</option>
                {detail.events
                  .filter((e) => e.elapsed_s !== null)
                  .slice(0, 200)
                  .map((e, i) => (
                    <option key={i} value={e.elapsed_s ?? 0}>
                      {clockText(e.elapsed_s ?? 0)} {e.type.replace(/_/g, " ")} {e.detail}
                    </option>
                  ))}
              </select>
            </label>
          )}
          <span className="replay-meta muted small num">
            {state.sampleIndex !== null && <>sample #{state.sampleIndex.toLocaleString()}</>}
            {state.frame && <> · game time {fmt(state.frame.game_timestamp_ms)} ms</>}
          </span>
        </div>
        {player && <Timeline id={id} state={state} events={detail?.events ?? []} onSeek={seek} onToggle={toggle} />}
        <p className="replay-note small" role="status">
          {note}
          {state.error && (
            <span className="text-danger">
              {state.error}{" "}
              <button type="button" className="btn btn-small" onClick={() => player?.retry()}>
                Try again
              </button>
            </span>
          )}
        </p>
      </section>
      {state.frame ? (
        <Live snap={snap} view={view} focus={focus} traceSource={source} traceLive={state.playing} controls={false} />
      ) : (
        !state.loading && !state.error && <p className="muted pad">No samples at this position.</p>
      )}
    </div>
  );
}
