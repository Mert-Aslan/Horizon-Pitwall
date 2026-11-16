import { useEffect, useState } from "react";
import { Banners } from "./components/Banners";
import { Diagnostics } from "./components/Diagnostics";
import { Garage } from "./components/Garage";
import { useCars } from "./cars";
import { Lab } from "./components/Lab";
import { Live } from "./components/Live";
import { Replay } from "./components/Replay";
import { Sessions } from "./components/Sessions";
import { TABS, TopBar, type Tab } from "./components/TopBar";
import { usePref } from "./prefs";
import { store, useTelemetry } from "./stream";
import type { Capabilities } from "./types";
import { deriveView } from "./viewstate";

export function App() {
  const snap = useTelemetry();
  const view = deriveView(snap);
  const [tab, setTab] = usePref<Tab>("tab", TABS, "live");
  const [focusPref, setFocusPref] = usePref("focus", ["on", "off"] as const, "off");
  const [caps, setCaps] = useState<Capabilities | null>(null);

  useEffect(() => store.start(), []);
  // Capabilities are re-read after every server restart (new server_instance_id).
  const [capsFor, setCapsFor] = useState(-1);
  useEffect(() => {
    if (snap.link !== "open" || capsFor === snap.serverRestarts) return;
    let alive = true;
    fetch("/api/v1/capabilities", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => {
        if (c && alive) {
          setCaps(c);
          setCapsFor(snap.serverRestarts);
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [snap.link, snap.serverRestarts, capsFor]);
  const recordingAvailable = !!caps?.features.recording;
  const catalogAvailable = !!caps?.features.car_catalog;
  const { names: carNames } = useCars(catalogAvailable);
  const shown: Tab = caps && ((tab === "sessions" && !recordingAvailable) || (tab === "garage" && !catalogAvailable)) ? "live" : tab;


  const focus = focusPref === "on";
  // Replay and Lab are views on top of the tabs; neither changes the server's live mode.
  const [overlay, setOverlay] = useState<{ kind: "replay" | "lab"; id: string; at?: number } | null>(null);
  const openTab = (t: Tab) => {
    setOverlay(null);
    setTab(t);
  };

  useEffect(() => {
    document.title = overlay ? `${overlay.kind === "lab" ? "Lab" : "Replay"} · Horizon Pitwall` : view.demo ? "Demo · Horizon Pitwall" : `${view.label} · Horizon Pitwall`;
  }, [overlay, view.demo, view.label]);
  return (
    <div className={`app state-${view.kind}${view.demo ? " is-demo" : ""}`}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <TopBar
        snap={snap}
        view={view}
        tab={overlay ? null : shown}
        onTab={openTab}
        overlay={overlay?.kind ?? null}
        focus={focus}
        onFocus={(on) => setFocusPref(on ? "on" : "off")}
        demoOrdinal={caps?.demo_car_ordinal ?? null}
        recordingAvailable={recordingAvailable}
        catalogAvailable={catalogAvailable}
        carNames={carNames}
      />
      <Banners snap={snap} view={view} replay={!!overlay} />
      <main id="main" tabIndex={-1}>
        {overlay?.kind === "replay" ? (
          <Replay key={`${overlay.id}@${overlay.at ?? 0}`} id={overlay.id} startAt={overlay.at} liveSnap={snap} focus={focus} onClose={() => openTab("sessions")} />
        ) : overlay?.kind === "lab" ? (
          <Lab key={overlay.id} id={overlay.id} onClose={() => openTab("sessions")} onReplayAt={(id, at) => setOverlay({ kind: "replay", id, at })} />
        ) : (
          <>
            {shown === "live" && <Live snap={snap} view={view} focus={focus} />}
            {shown === "sessions" && (
              <Sessions snap={snap} onReplay={(id) => setOverlay({ kind: "replay", id })} onAnalyze={caps?.features.analysis ? (id) => setOverlay({ kind: "lab", id }) : undefined} />
            )}
            {shown === "garage" && <Garage snap={snap} />}
            {shown === "diagnostics" && <Diagnostics snap={snap} view={view} caps={caps} />}
          </>
        )}
      </main>
    </div>
  );
}
