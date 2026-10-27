import { useCallback, useEffect, useState } from "react";
import {
  addModelTest,
  deleteModelTest,
  loadModelJson,
  modelJson,
  modelTests,
  modelVersions,
  productClasses,
  releaseCheck,
  releaseModel,
  retireModelVersion,
  saveClassAttribute,
  saveProductClass,
  type ModelTestOut,
  type ModelVersionOut,
  type ProductClassOut,
  type ReleaseCheck,
} from "../api/client";
import Configurator from "../components/configurator/Configurator";
import { INPUT_CLS } from "../components/intent/intentUi";

const clean = (e: unknown) => String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");

/** Workspace sandbox, model tests, QA, release gate and versions of one product. */
export function ModelLifecycle({ product, onChanged }: { product: string; onChanged: () => void }) {
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [tests, setTests] = useState<ModelTestOut[]>([]);
  const [check, setCheck] = useState<ReleaseCheck | null>(null);
  const [versions, setVersions] = useState<ModelVersionOut[]>([]);
  const [testName, setTestName] = useState("");
  const [expect, setExpect] = useState<"valid" | "invalid">("valid");
  const [validFrom, setValidFrom] = useState("");
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    modelTests(product).then(setTests).catch((e) => setError(clean(e)));
    modelVersions(product).then(setVersions).catch(() => null);
  }, [product]);

  useEffect(() => {
    reload();
  }, [reload]);

  const download = async (version?: number) => {
    const data = await modelJson(product, version);
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${product.replace(/\s+/g, "_")}${version ? `_v${version}` : "_workspace"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
      {status && <p className="text-xs text-emerald-400">{status}</p>}
      <section className="space-y-2 rounded-xl border border-slate-800 p-3">
        <p className="text-sm font-semibold text-slate-200">Try the workspace model</p>
        <p className="text-xs text-slate-500">Exactly what sellers will get after you release - pick options, see what is ruled out and why, then keep good cases as tests.</p>
        <Configurator product={product} picks={picks} onChange={setPicks} context={{ workspace: true }} />
        <div className="flex flex-wrap items-center gap-2">
          <input value={testName} onChange={(e) => setTestName(e.target.value)} placeholder="Test name, e.g. DSL limited to 2 Mbps" className={`${INPUT_CLS} w-72`} />
          <select value={expect} onChange={(e) => setExpect(e.target.value as "valid" | "invalid")} className={`${INPUT_CLS} w-auto`}>
            <option value="valid">should be valid</option>
            <option value="invalid">should be refused</option>
          </select>
          <button
            type="button"
            disabled={!testName.trim()}
            onClick={() =>
              addModelTest(product, { name: testName.trim(), picks, expect })
                .then(() => (setTestName(""), setStatus("Test saved"), reload()))
                .catch((e) => setError(clean(e)))
            }
            className="rounded-lg border border-sky-600 px-3 py-1.5 text-xs font-semibold text-sky-300 disabled:opacity-40"
          >
            Save these picks as a test
          </button>
          <button type="button" onClick={() => setPicks({})} className="text-xs text-slate-400">
            Clear
          </button>
        </div>
      </section>

      <section className="space-y-2 rounded-xl border border-slate-800 p-3">
        <div className="flex items-center gap-2">
          <p className="flex-1 text-sm font-semibold text-slate-200">Model tests ({tests.length})</p>
          <button type="button" onClick={reload} className="text-xs text-sky-400">
            Run again
          </button>
        </div>
        {tests.length === 0 && <p className="text-xs text-slate-500">No tests yet - save a few cases from the sandbox above; releases run them.</p>}
        {tests.map((t) => (
          <div key={t.id} className="flex items-start gap-2 text-xs">
            <span className={t.passed ? "text-emerald-400" : "text-red-400"}>{t.passed ? "✓" : "✗"}</span>
            <div className="min-w-0 flex-1">
              <p className="text-slate-200">
                {t.name} <span className="text-slate-500">- expects {t.expect}</span>
              </p>
              <p className="truncate text-[10px] text-slate-500">{Object.entries(t.picks).map(([k, v]) => `${k}: ${v}`).join(", ") || "no picks"}</p>
              {t.problems?.map((p) => (
                <p key={p} className="text-red-300">
                  {p}
                </p>
              ))}
            </div>
            <button type="button" onClick={() => deleteModelTest(t.id).then(reload)} className="text-slate-600 hover:text-red-400">
              ×
            </button>
          </div>
        ))}
      </section>

      <section className="space-y-2 rounded-xl border border-slate-800 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <p className="flex-1 text-sm font-semibold text-slate-200">Release</p>
          <button type="button" onClick={() => releaseCheck(product).then(setCheck).catch((e) => setError(clean(e)))} className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-200">
            Check before release
          </button>
        </div>
        {check && (
          <div className="space-y-1 text-xs">
            <p className={check.ready ? "text-emerald-400" : "text-red-300"}>{check.ready ? "✓ Ready to release" : "Not ready"}</p>
            {check.blockers.map((b) => (
              <p key={b} className="text-red-300">
                ✗ {b}
              </p>
            ))}
            {check.warnings.map((w) => (
              <p key={w} className="text-amber-300">
                ⚠ {w}
              </p>
            ))}
            {check.qa.filter((q) => q.level === "info").map((q) => (
              <p key={q.text} className="text-slate-400">
                ℹ {q.text}
              </p>
            ))}
            <p className="text-slate-500">
              Regression: {String(check.regression.quotes_checked)} recent quotes and {String(check.regression.assets_checked)} live services re-checked against the workspace.
            </p>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <input type="date" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} className={`${INPUT_CLS} w-44`} title="Effective from (blank = now)" />
          <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What changed" className={`${INPUT_CLS} flex-1`} />
          <button
            type="button"
            onClick={() =>
              releaseModel(product, { valid_from: validFrom ? `${validFrom}T00:00:00` : null, notes })
                .then((v) => (setStatus(`Released version ${v.version}`), setNotes(""), reload(), onChanged()))
                .catch((e) => setError(clean(e)))
            }
            className="rounded-lg bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white"
          >
            Release
          </button>
        </div>
        <div className="space-y-1">
          {versions.map((v) => (
            <div key={v.id} className={`flex items-center gap-2 rounded-lg border border-slate-800 px-3 py-1.5 text-xs ${v.status === "retired" ? "opacity-50" : ""}`}>
              <span className="font-semibold text-slate-200">v{v.version}</span>
              {v.current && <span className="rounded bg-emerald-500/15 px-1.5 text-[10px] text-emerald-300">selling now</span>}
              <span className="text-slate-500">from {new Date(v.valid_from).toLocaleDateString()}</span>
              <span className="flex-1 truncate text-slate-400">{v.notes}</span>
              <button type="button" onClick={() => download(v.version)} className="text-sky-400">
                JSON
              </button>
              {v.status !== "retired" && (
                <button type="button" onClick={() => retireModelVersion(v.id).then(reload)} className="text-slate-400 hover:text-red-300">
                  Retire
                </button>
              )}
            </div>
          ))}
          {versions.length === 0 && <p className="text-xs text-slate-500">Never released - sellers use the workspace directly until the first release.</p>}
        </div>
        <div className="flex gap-3 text-xs">
          <button type="button" onClick={() => download()} className="text-sky-400">
            ⬇ Workspace as JSON (model-as-code)
          </button>
          <label className="cursor-pointer text-sky-400">
            ⬆ Load JSON into the workspace
            <input
              type="file"
              accept=".json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                f.text()
                  .then((text) => loadModelJson(product, JSON.parse(text)))
                  .then((r) => (setStatus(`Loaded ${r.changes.length} changes into the workspace`), onChanged(), reload()))
                  .catch((err) => setError(clean(err)));
              }}
            />
          </label>
        </div>
      </section>
    </div>
  );
}

