import { useRef, type ReactNode } from "react";
import { DASH, clamp, fmt, fmtLap, fmtPct, gearText, gripLatch, isNum, steerBar } from "../format";
import { usePref } from "../prefs";
import type { Snapshot } from "../stream";
import type { Frame, N, WheelKey } from "../types";
import type { ViewState } from "../viewstate";
import { DemoButton } from "./DemoButton";
import { Traces } from "./Traces";

const SEGMENTS = 48;
const SHIFT_WARN = 0.85;
const SHIFT_NOW = 0.95;

export function ShiftBand({ frame }: { frame: Frame | null }) {
  const n = frame?.normalized;
  const fr = frame?.derived.rpm_fraction ?? null;
  const lit = isNum(fr) ? Math.round(clamp(fr, 0, 1) * SEGMENTS) : 0;
  return (
    <section className="panel shiftband" aria-label="Engine speed">
      <div className="shift-segments" aria-hidden="true">
        {Array.from({ length: SEGMENTS }, (_, i) => {
          const pos = (i + 1) / SEGMENTS;
          const zone = pos > SHIFT_NOW ? "now" : pos > SHIFT_WARN ? "warn" : "base";
          return <span key={i} className={`seg seg-${zone}${i < lit ? " on" : ""}`} />;
        })}
      </div>
      <div className="shift-readout">
        <span className="num rpm-now">{fmt(n?.rpm)}</span>
        <span className="unit">rpm</span>
        <span className="rpm-limits">
          <span>max {fmt(n?.rpm_max)}</span>
          <span>idle {fmt(n?.rpm_idle)}</span>
        </span>
      </div>
    </section>
  );
}

function Cluster({ frame }: { frame: Frame | null }) {
  const g = gearText(frame?.normalized.gear.raw, frame?.normalized.gear.label, frame?.normalized.gear.note);
  return (
    <section className="panel cluster" aria-label="Gear and speed">
      <div className="gear-box">
        <span className="num gear-num">{g.main}</span>
        <span className="cap">{g.note ?? "gear"}</span>
      </div>
      <div className="speed-box">
        <span className="num speed-num">{fmt(frame?.derived.speed_kmh)}</span>
        <span className="cap">km/h</span>
      </div>
    </section>
  );
}

function Pedal({ label, value, tone }: { label: string; value: N; tone: "throttle" | "brake" | "neutral" }) {
  const h = isNum(value) ? clamp(value, 0, 1) * 100 : 0;
  return (
    <div className={`pedal pedal-${tone}`}>
      <div className="pedal-track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={isNum(value) ? Math.round(value * 100) : undefined}>
        <div className={`pedal-fill fill-${tone}`} style={{ height: `${h}%` }} />
      </div>
      <span className="num pedal-val">{fmtPct(value)}</span>
      <span className="label">{label}</span>
    </div>
  );
}

function MiniMeter({ label, value }: { label: string; value: N }) {
  const w = isNum(value) ? clamp(value, 0, 1) * 100 : 0;
  return (
    <div className="mini-meter">
      <span className="label">{label}</span>
      <span className="meter-track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={isNum(value) ? Math.round(value * 100) : undefined}>
        <span className="meter-fill" style={{ width: `${w}%` }} />
      </span>
      <span className="num">{fmtPct(value)}</span>
    </div>
  );
}

function Inputs({ frame, compactAux }: { frame: Frame | null; compactAux?: boolean }) {
  const [aux, setAux] = usePref("auxInputs", ["compact", "expanded"] as const, "compact");
  const expanded = aux === "expanded" && !compactAux;
  const i = frame?.normalized.inputs;
  const steer = i?.steer ?? null;
  const bar = steerBar(steer);
  const outOfRange = frame?.quality.fields["Steer"]?.status === "out_of_range";
  return (
    <section className="panel inputs" aria-labelledby="inputs-title">
      <div className="panel-head">
        <h2 id="inputs-title" className="panel-title">
          Inputs
        </h2>
        {!compactAux && (
          <button type="button" className="btn btn-quiet btn-small" aria-pressed={expanded} onClick={() => setAux(expanded ? "compact" : "expanded")}>
            {expanded ? "Compact clutch & handbrake" : "Expand clutch & handbrake"}
          </button>
        )}
      </div>
      <div className={`pedals${expanded ? " pedals-4" : ""}`}>
        <Pedal label="Throttle" value={i?.throttle ?? null} tone="throttle" />
        <Pedal label="Brake" value={i?.brake ?? null} tone="brake" />
        {expanded && (
          <>
            <Pedal label="Clutch" value={i?.clutch ?? null} tone="neutral" />
            <Pedal label="Handbrake" value={i?.handbrake ?? null} tone="neutral" />
          </>
        )}
      </div>
      <div className="steer">
        <div className="steer-head">
          <span className="label">Steering</span>
          <span className="num steer-val">{isNum(steer) ? `${steer < 0 ? "L " : steer > 0 ? "R " : ""}${fmt(Math.abs(steer) * 100)}%` : DASH}</span>
        </div>
        <div className="steer-track" role="meter" aria-label="Steering, negative is left" aria-valuemin={-100} aria-valuemax={100} aria-valuenow={isNum(steer) ? Math.round(steer * 100) : undefined}>
          <span className="steer-centre" />
          <span className="steer-fill" style={{ left: `${bar.left}%`, width: `${bar.width}%` }} />
        </div>
        <div className="steer-scale" aria-hidden="true">
          <span>Left</span>
          <span>Right</span>
        </div>
        {outOfRange && <p className="note">Raw steer is outside the documented −127…127 range ({frame?.quality.fields["Steer"]?.detail ?? "raw value"}); shown clamped.</p>}
      </div>
      {!expanded && !compactAux && (
        <div className="aux-inputs">
          <MiniMeter label="Clutch" value={i?.clutch ?? null} />
          <MiniMeter label="Handbrake" value={i?.handbrake ?? null} />
        </div>
      )}
    </section>
  );
}

