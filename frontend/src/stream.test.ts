// Stream client regression tests (F-01 watchdog, F-03 identity/raw isolation, lifecycle,
// runtime validation). A fake environment replaces WebSocket, timers and rAF, so these run
// in Node without a browser; real-browser behaviour is covered by tools/ui_smoke.py.
import { describe, expect, it } from "vitest";
import { TelemetryStore, parseMessage, type SocketLike, type StreamEnv } from "./stream";

class FakeSocket implements SocketLike {
  readyState = 0;
  sent: string[] = [];
  closed = 0;
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.closed++;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  recv(obj: unknown) {
    this.onmessage?.({ data: typeof obj === "string" ? obj : JSON.stringify(obj) });
  }
  serverClose() {
    this.readyState = 3;
    this.onclose?.();
  }
}

class FakeEnv implements StreamEnv {
  t = 0;
  sockets: FakeSocket[] = [];
  intervals = new Map<number, { cb: () => void; ms: number; next: number }>();
  timeouts = new Map<number, { cb: () => void; at: number }>();
  rafs = new Map<number, () => void>();
  visible: (() => void)[] = [];
  private id = 0;
  rafPaused = false;
  uiBuildId: string | null = "build-A";
  makeSocket() {
    const s = new FakeSocket();
    this.sockets.push(s);
    return s;
  }
  now() {
    return this.t;
  }
  raf(cb: () => void) {
    this.rafs.set(++this.id, cb);
    return this.id;
  }
  cancelRaf(id: number) {
    this.rafs.delete(id);
  }
  setInterval(cb: () => void, ms: number) {
    this.intervals.set(++this.id, { cb, ms, next: this.t + ms });
    return this.id;
  }
  clearInterval(id: number) {
    this.intervals.delete(id);
  }
  setTimeout(cb: () => void, ms: number) {
    this.timeouts.set(++this.id, { cb, at: this.t + ms });
    return this.id;
  }
  clearTimeout(id: number) {
    this.timeouts.delete(id);
  }
  onVisible(cb: () => void) {
    this.visible.push(cb);
    return () => {
      this.visible = this.visible.filter((x) => x !== cb);
    };
  }
  random() {
    return 0;
  }
  /** advance time; fire due timers; run rAF callbacks unless the tab is "hidden" */
  advance(ms: number, step = 100) {
    const end = this.t + ms;
    while (this.t < end) {
      this.t = Math.min(end, this.t + step);
      for (const [, iv] of this.intervals) {
        while (iv.next <= this.t) {
          iv.next += iv.ms;
          iv.cb();
        }
      }
      for (const [id, to] of [...this.timeouts]) {
        if (to.at <= this.t) {
          this.timeouts.delete(id);
          to.cb();
        }
      }
      this.flushRaf();
    }
  }
  flushRaf() {
    if (this.rafPaused) return;
    for (const [id, cb] of [...this.rafs]) {
      this.rafs.delete(id);
      cb();
    }
  }
  get ws() {
    return this.sockets[this.sockets.length - 1];
  }
}

const status = { connection_state: "receiving", mode: "live", sequence: 1 };
function frameMsg(inst: string, seg: string, seq: number, speed = 100, raw?: Record<string, number>) {
  return {
    type: "frame",
    status,
    frame: {
      server_instance_id: inst,
      stream_segment_id: seg,
      session_id: seg,
      sequence: seq,
      normalized: { rpm: 5000, inputs: { throttle: 1, brake: 0, steer: 0 }, car: { ordinal: 1 } },
      derived: { speed_kmh: speed },
      quality: { fields: {} },
      ...(raw ? { raw } : {}),
    },
  };
}
const hello = (inst: string, ui = "build-A") => ({ type: "hello", api_version: 1, server_instance_id: inst, ui_build_id: ui });

function setup() {
  const env = new FakeEnv();
  const store = new TelemetryStore(env);
  store.start();
  env.ws.open();
  env.ws.recv(hello("i1"));
  return { env, store };
}

