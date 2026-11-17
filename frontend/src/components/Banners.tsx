import { useEffect, useState, type ReactNode } from "react";
import { ageText } from "../format";
import type { Snapshot } from "../stream";
import { DemoButton } from "./DemoButton";
import type { ViewState } from "../viewstate";

/** Updates the reconnect countdown while offline. */
function useTick(active: boolean): void {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => set((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [active]);
}

export function Banners({ snap, view, replay = false }: { snap: Snapshot; view: ViewState; replay?: boolean }) {
  useTick(view.kind === "server_offline");
  const items: { tone: "info" | "warn" | "danger" | "demo"; text: string; action?: ReactNode }[] = [];
  if (snap.versionMismatch) {
    items.push({ tone: "danger", text: "Incompatible Pitwall version. Reload the page." });
  } else if (snap.uiBuildMismatch) {
    items.push({
      tone: "info",
      text: "Pitwall has been updated.",
      action: (
        <button type="button" className="btn btn-small" onClick={() => location.reload()}>
          Reload page
        </button>
      ),
    });
  }
  if (snap.protocolError) {
    items.push({ tone: "danger", text: "Could not read telemetry from the server. Reload the page." });
  }
  const rec = snap.status?.recorder;
  if (rec?.state === "failed" && rec.error) {
    items.push({ tone: "danger", text: `Recording stopped: ${rec.error}. Check Sessions for saved data.` });
  }
  if (view.demo && !replay) {
    items.push({
      tone: "demo",
      text: "Live telemetry is paused during the demo.",
      action: <DemoButton enable={false} label="Stop demo" quiet />,
    });
  }
  if (view.kind === "server_offline" && snap.lastMessageAt !== null) {
    const secs = snap.retryAt ? Math.max(0, Math.round((snap.retryAt - performance.now()) / 1000)) : null;
    items.push({ tone: "danger", text: secs !== null ? `Reconnecting in ${secs} s…` : "Reconnecting…" });
  }
  if (view.kind === "no_data" && !replay) {
    items.push({
      tone: "warn",
      text: `Packets without driving data${view.hasFrame ? ". Showing the last driving values." : ". Start driving to see telemetry."}`,
    });
  }
  if (view.kind === "stale" && view.hasFrame && !replay) {
    items.push({
      tone: "warn",
      text: `No new packets for ${ageText(snap.status?.packet_age_ms)}. Showing the last values.`,
    });
  }
  if (view.warning === "invalid_packets" && !replay) {
    items.push({
      tone: "danger",
      text: "Unexpected packet format. Check that Forza Horizon 6 is the sender. See Diagnostics for details.",
    });
  }
  if (view.warning === "foreign_source" && !replay) {
    items.push({ tone: "warn", text: "Packets from a second sender are being ignored. See Diagnostics." });
  }
  if (!items.length) return null;
  return (
    <div className="banners">
      {items.map((b, i) => (
        <div key={i} className={`banner banner-${b.tone}`}>
          <span>{b.text}</span>
          {b.action}
        </div>
      ))}
    </div>
  );
}
