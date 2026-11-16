import { useEffect, useState } from "react";
import { Banners } from "./components/Banners";
import { Diagnostics } from "./components/Diagnostics";
import { Live } from "./components/Live";
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

  useEffect(() => {
    document.title = view.demo ? "Demo · Horizon Pitwall" : `${view.label} · Horizon Pitwall`;
  }, [view.demo, view.label]);

  const focus = focusPref === "on";
  return (
    <div className={`app state-${view.kind}${view.demo ? " is-demo" : ""}`}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <TopBar
        snap={snap}
        view={view}
        tab={shown}
        onTab={setTab}
        focus={focus}
        onFocus={(on) => setFocusPref(on ? "on" : "off")}
        demoOrdinal={caps?.demo_car_ordinal ?? null}
        recordingAvailable={recordingAvailable}
      />
      <Banners snap={snap} view={view} />
      <main id="main" tabIndex={-1}>
        {shown === "live" && <Live snap={snap} view={view} focus={focus} />}
        {shown === "sessions" && <Sessions snap={snap} />}
        {shown === "diagnostics" && <Diagnostics snap={snap} view={view} caps={caps} />}
      </main>
    </div>
  );
}
