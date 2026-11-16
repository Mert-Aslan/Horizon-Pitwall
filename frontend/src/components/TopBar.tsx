import { ageText, fmt, isNum } from "../format";
import type { Snapshot } from "../stream";
import type { ViewState } from "../viewstate";

export type Tab = "live" | "diagnostics";

interface Props {
  snap: Snapshot;
  view: ViewState;
  tab: Tab;
  onTab: (t: Tab) => void;
  focus: boolean;
  onFocus: (on: boolean) => void;
  demoOrdinal: number | null;
}

export function CarIdentity({ snap, demoOrdinal }: { snap: Snapshot; demoOrdinal: number | null }) {
  const car = snap.frame?.normalized.car;
  if (!car) return <span className="car-id muted">No car yet</span>;
  const isDemoCar = demoOrdinal !== null && car.ordinal === demoOrdinal;
  const name = isDemoCar ? "Demo car" : `Car #${fmt(car.ordinal)}`;
  return (
    <span className="car-id" title={isDemoCar ? "Synthetic demo data" : `CarOrdinal ${fmt(car.ordinal)}: no name is known for this id yet`}>
      <span className="car-name">{name}</span>
      <span className="tag tag-neutral">{isDemoCar ? "synthetic" : "name not known yet"}</span>
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

export function TopBar({ snap, view, tab, onTab, focus, onFocus, demoOrdinal }: Props) {
  const st = snap.status;
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
        {(["live", "diagnostics"] as const).map((t) => (
          <button key={t} className="tab" aria-current={tab === t ? "page" : undefined} onClick={() => onTab(t)}>
            {t === "live" ? "Live" : "Diagnostics"}
          </button>
        ))}
      </nav>
      <div className="topbar-car">
        <CarIdentity snap={snap} demoOrdinal={demoOrdinal} />
      </div>
      <div className="topbar-status">
        {tab === "live" && (
          <button type="button" className="btn btn-quiet btn-small focus-toggle" aria-pressed={focus} onClick={() => onFocus(!focus)}>
            Focus view
          </button>
        )}
        {view.demo && <span className="mode-badge">Demo</span>}
        <span className={`conn conn-${view.kind}`} role="status" aria-live="polite">
          <span className="conn-mark" aria-hidden="true" />
          {view.label}
        </span>
        <span className="conn-detail" aria-hidden={view.kind !== "receiving" && view.kind !== "stale"}>
          {view.kind === "receiving" && st && (
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