describe("F-01 watchdog uses transport time, not render time", () => {
  it("does not close a live socket while rAF is paused (background tab)", () => {
    const { env, store } = setup();
    env.ws.recv(frameMsg("i1", "s1", 1));
    env.flushRaf();
    env.rafPaused = true; // hidden tab: no commits from now on
    for (let i = 2; i < 10; i++) {
      env.advance(1000);
      env.ws.recv(frameMsg("i1", "s1", i));
    }
    expect(env.ws.closed).toBe(0);
    expect(store.getSnapshot().frame?.sequence).toBe(1); // nothing committed, as intended
    expect(store.history.last()?.speed).toBe(100); // but data still recorded in the trace
  });
  it("closes once on real silence and reconnects", () => {
    const { env } = setup();
    env.ws.recv(frameMsg("i1", "s1", 1));
    const first = env.ws;
    env.advance(4000);
    expect(first.closed).toBe(1);
    first.serverClose();
    env.advance(1000);
    expect(env.sockets.length).toBe(2);
  });
});

describe("F-03 stream identity", () => {
  it("a new segment with the same sequence number is not dropped", () => {
    const { env, store } = setup();
    env.ws.recv(frameMsg("i1", "A", 1, 50));
    env.ws.recv(frameMsg("i1", "B", 1, 80));
    env.flushRaf();
    expect(store.history.last()?.speed).toBe(80);
    expect([...store.history.since(-1)].length).toBe(1); // A's sample is not mixed in
  });
  it("a server restart (new instance) with the same segment-local sequence starts fresh", () => {
    const { env, store } = setup();
    env.ws.recv(frameMsg("i1", "A", 7, 50));
    env.ws.recv(hello("i2"));
    env.ws.recv(frameMsg("i2", "Z", 7, 90));
    env.flushRaf();
    expect(store.getSnapshot().serverRestarts).toBe(1);
    expect([...store.history.since(-1)].map((s) => s.speed)).toEqual([90]);
  });
  it("raw values never carry over into another stream", () => {
    const { env, store } = setup();
    env.ws.recv(frameMsg("i1", "A", 1, 50, { CarOrdinal: 4156 }));
    env.flushRaf();
    expect(store.getSnapshot().raw?.values.CarOrdinal).toBe(4156);
    env.ws.recv(frameMsg("i1", "A", 2, 50));
    env.flushRaf();
    expect(store.getSnapshot().raw?.sequence).toBe(1); // same stream: older raw kept, labelled with its frame
    env.ws.recv(frameMsg("i1", "B", 1, 50));
    env.flushRaf();
    expect(store.getSnapshot().raw).toBeNull();
    expect(store.getSnapshot().frame && "raw" in store.getSnapshot().frame!).toBe(false);
  });
});

describe("lifecycle", () => {
  it("start is idempotent, stop detaches and clears timers, old callbacks are ignored", () => {
    const env = new FakeEnv();
    const store = new TelemetryStore(env);
    store.start();
    store.start();
    expect(env.sockets.length).toBe(1);
    expect(env.intervals.size).toBe(1);
    env.ws.open();
    const old = env.ws;
    const handler = old.onmessage;
    store.stop();
    store.stop();
    expect(old.closed).toBe(1);
    expect(env.intervals.size).toBe(0);
    expect(env.visible.length).toBe(0);
    handler?.({ data: JSON.stringify(frameMsg("i1", "A", 1)) }); // late message from the closed socket
    env.advance(20_000);
    expect(store.getSnapshot().frame).toBeNull();
    expect(env.sockets.length).toBe(1); // no reconnect after stop
    store.start();
    expect(env.sockets.length).toBe(2);
    store.dispose();
  });
  it("a reconnect timer pending at stop() never fires", () => {
    const { env, store } = setup();
    env.ws.serverClose();
    expect(env.timeouts.size).toBe(1);
    store.stop();
    expect(env.timeouts.size).toBe(0);
  });
});

