import { Profiler, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// The profiling build collects React commit times for tools/ui_capture.py.
declare global {
  interface Window {
    __pitwallCommits?: number[];
  }
}
const onRender = (_id: string, _phase: string, actualDuration: number) => {
  const c = (window.__pitwallCommits ??= []);
  if (c.length < 100_000) c.push(actualDuration);
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Profiler id="app" onRender={onRender}>
      <App />
    </Profiler>
  </StrictMode>,
);
