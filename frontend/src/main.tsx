import { Profiler, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// `npm run build:profile` aliases react-dom to its profiling build so the Profiler below
// reports commit durations (window.__pitwallCommits) for tools/ui_capture.py. In the
// normal production build onRender is never called and this costs nothing.
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