/** Product classes: members, parent class and the attributes members inherit. */
export function ProductClassesPanel({ products }: { products: string[] }) {
  const [list, setList] = useState<ProductClassOut[]>([]);
  const [draft, setDraft] = useState<{ name: string; parent: string; members: string; description: string } | null>(null);
  const [attr, setAttr] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const reload = useCallback(() => productClasses().then(setList).catch((e) => setError(clean(e))), []);
  useEffect(() => {
    reload();
  }, [reload]);
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">
        A class groups products: its attributes are inherited by every member (a member can override them), and a child product slot can be filled by "any product of
        class X" - new products join without re-modelling the parent.
      </p>
      {error && <p className="text-xs text-red-400">{error}</p>}
      {list.map((c) => (
        <div key={c.id} className="space-y-1.5 rounded-lg border border-slate-800 p-3 text-xs">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-slate-100">{c.name}</span>
            {c.parent && <span className="text-slate-500">under {c.parent}</span>}
            <button
              type="button"
              onClick={() => setDraft({ name: c.name, parent: c.parent ?? "", members: c.members.join(", "), description: c.description })}
              className="ml-auto text-sky-400"
            >
              Edit
            </button>
          </div>
          <p className="text-slate-400">Products: {c.all_members.join(", ") || "none yet"}</p>
          <p className="text-slate-400">Inherited attributes: {c.attributes.map((a) => `${a.name}${a.values.length ? ` (${a.values.join("/")})` : ""}`).join(", ") || "none"}</p>
          <div className="flex gap-2">
            <input
              value={attr[c.name] ?? ""}
              onChange={(e) => setAttr({ ...attr, [c.name]: e.target.value })}
              placeholder="Add attribute: Service Level: Gold, Silver"
              className={`${INPUT_CLS} flex-1`}
            />
            <button
              type="button"
              disabled={!(attr[c.name] ?? "").includes(":") && !(attr[c.name] ?? "").trim()}
              onClick={() => {
                const [name, values = ""] = (attr[c.name] ?? "").split(":");
                saveClassAttribute(c.name, { name: name.trim(), values: values.split(",").map((v) => v.trim()).filter(Boolean), affects_price: false, affects_provisioning: true })
                  .then(() => (setAttr({ ...attr, [c.name]: "" }), reload()))
                  .catch((e) => setError(clean(e)));
              }}
              className="rounded-lg border border-slate-600 px-2 text-slate-200"
            >
              Add
            </button>
          </div>
        </div>
      ))}
      {draft ? (
        <div className="grid gap-2 rounded-lg border border-slate-700 p-3 sm:grid-cols-2">
          <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Class name, e.g. Routers" className={INPUT_CLS} />
          <select value={draft.parent} onChange={(e) => setDraft({ ...draft, parent: e.target.value })} className={INPUT_CLS}>
            <option value="">No parent class</option>
            {list.filter((c) => c.name !== draft.name).map((c) => (
              <option key={c.id}>{c.name}</option>
            ))}
          </select>
          <input value={draft.members} onChange={(e) => setDraft({ ...draft, members: e.target.value })} placeholder="Members, comma-separated" list="model-products" className={`${INPUT_CLS} sm:col-span-2`} />
          <datalist id="model-products">
            {products.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
          <div className="flex gap-2 sm:col-span-2">
            <button
              type="button"
              disabled={!draft.name.trim()}
              onClick={() =>
                saveProductClass({ name: draft.name, parent: draft.parent || null, description: draft.description, members: draft.members.split(",").map((m) => m.trim()).filter(Boolean) })
                  .then(() => (setDraft(null), reload()))
                  .catch((e) => setError(clean(e)))
              }
              className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
            >
              Save class
            </button>
            <button type="button" onClick={() => setDraft(null)} className="text-xs text-slate-400">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" onClick={() => setDraft({ name: "", parent: "", members: "", description: "" })} className="text-sm text-sky-400">
          + New class
        </button>
      )}
    </div>
  );
}
