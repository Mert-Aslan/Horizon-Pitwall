import { useState } from "react";
import { setDemo } from "../stream";

/** Demo on/off with visible pending and failure states. */
export function DemoButton({ enable, label, quiet }: { enable: boolean; label: string; quiet?: boolean }) {
  const [state, setState] = useState<"idle" | "pending" | "error">("idle");
  const [error, setError] = useState("");
  const run = async () => {
    setState("pending");
    try {
      await setDemo(enable);
      setState("idle");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  };
  return (
    <span className="action">
      <button type="button" className={`btn${quiet ? " btn-quiet" : ""}`} disabled={state === "pending"} aria-busy={state === "pending"} onClick={() => void run()}>
        {state === "pending" ? (enable ? "Starting demo…" : "Stopping demo…") : label}
      </button>
      {state === "error" && (
        <span className="action-error" role="alert">
          Could not {enable ? "start" : "stop"} demo: {error}
        </span>
      )}
    </span>
  );
}
