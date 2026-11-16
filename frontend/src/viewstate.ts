// One UI state model derived from the server's status plus the link to the server.
import type { Snapshot } from "./stream";

export type ViewKind = "server_offline" | "starting" | "waiting" | "receiving" | "no_data" | "stale" | "error";

export interface ViewState {
  kind: ViewKind;
  demo: boolean;
  /** live values exist but should be shown dimmed, not animated */
  dim: boolean;
  hasFrame: boolean;
  warning: "invalid_packets" | "foreign_source" | null;
  label: string;
}

const LABELS: Record<ViewKind, string> = {
  server_offline: "Server unreachable",
  starting: "Starting receiver",
  waiting: "Waiting for data",
  receiving: "Receiving",
  no_data: "No driving data",
  stale: "Data stale",
  error: "Receiver error",
};

export function deriveView(s: Snapshot): ViewState {
  const hasFrame = s.frame !== null;
  let kind: ViewKind;
  if (s.link !== "open" || !s.status) kind = "server_offline";
  else kind = s.status.connection_state;
  if (kind === "receiving" && s.telemetryEmpty) kind = "no_data";
  const demo = (s.status?.mode ?? s.frame?.mode) === "demo";
  return {
    kind,
    demo,
    dim: kind !== "receiving",
    hasFrame,
    warning: kind === "server_offline" ? null : (s.status?.traffic_warning ?? null),
    label: LABELS[kind],
  };
}
