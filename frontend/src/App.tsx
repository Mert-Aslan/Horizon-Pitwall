import { useEffect, useState } from "react";
import { Banners } from "./components/Banners";
import { Diagnostics } from "./components/Diagnostics";
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
  const shown: Tab = tab === "sessions" && caps && !recordingAvailable ? "live" : tab;


  const focus = focusPref === "on";
  // Replay is a view on top of the tabs; it never changes the server's live mode.
  const [replayId, setReplayId] = useState<string | null>(null);
  const openTab = (t: Tab) => {
    setReplayId(null);
    setTab(t);
  };

  useEffect(() => {
    document.title = replayId ? "Replay · Horizon Pitwall" : view.demo ? "Demo · Horizon Pitwall" : `${view.label} · Horizon Pitwall`;
  }, [replayId, view.demo, view.label]);
  return (
    <div className={`app state-${view.kind}${view.demo ? " is-demo" : ""}`}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <TopBar
        snap={snap}
        view={view}
        tab={replayId ? null : shown}
        onTab={openTab}
        replay={!!replayId}
        focus={focus}
        onFocus={(on) => setFocusPref(on ? "on" : "off")}
        demoOrdinal={caps?.demo_car_ordinal ?? null}
        recordingAvailable={recordingAvailable}
      />
      <Banners snap={snap} view={view} replay={!!replayId} />
      <main id="main" tabIndex={-1}>
        {replayId ? (
          <Replay key={replayId} id={replayId} liveSnap={snap} focus={focus} onClose={() => openTab("sessions")} />
        ) : (
          <>
            {shown === "live" && <Live snap={snap} view={view} focus={focus} />}
            {shown === "sessions" && <Sessions snap={snap} onReplay={setReplayId} />}
            {shown === "diagnostics" && <Diagnostics snap={snap} view={view} caps={caps} />}
          </>
        )}
      </main>
    </div>
  );
}
