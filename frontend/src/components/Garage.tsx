// Garage: give the numeric car ids from the game a name. Pitwall ships no built-in
// list; names are typed here or imported from a file the user chooses.
import { useState } from "react";
import { CARS_CHANGED, carDisplayName, provenance, useCars, type CarName } from "../cars";
import { fmt, isNum } from "../format";
import type { Snapshot } from "../stream";

function when(utc: string | null): string {
  if (!utc) return "—";
  const d = new Date(utc);
  return Number.isNaN(d.getTime()) ? utc : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

async function send(method: string, url: string, body?: unknown): Promise<Response> {
  return fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
}

function errorText(r: Response | null, body: { detail?: string } | null): string {
  if (!r) return "the server could not be reached";
  if (r.status === 403) return "this device may only view; edit names on the Pitwall PC";
  return body?.detail ?? `the server answered ${r.status}`;
}

function Editor({ ordinal, current, onDone }: { ordinal: number; current?: CarName; onDone: () => void }) {
  const [form, setForm] = useState({ name: current?.name ?? "", year: current?.year ? String(current.year) : "", variant: current?.variant ?? "", notes: current?.notes ?? "" });
  const [msg, setMsg] = useState<string | null>(null);
  const save = async () => {
    setMsg(null);
    const year = form.year.trim() ? Number(form.year) : null;
    const r = await send("PUT", `/api/v1/cars/${ordinal}`, { name: form.name, year, variant: form.variant || null, notes: form.notes || null }).catch(() => null);
    const body = r ? await r.json().catch(() => null) : null;
    if (r?.ok) {
      window.dispatchEvent(new Event(CARS_CHANGED));
      onDone();
    } else setMsg(`Not saved: ${errorText(r, body)}.`);
  };
  const remove = async () => {
    const r = await send("DELETE", `/api/v1/cars/${ordinal}`).catch(() => null);
    if (r?.ok) {
      window.dispatchEvent(new Event(CARS_CHANGED));
      onDone();
    } else setMsg("The name could not be removed.");
  };
  return (
    <form
      className="car-editor"
      onSubmit={(e) => {
        e.preventDefault();
        if (form.name.trim()) void save();
      }}
    >
      <label className="field">
        <span>Name</span>
        <input autoFocus value={form.name} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. the model as shown in the game" />
      </label>
      <label className="field field-short">
        <span>Year</span>
        <input inputMode="numeric" value={form.year} maxLength={4} onChange={(e) => setForm({ ...form, year: e.target.value.replace(/\D/g, "") })} />
      </label>
      <label className="field">
        <span>Variant</span>
        <input value={form.variant} maxLength={120} onChange={(e) => setForm({ ...form, variant: e.target.value })} placeholder="optional" />
      </label>
      <label className="field">
        <span>Notes</span>
        <input value={form.notes} maxLength={500} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="optional" />
      </label>
      <div className="row-actions">
        <button type="submit" className="btn btn-small" disabled={!form.name.trim()}>
          Save name
        </button>
        <button type="button" className="btn btn-small btn-quiet" onClick={onDone}>
          Cancel
        </button>
        {current && (
          <button type="button" className="btn btn-small btn-quiet" onClick={() => void remove()}>
            Remove name
          </button>
        )}
        {msg && (
          <span className="text-danger small" role="alert">
            {msg}
          </span>
        )}
      </div>
    </form>
  );
}

export function Garage({ snap }: { snap: Snapshot }) {
  const { names, seen, loaded, failed } = useCars(true);
  const [editing, setEditing] = useState<number | null>(null);
  const [manual, setManual] = useState("");
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const live = snap.frame?.car_identity?.status === "confirmed" ? snap.frame.car_identity.confirmed_ordinal : null;
  const liveCar = snap.frame?.normalized.car;

  if (!loaded) return <p className="muted pad">Loading the garage…</p>;
  if (failed) return <p className="muted pad">The car list could not be loaded from the server.</p>;

  const ordinals = new Set<number>([...seen.map((s) => s.ordinal), ...names.keys()]);
  if (live !== null && live > 0) ordinals.add(live);
  const rows = [...ordinals].sort((a, b) => (a === live ? -1 : b === live ? 1 : 0));
  const seenBy = new Map(seen.map((s) => [s.ordinal, s]));

  const doImport = async (file: File) => {
    setImportMsg(null);
    if (file.size > 2_000_000) {
      setImportMsg("The file is larger than 2 MB.");
      return;
    }
    const text = await file.text();
    const r = await fetch("/api/v1/cars/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: text }).catch(() => null);
    const body = r ? await r.json().catch(() => null) : null;
    if (!r?.ok) {
      setImportMsg(`Not imported: ${errorText(r, body)}.`);
      return;
    }
    window.dispatchEvent(new Event(CARS_CHANGED));
    const parts = [`${body.added} added`, `${body.updated} updated`];
    if (body.kept_user_entries.length) parts.push(`${body.kept_user_entries.length} kept because you named them yourself`);
    if (body.conflicts.length) parts.push(`${body.conflicts.length} not changed (named by another list: ${body.conflicts.slice(0, 3).map((c: { ordinal: number }) => `#${c.ordinal}`).join(", ")})`);
    if (body.invalid.length) parts.push(`${body.invalid.length} invalid lines skipped`);
    setImportMsg(`Imported from ${body.source.replace(/^import:/, "")}: ${parts.join(", ")}.`);
  };

  return (
    <div className="garage">
      <section className="panel" aria-labelledby="garage-title">
        <div className="panel-head">
          <h2 id="garage-title" className="panel-title">
            Garage <span className="muted small">{names.size} named · {seen.length} seen in recordings</span>
          </h2>
          <div className="row-actions">
            <label className="btn btn-small btn-quiet file-btn">
              Import names…
              <input type="file" accept="application/json,.json" onChange={(e) => e.target.files?.[0] && void doImport(e.target.files[0])} />
            </label>
            <a className="btn btn-small btn-quiet" href="/api/v1/cars/export" download>
              Export names
            </a>
          </div>
        </div>
        <p className="muted small">
          The game only sends a number for each car. Pitwall does not come with a list of names, so it never guesses: name a car once here and that name is
          used everywhere. Imported lists show where they came from and never replace a name you typed. Power, PI and drivetrain on screen always come from
          the game, not from this list.
        </p>
        {importMsg && (
          <p className="small" role="status">
            {importMsg}
          </p>
        )}
        {rows.length === 0 ? (
          <p className="muted pad">No cars yet. Drive with Data Out on (and record) and the cars you use appear here.</p>
        ) : (
          <ul className="garage-list">
            {rows.map((o) => {
              const n = names.get(o);
              const s = seenBy.get(o);
              return (
                <li key={o} className={o === live ? "is-live" : undefined}>
                  <div className="garage-row">
                    <span className="garage-id num">#{o}</span>
                    <span className="garage-name">
                      {n ? carDisplayName(n) : <span className="muted">No name yet</span>}
                      {n && <span className="tag">{provenance(n)}</span>}
                      {o === live && <span className="tag tag-certain">on track now</span>}
                    </span>
                    <span className="garage-meta muted small num">
                      {o === live && liveCar && (
                        <>
                          Class {liveCar.class_label ?? "—"} · PI {fmt(liveCar.pi)} · {liveCar.drivetrain_label ?? "—"}
                          {isNum(liveCar.cylinders) ? ` · ${liveCar.cylinders} cyl` : ""} ·{" "}
                        </>
                      )}
                      {s ? `${s.recordings} recording${s.recordings === 1 ? "" : "s"}, last ${when(s.last_seen_utc)}` : "not in recordings"}
                      {s && s.car_labels.length > 0 && ` · your recording labels: ${s.car_labels.slice(0, 3).join(", ")}`}
                    </span>
                    {editing !== o && (
                      <button type="button" className="btn btn-small btn-quiet" onClick={() => setEditing(o)}>
                        {n ? "Edit" : "Name it"}
                      </button>
                    )}
                  </div>
                  {editing === o && <Editor ordinal={o} current={n} onDone={() => setEditing(null)} />}
                </li>
              );
            })}
          </ul>
        )}
        <form
          className="row-actions garage-add"
          onSubmit={(e) => {
            e.preventDefault();
            const o = Number(manual);
            if (Number.isInteger(o) && o > 0) {
              setEditing(o);
              setManual("");
            }
          }}
        >
          <label className="field field-short">
            <span>Name another car id</span>
            <input inputMode="numeric" value={manual} onChange={(e) => setManual(e.target.value.replace(/\D/g, ""))} placeholder="e.g. 4210" />
          </label>
          <button type="submit" className="btn btn-small" disabled={!manual}>
            Add
          </button>
        </form>
        {editing !== null && !rows.includes(editing) && <Editor ordinal={editing} onDone={() => setEditing(null)} />}
      </section>
    </div>
  );
}