describe("empty game packets", () => {
  const emptyMsg = (seq: number) => {
    const m = frameMsg("i1", "A", seq, 0);
    (m.frame as Record<string, unknown>).quality = { fields: {}, flags: ["empty_packet"] };
    (m.frame as { derived: { speed_kmh: number | null } }).derived.speed_kmh = null;
    return m;
  };
  it("keep the last real frame on screen and mark the stream as empty", () => {
    const { env, store } = setup();
    env.ws.recv(frameMsg("i1", "A", 1, 120));
    env.ws.recv(emptyMsg(2));
    env.flushRaf();
    const s = store.getSnapshot();
    expect(s.telemetryEmpty).toBe(true);
    expect(s.frame?.sequence).toBe(1);
    expect(store.history.last()?.speed).toBeNull(); // the trace shows the gap
    env.ws.recv(frameMsg("i1", "A", 3, 90));
    env.flushRaf();
    expect(store.getSnapshot().telemetryEmpty).toBe(false);
    expect(store.getSnapshot().frame?.sequence).toBe(3);
  });
  it("an empty first frame of a new stream is shown as-is (no borrowed values)", () => {
    const { env, store } = setup();
    env.ws.recv(frameMsg("i1", "A", 1, 120));
    const m = emptyMsg(1);
    (m.frame as Record<string, unknown>).stream_segment_id = "B";
    env.ws.recv(m);
    env.flushRaf();
    expect(store.getSnapshot().frame?.stream_segment_id).toBe("B");
  });
});

describe("flow control", () => {
  it("opts in when the server offers it and acks on receipt, even with rAF paused", () => {
    const env = new FakeEnv();
    const store = new TelemetryStore(env);
    store.start();
    env.ws.open();
    env.rafPaused = true;
    env.ws.recv({ ...hello("i1"), flow: { window: 15, ack_every: 5 } });
    for (let i = 1; i <= 9; i++) env.ws.recv(frameMsg("i1", "A", i));
    const sent = env.ws.sent.map((x) => JSON.parse(x));
    expect(sent[0]).toEqual({ type: "flow", ack: true });
    expect(sent.filter((m) => m.type === "ack").map((m) => m.n)).toEqual([5, 10]);
  });
  it("an old server without flow gets no acks", () => {
    const { env } = setup();
    for (let i = 1; i <= 12; i++) env.ws.recv(frameMsg("i1", "A", i));
    expect(env.ws.sent.length).toBe(0);
  });
});

describe("runtime message validation", () => {
  it("rejects malformed shapes", () => {
    expect(parseMessage("{").kind).toBe("invalid");
    expect(parseMessage('{"type":"frame"}').kind).toBe("invalid");
    expect(parseMessage(JSON.stringify({ type: "frame", status: { connection_state: "odd", mode: "live" }, frame: null })).kind).toBe("invalid");
    expect(parseMessage(JSON.stringify({ type: "frame", status, frame: { sequence: "1" } })).kind).toBe("invalid");
    expect(parseMessage(JSON.stringify({ type: "frame", status, frame: null })).kind).toBe("frame");
  });
  it("persistent bad data becomes a visible error without breaking the stream", () => {
    const { env, store } = setup();
    for (let i = 0; i < 5; i++) env.ws.recv("garbage");
    env.flushRaf();
    expect(store.getSnapshot().protocolError).toMatch(/cannot read/);
    env.ws.recv(frameMsg("i1", "A", 1));
    env.flushRaf();
    expect(store.getSnapshot().protocolError).toBeNull();
    expect(store.getSnapshot().frame?.sequence).toBe(1);
  });
  it("flags a newer UI bundle on the server", () => {
    const { env, store } = setup();
    env.ws.recv(hello("i1", "build-B"));
    env.flushRaf();
    expect(store.getSnapshot().uiBuildMismatch).toBe(true);
  });
});