const WHEELS: { key: WheelKey; label: string; name: string }[] = [
  { key: "fl", label: "FL", name: "Front left" },
  { key: "fr", label: "FR", name: "Front right" },
  { key: "rl", label: "RL", name: "Rear left" },
  { key: "rr", label: "RR", name: "Rear right" },
];

/** Latches the loss-of-grip warning per wheel with hysteresis (see gripLatch). */
function useGripLatch() {
  const state = useRef<Record<string, boolean>>({});
  return (key: string, slip: N): boolean => (state.current[key] = gripLatch(state.current[key] ?? false, slip));
}

function Chassis({ frame, compact }: { frame: Frame | null; compact?: boolean }) {
  const w = frame?.normalized.wheels;
  const latch = useGripLatch();
  const cells = WHEELS.map(({ key, label, name }) => {
    const wh = w?.[key];
    const slip = wh?.combined_slip ?? null;
    const susp = wh?.suspension_norm ?? null;
    const grip = latch(key, slip);
    const slipFill = isNum(slip) ? clamp(Math.abs(slip) / 2, 0, 1) * 100 : 0;
    return (
      <div key={key} className={`wheel wheel-${key}${grip ? " grip-loss" : ""}`} role="group" aria-label={name}>
        <div className="wheel-head">
          <span className="wheel-name">{label}</span>
          <span className="wheel-temp">
            <span className="num">{fmt(wh?.tire_temp, 1)}</span>
            <span className="cap">temp</span>
          </span>
        </div>
        {!compact && (
          <>
            <div className="meter">
              <span className="meter-label">Slip</span>
              <span className="meter-track slip-track" title="Combined slip, bar 0 to 2; the mark is 1.0, where grip is lost">
                <span className={`meter-fill${grip ? " fill-brake" : ""}${isNum(slip) && Math.abs(slip) >= 2 ? " saturated" : ""}`} style={{ width: `${slipFill}%` }} />
                <span className="slip-limit" aria-hidden="true" />
              </span>
              <span className="num meter-val">{fmt(slip, 2)}</span>
            </div>
            <div className="meter">
              <span className="meter-label">Susp</span>
              <span className="meter-track" title="Suspension compression: 0 % fully extended, 100 % fully compressed">
                <span className="meter-fill" style={{ width: `${isNum(susp) ? clamp(susp, 0, 1) * 100 : 0}%` }} />
              </span>
              <span className="num meter-val">{fmtPct(susp)}</span>
            </div>
          </>
        )}
        <div className="wheel-flags">
          {grip && <span className="tag tag-danger">⚠ grip lost</span>}
          {!compact && wh?.on_rumble_strip && <span className="tag">rumble strip</span>}
          {!compact && wh?.in_puddle && <span className="tag">puddle</span>}
          {compact && <span className="num muted small">slip {fmt(slip, 2)}</span>}
        </div>
      </div>
    );
  });
  return (
    <section className={`panel chassis${compact ? " chassis-compact" : ""}`} aria-labelledby="tyres-title">
      <div className="panel-head">
        <h2 id="tyres-title" className="panel-title">
          Tyres
        </h2>
        <details className="info">
          <summary>About these values</summary>
          <p>
            Temperature is shown exactly as the game sends it: the game does not document the unit, so no Celsius or Fahrenheit conversion is made. Slip bars run from 0 to 2; the mark is
            1.0, above which the game reports loss of grip. Suspension is compression, 0 % fully extended to 100 % fully compressed.
          </p>
        </details>
      </div>
      {compact ? (
        <div className="chassis-strip">{cells}</div>
      ) : (
        <div className="chassis-grid">
          <span className="axis-label col-l" aria-hidden="true">
            Left
          </span>
          <span className="axis-label col-r" aria-hidden="true">
            Right
          </span>
          <span className="axis-label row-f" aria-hidden="true">
            Front
          </span>
          <span className="axis-label row-r" aria-hidden="true">
            Rear
          </span>
          {cells}
        </div>
      )}
    </section>
  );
}

