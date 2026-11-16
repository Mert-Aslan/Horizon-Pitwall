import { useEffect, useState } from "react";
import { Banners } from "./components/Banners";
import { Diagnostics } from "./components/Diagnostics";
import { Live } from "./components/Live";
import { TopBar, type Tab } from "./components/TopBar";
import { usePref } from "./prefs";
import { store, useTelemetry } from "./stream";
import type { Capabilities } from "./types";
import { deriveView } from "./viewstate";

export function App() {
  const snap = useTelemetry();
  const view = deriveView(snap);
  const [tab, setTab] = usePref<Tab>("tab", ["live", "diagnostics"], "live");
  const [focusPref, setFocusPref] = usePref("focus", ["on", "off"] as const, "off");
  const [caps, setCaps] = useState<Capabilities | null>(null);

  useEffect(() => store.start(), []);
  useEffect(() => {
    if (snap.link !== "open" || caps) return;
    fetch("/api/v1/capabilities", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => c && setCaps(c))
      .catch(() => undefined);
  }, [snap.link, caps]);

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
        tab={tab}
        onTab={setTab}
        focus={focus}
        onFocus={(on) => setFocusPref(on ? "on" : "off")}
        demoOrdinal={caps?.demo_car_ordinal ?? null}
      />
      <Banners snap={snap} view={view} />
      <main id="main" tabIndex={-1}>
        {tab === "live" ? <Live snap={snap} view={view} focus={focus} /> : <Diagnostics snap={snap} view={view} caps={caps} />}
      </main>
    </div>
  );
}
