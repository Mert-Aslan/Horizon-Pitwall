import { useEffect, useState, type ReactNode } from "react";
import { ageText } from "../format";
import type { Snapshot } from "../stream";
import { DemoButton } from "./DemoButton";
import type { ViewState } from "../viewstate";

/** Re-render once a second while the server is unreachable so the retry countdown moves
 *  (no messages arrive to trigger renders in that state). */
function useTick(active: boolean): void {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => set((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [active]);
}

export function Banners({ snap, view }: { snap: Snapshot; view: ViewState }) {
  useTick(view.kind === "server_offline");
  const items: { tone: "info" | "warn" | "danger" | "demo"; text: string; action?: ReactNode }[] = [];
  if (snap.versionMismatch) {
    items.push({ tone: "danger", text: "This page and the Pitwall server are different versions. Reload the page." });
  } else if (snap.uiBuildMismatch) {
    items.push({
      tone: "info",
      text: "The Pitwall server now serves a newer version of this page.",
      action: (
        <button type="button" className="btn btn-small" onClick={() => location.reload()}>
          Reload page
        </button>
      ),
    });
  }
  if (snap.protocolError) {
    items.push({ tone: "danger", text: `${snap.protocolError} Values may be out of date; reloading the page usually fixes a version mix-up.` });
  }
  const rec = snap.status?.recorder;
  if (rec?.state === "failed" && rec.error) {
    items.push({ tone: "danger", text: `Recording stopped: ${rec.error}. Live view keeps working; the part saved so far is in Sessions.` });
  }
  if (view.demo) {
    items.push({
      tone: "demo",
      text: "Demo mode: synthetic data, not a real drive. Live UDP packets are ignored while demo is on.",
      action: <DemoButton enable={false} label="Stop demo" quiet />,
    });
  }
  if (view.kind === "server_offline" && snap.lastMessageAt !== null) {
    const secs = snap.retryAt ? Math.max(0, Math.round((snap.retryAt - performance.now()) / 1000)) : null;
    items.push({ tone: "danger", text: `Lost connection to the Pitwall server${secs !== null ? `; retrying in ${secs} s` : ""}. Values below are frozen.` });
  }
  if (view.kind === "no_data") {
    items.push({
      tone: "warn",
      text: `The game is sending packets without driving data (IsRaceOn = 0, every value zero).${view.hasFrame ? " Last driving values shown dimmed." : ""} Data returns when you are driving again.`,
    });
  }
  if (view.kind === "stale" && view.hasFrame) {
    items.push({
      tone: "warn",
      text: `No new packets for ${ageText(snap.status?.packet_age_ms)}. Last values shown dimmed. The game pauses Data Out in menus, pause, rewind, replays and after a race.`,
    });
  }
  if (view.warning === "invalid_packets") {
    items.push({
      tone: "danger",
      text: `Packets are arriving but do not match the FH6 format (${snap.status?.last_rejected_reason ?? "invalid"}). Check that the sender is Forza Horizon 6. Details in Diagnostics.`,
    });
  }
  if (view.warning === "foreign_source") {
    items.push({ tone: "warn", text: "A second sender is transmitting to this port; it is ignored while the current one is active. See Diagnostics." });
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