function Readout({ label, value, unit, sub }: { label: string; value: string; unit?: string; sub?: string }) {
  return (
    <div className="readout">
      <dt>{label}</dt>
      <dd>
        <span className="num">{value}</span>
        {unit && value !== DASH && <span className="unit">{unit}</span>}
        {sub && <span className="sub">{sub}</span>}
      </dd>
    </div>
  );
}

function OutputAndTiming({ frame }: { frame: Frame | null }) {
  const n = frame?.normalized;
  const r = n?.race;
  const lapTimes = [r?.current_lap_s, r?.last_lap_s, r?.best_lap_s];
  const hasTiming = lapTimes.some(isNum) || (isNum(r?.position) && r!.position! > 0) || (isNum(r?.lap_number) && r!.lap_number! > 0);
  const rows: [string, ReactNode][] = hasTiming
    ? [
        ["Lap", fmt(r?.lap_number)],
        ["Position", isNum(r?.position) && r!.position! > 0 ? fmt(r?.position) : DASH],
        ["Current lap", fmtLap(r?.current_lap_s)],
        ["Last lap", fmtLap(r?.last_lap_s)],
        ["Best lap", fmtLap(r?.best_lap_s)],
      ]
    : [];
  rows.push(["Driving time", fmtLap(r?.race_time_s)], ["Distance", isNum(n?.distance_m) ? `${fmt(n!.distance_m! / 1000, 2)} km` : DASH]);
  return (
    <section className="panel output" aria-label="Car output and timing">
      <h2 className="panel-title">Car output</h2>
      <dl className="readouts">
        <Readout label="Power" value={fmt(frame?.derived.power_kw)} unit="kW" sub={isNum(frame?.derived.power_hp) ? `${fmt(frame?.derived.power_hp)} hp` : undefined} />
        <Readout label="Torque" value={fmt(n?.torque_nm)} unit="N·m" />
        <Readout label="Boost" value={fmt(n?.boost_psi, 1)} unit="psi" />
        <Readout label="Fuel" value={fmtPct(n?.fuel_fraction)} />
      </dl>
      <h2 className="panel-title timing-title">Timing</h2>
      {!hasTiming && <p className="muted small">No lap timing in this session: the game reports lap times as 0.0 when they do not apply.</p>}
      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className="num">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="muted small race-flag">
        Game race flag (IsRaceOn): {n?.race_on === null || n?.race_on === undefined ? "unknown" : n.race_on ? "1" : "0"}
      </p>
    </section>
  );
}

function EmptyState({ snap, view }: { snap: Snapshot; view: ViewState }) {
  const addr = snap.status?.receiver_address ?? "127.0.0.1:5607";
  const [ip, port] = addr.split(":");
  if (view.kind === "error") {
    return (
      <section className="empty panel" aria-labelledby="empty-title">
        <h1 id="empty-title">The UDP receiver could not start</h1>
        <p className="error-text">{snap.status?.receiver_error}</p>
        <ul>
          <li>
            Another program (or a second Pitwall) may already use this port. Close it, or start Pitwall with <code>--udp-port</code> and set the same port in the game.
          </li>
          <li>Avoid ports 5200–5300; the game uses them for its own socket.</li>
        </ul>
        <DemoButton enable label="Explore with demo data" />
      </section>
    );
  }
  if (view.kind === "server_offline") {
    return (
      <section className="empty panel" aria-labelledby="empty-title">
        <h1 id="empty-title">Connecting to the Pitwall server…</h1>
        <p>If this does not change, make sure Pitwall is still running in its terminal window.</p>
      </section>
    );
  }
  return (
    <section className="empty panel" aria-labelledby="empty-title">
      <h1 id="empty-title">Waiting for telemetry</h1>
      <p>
        Listening on UDP <span className="num">{addr}</span>. Nothing has been received yet, so no values are shown.
      </p>
      <ol>
        <li>In Forza Horizon 6 open Settings › HUD and Gameplay.</li>
        <li>
          Set Data Out to On, Data Out IP Address to <code>{ip}</code> and Data Out IP Port to <code>{port}</code>.
        </li>
        <li>Start driving. The game sends data only while you drive, not in menus.</li>
      </ol>
      <DemoButton enable label="Explore with demo data" />
    </section>
  );
}

export function Live({ snap, view, focus }: { snap: Snapshot; view: ViewState; focus: boolean }) {
  const frame = snap.frame;
  if (!frame) return <EmptyState snap={snap} view={view} />;
  return (
    <div className={`live${view.dim ? " is-dim" : ""}${focus ? " is-focus" : ""}`}>
      <ShiftBand frame={frame} />
      <Cluster frame={frame} />
      <Inputs frame={frame} compactAux={focus} />
      <Chassis frame={frame} compact={focus} />
      {!focus && <Traces live={!view.dim} />}
      {!focus && <OutputAndTiming frame={frame} />}
    </div>
  );
}
