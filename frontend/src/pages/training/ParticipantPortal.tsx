import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmt, pubApi } from "./api";
import type { Landing, PlayerForm, PlayerQuestion } from "./api";

/* Participant screens of the training module - no login (ASM-010..016, SES-007/009, WFA-009).
   Mobile-first: one question per screen under 640 px, a progress bar, 44 px tap targets. */

type Answers = Record<string, unknown>;
const tap = "min-h-11 rounded-xl px-4 text-base font-semibold";
const primary = `${tap} bg-sky-600 text-white hover:bg-sky-500 disabled:opacity-40`;
const secondary = `${tap} border border-slate-600 text-slate-200 hover:border-sky-400`;
const input = "min-h-11 w-full rounded-xl border border-slate-600 bg-slate-900 px-3 text-base text-slate-100 focus:border-sky-400 focus:outline-none";

function useNarrow(): boolean {
  const query = "(max-width: 639px)";
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setNarrow(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return narrow;
}

function store(key: string, value?: Answers): Answers | null {
  try {
    if (value === undefined) {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as Answers) : null;
    }
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode - the draft simply isn't kept on this device */
  }
  return null;
}

function forget(key: string) {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

function Shell({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <main className="min-h-screen bg-slate-950 px-4 pb-10 text-slate-100">
      <div className="mx-auto max-w-xl space-y-5 pt-6">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-sky-300">BRT 360 · Training & Learning</p>
        {title && <h1 className="text-2xl font-bold leading-tight">{title}</h1>}
        {children}
      </div>
    </main>
  );
}

/* ---------------------------------------------------------------- one question */

function QuestionField({ q, value, onChange, error }: { q: PlayerQuestion; value: unknown; onChange: (v: unknown) => void; error?: string }) {
  const id = `q-${q.item_key}`;
  const scale = (from: number, to: number, labels?: [string, string]) => (
    <div>
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-labelledby={`${id}-label`}>
        {Array.from({ length: to - from + 1 }, (_, i) => from + i).map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            onClick={() => onChange(n)}
            className={`min-h-11 min-w-11 rounded-xl border text-base font-semibold ${value === n ? "border-sky-400 bg-sky-600 text-white" : "border-slate-600 text-slate-200 hover:border-sky-400"}`}
          >
            {n}
          </button>
        ))}
      </div>
      {labels && (
        <div className="mt-1 flex justify-between text-xs text-slate-400">
          <span>{labels[0]}</span>
          <span>{labels[1]}</span>
        </div>
      )}
    </div>
  );
  let control: React.ReactNode;
  if (q.type === "single" || q.type === "true_false") {
    control = (
      <div className="space-y-2" role="radiogroup" aria-labelledby={`${id}-label`}>
        {q.options.map((o) => (
          <label key={o.i} className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-xl border px-4 py-2 ${value === o.i ? "border-sky-400 bg-sky-500/10" : "border-slate-700"}`}>
            <input type="radio" name={id} className="h-5 w-5 accent-sky-500" checked={value === o.i} onChange={() => onChange(o.i)} />
            <span className="text-base">{o.label}</span>
          </label>
        ))}
      </div>
    );
  } else if (q.type === "multi") {
    const chosen = Array.isArray(value) ? (value as number[]) : [];
    control = (
      <div className="space-y-2" aria-labelledby={`${id}-label`}>
        <p className="text-xs text-slate-400">Choose all that apply.</p>
        {q.options.map((o) => (
          <label key={o.i} className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-xl border px-4 py-2 ${chosen.includes(o.i) ? "border-sky-400 bg-sky-500/10" : "border-slate-700"}`}>
            <input
              type="checkbox"
              className="h-5 w-5 accent-sky-500"
              checked={chosen.includes(o.i)}
              onChange={(e) => onChange(e.target.checked ? [...chosen, o.i] : chosen.filter((x) => x !== o.i))}
            />
            <span className="text-base">{o.label}</span>
          </label>
        ))}
      </div>
    );
  } else if (q.type === "likert5") {
    control = scale(1, 5, q.measure === "confidence" ? ["Not at all confident", "Very confident"] : ["Strongly disagree", "Strongly agree"]);
  } else if (q.type === "rating10") {
    control = scale(1, 10, ["Poor", "Excellent"]);
  } else if (q.type === "nps") {
    control = scale(0, 10, ["Not at all likely", "Extremely likely"]);
  } else {
    const long = q.type === "long_text";
    control = long ? (
      <textarea id={id} maxLength={4000} rows={5} className={`${input} py-2`} value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} />
    ) : (
      <input id={id} maxLength={500} className={input} value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} />
    );
  }
  return (
    <fieldset className={`space-y-3 rounded-2xl border p-4 ${error ? "border-red-500/70 bg-red-500/[0.04]" : "border-slate-800 bg-slate-900/40"}`}>
      <legend id={`${id}-label`} className="px-1 text-lg font-semibold leading-snug">
        {q.text}
        {!q.required && <span className="ml-2 text-xs font-normal text-slate-400">(optional)</span>}
      </legend>
      {q.help && <p className="text-sm text-slate-400">{q.help}</p>}
      {control}
      {error && (
        <p className="text-sm text-red-300" role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}

function answered(v: unknown): boolean {
  return !(v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0));
}

/* ---------------------------------------------------------------- the player */

function Player({
  title,
  questions,
  initial,
  draftKey,
  consentNeeded,
  onAutosave,
  onSubmit,
}: {
  title: string;
  questions: PlayerQuestion[];
  initial: Answers;
  draftKey: string;
  consentNeeded: boolean;
  onAutosave?: (a: Answers) => Promise<unknown>;
  onSubmit: (a: Answers, consent: boolean) => Promise<void>;
}) {
  const narrow = useNarrow();
  const [answers, setAnswers] = useState<Answers>(() => ({ ...(store(draftKey) ?? {}), ...initial }));
  const [index, setIndex] = useState(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [consent, setConsent] = useState(false);
  const [saving, setSaving] = useState<"idle" | "saving" | "saved" | "offline">("idle");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const done = questions.filter((q) => answered(answers[q.item_key])).length;
  const set = useCallback(
    (key: string, value: unknown) => {
      setAnswers((prev) => {
        const next = { ...prev, [key]: value };
        store(draftKey, next); // never lose more than the current answer (ASM-015)
        window.clearTimeout(timer.current);
        if (onAutosave) {
          setSaving("saving");
          timer.current = window.setTimeout(() => {
            onAutosave({ [key]: value })
              .then(() => setSaving("saved"))
              .catch(() => setSaving("offline"));
          }, 500);
        }
        return next;
      });
      setErrors((e) => {
        const rest = { ...e };
        delete rest[key];
        return rest;
      });
    },
    [draftKey, onAutosave],
  );

  async function submit() {
    const missing: Record<string, string> = {};
    for (const q of questions) if (q.required && !answered(answers[q.item_key])) missing[q.item_key] = "Answer this question";
    if (Object.keys(missing).length) {
      setErrors(missing);
      if (narrow) setIndex(questions.findIndex((q) => missing[q.item_key]));
      return;
    }
    if (consentNeeded && !consent) {
      setError("Please confirm the consent statement to submit.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSubmit(answers, consent);
      forget(draftKey);
    } catch (e) {
      const err = e as Error & { fields?: Record<string, string> };
      if (err.fields) setErrors(err.fields);
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const visible = narrow ? [questions[index]] : questions;
  const last = !narrow || index === questions.length - 1;
  return (
    <div className="space-y-5">
      <div>
        <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
          <span>{title}</span>
          <span aria-live="polite">
            {done} of {questions.length} answered
            {saving === "saving" ? " · saving…" : saving === "saved" ? " · saved" : saving === "offline" ? " · saved on this device" : ""}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-800" role="progressbar" aria-valuemin={0} aria-valuemax={questions.length} aria-valuenow={done}>
          <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${(100 * done) / Math.max(1, questions.length)}%` }} />
        </div>
      </div>
      {narrow && (
        <p className="text-sm text-slate-400">
          Question {index + 1} of {questions.length}
        </p>
      )}
      {visible.map((q) => (
        <QuestionField key={q.item_key} q={q} value={answers[q.item_key]} error={errors[q.item_key]} onChange={(v) => set(q.item_key, v)} />
      ))}
      {last && consentNeeded && (
        <label className="flex min-h-11 items-start gap-3 rounded-xl border border-slate-700 p-3 text-sm text-slate-300">
          <input type="checkbox" className="mt-1 h-5 w-5 accent-sky-500" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          <span>I agree that my answers are used to measure this training's impact, as described in the organization's privacy notice. I can ask for my data to be exported or erased.</span>
        </label>
      )}
      {error && (
        <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">
          {error}
        </p>
      )}
      <div className="flex gap-3">
        {narrow && index > 0 && (
          <button type="button" className={secondary} onClick={() => setIndex(index - 1)}>
            Back
          </button>
        )}
        {narrow && !last ? (
          <button type="button" className={`${primary} flex-1`} onClick={() => setIndex(index + 1)}>
            Next
          </button>
        ) : (
          <button type="button" className={`${primary} flex-1`} disabled={busy} onClick={submit}>
            {busy ? "Submitting…" : "Submit"}
          </button>
        )}
      </div>
    </div>
  );
}

function Scores({ scores }: { scores: PlayerForm["scores"] }) {
  if (!scores || !Object.keys(scores).length) return null;
  return (
    <div className="grid grid-cols-2 gap-3">
      {Object.entries(scores).map(([phase, s]) => (
        <div key={phase} className="rounded-xl border border-slate-700 p-3">
          <p className="text-xs uppercase text-slate-400">{phase === "pre" ? "Before" : "After"}</p>
          <p className="text-2xl font-bold">{s.knowledge ?? "—"}%</p>
          <p className="text-xs text-slate-400">confidence {s.confidence ?? "—"} / 5</p>
        </div>
      ))}
    </div>
  );
}

const PHASE_TITLE: Record<string, string> = { pre: "Before your session", post: "After your session", feedback: "Your feedback" };

function AssessmentLink({ token }: { token: string }) {
  const [form, setForm] = useState<PlayerForm | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ scores?: PlayerForm["scores"]; anonymous?: boolean } | null>(null);
  useEffect(() => {
    pubApi
      .form(token)
      .then(setForm)
      .catch((e: Error) => setError(e.message));
  }, [token]);
  const autosave = useCallback((a: Answers) => pubApi.save(token, a), [token]);

  if (error) return <Shell title="This link isn't available">{<p className="text-slate-300">{error}</p>}</Shell>;
  if (!form) return <Shell>{<p className="animate-pulse text-slate-400">Loading…</p>}</Shell>;
  if (done || form.status === "submitted")
    return (
      <Shell title="Thank you - your answers are in">
        <p className="text-slate-300">{done?.anonymous || form.anonymous ? "Your feedback was recorded anonymously." : `Your ${form.type}-session answers for ${form.session} are submitted.`}</p>
        <Scores scores={done?.scores ?? form.scores ?? null} />
      </Shell>
    );
  if (form.status === "not_yet_open")
    return (
      <Shell title={form.session}>
        <p className="text-slate-300">This assessment opens {fmt(form.opens_at)}. Come back then - this link will work.</p>
      </Shell>
    );
  if (form.status === "closed")
    return (
      <Shell title={form.session}>
        <p className="text-slate-300">This assessment is closed. If you think that's a mistake, contact your training coordinator.</p>
      </Shell>
    );
  return (
    <Shell title={form.session}>
      <p className="text-sm text-slate-400">
        {PHASE_TITLE[form.type]} · closes {fmt(form.closes_at)}
        {form.anonymous && " · anonymous - your name is not stored with your answers"}
      </p>
      <Player
        title={form.name ?? PHASE_TITLE[form.type]}
        questions={form.questions ?? []}
        initial={(form.draft as Answers) ?? {}}
        draftKey={`brt-tr-${token.slice(0, 12)}`}
        consentNeeded={!!form.consent_needed}
        onAutosave={form.anonymous ? undefined : autosave}
        onSubmit={async (answers, consent) => {
          const out = await pubApi.submit(token, answers, consent);
          setDone({ scores: out.scores, anonymous: out.anonymous });
        }}
      />
    </Shell>
  );
}

/* ---------------------------------------------------------------- session QR */

function Identify({ code, purpose, onDone }: { code: string; purpose: "assessment" | "checkin"; onDone: (msg: string) => void }) {
  const [email, setEmail] = useState("");
  const [method, setMethod] = useState<"otp" | "phone">("otp");
  const [otpSent, setOtpSent] = useState(false);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  async function go() {
    setBusy(true);
    setError("");
    try {
      const body = { email, ...(method === "otp" ? { otp: secret } : { phone_last4: secret }) };
      if (purpose === "checkin") {
        const out = await pubApi.checkin(code, body);
        onDone(out.late ? "You're checked in (marked late)." : "You're checked in. Enjoy the session!");
      } else {
        const out = await pubApi.identify(code, body);
        window.location.assign(`/p/a/${out.token}`);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
      <label className="block space-y-1">
        <span className="text-sm text-slate-300">Your email (the one you registered with)</span>
        <input className={input} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <div className="flex gap-2" role="tablist">
        {(["otp", "phone"] as const).map((m) => (
          <button key={m} type="button" role="tab" aria-selected={method === m} className={method === m ? primary : secondary} onClick={() => setMethod(m)}>
            {m === "otp" ? "Email me a code" : "Last 4 of my phone"}
          </button>
        ))}
      </div>
      {method === "otp" && !otpSent && (
        <button
          type="button"
          className={secondary}
          disabled={!email.includes("@")}
          onClick={() =>
            pubApi
              .otp(code, email)
              .then((r) => {
                setOtpSent(true);
                setInfo(r.message);
              })
              .catch((e: Error) => setError(e.message))
          }
        >
          Send my code
        </button>
      )}
      {(method === "phone" || otpSent) && (
        <label className="block space-y-1">
          <span className="text-sm text-slate-300">{method === "otp" ? "6-digit code" : "Last 4 digits of your phone"}</span>
          <input className={input} inputMode="numeric" maxLength={method === "otp" ? 6 : 4} value={secret} onChange={(e) => setSecret(e.target.value.replace(/\D/g, ""))} />
        </label>
      )}
      {info && <p className="text-sm text-slate-400">{info}</p>}
      {error && (
        <p role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}
      <button type="button" className={`${primary} w-full`} disabled={busy || !email || secret.length < 4} onClick={go}>
        {purpose === "checkin" ? "Check me in" : "Open my assessment"}
      </button>
    </div>
  );
}

function SessionQr({ code }: { code: string }) {
  const [landing, setLanding] = useState<Landing | null>(null);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"menu" | "checkin" | "assessment" | "anonymous">("menu");
  const [message, setMessage] = useState("");
  useEffect(() => {
    pubApi
      .landing(code)
      .then(setLanding)
      .catch((e: Error) => setError(e.message));
  }, [code]);
  if (error) return <Shell title="This QR code isn't valid">{<p className="text-slate-300">{error}</p>}</Shell>;
  if (!landing) return <Shell>{<p className="animate-pulse text-slate-400">Loading…</p>}</Shell>;
  if (message) return <Shell title={landing.session}>{<p className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-4 text-emerald-200">{message}</p>}</Shell>;
  const identified = landing.phases.filter((p) => !p.anonymous);
  return (
    <Shell title={landing.session}>
      <p className="text-sm text-slate-400">Starts {fmt(landing.starts_at)}</p>
      {mode === "menu" && (
        <div className="grid gap-3">
          {landing.checkin_open && (
            <button type="button" className={primary} onClick={() => setMode("checkin")}>
              ✅ Check in
            </button>
          )}
          {identified.length > 0 && (
            <button type="button" className={landing.checkin_open ? secondary : primary} onClick={() => setMode("assessment")}>
              📝 My assessment
            </button>
          )}
          {landing.anonymous_form && (
            <button type="button" className={secondary} onClick={() => setMode("anonymous")}>
              💬 Anonymous feedback
            </button>
          )}
          {!landing.checkin_open && identified.length === 0 && !landing.anonymous_form && (
            <p className="text-slate-300">Nothing is open for this session right now. Check-in opens 30 minutes before the start.</p>
          )}
        </div>
      )}
      {mode === "checkin" && <Identify code={code} purpose="checkin" onDone={setMessage} />}
      {mode === "assessment" && <Identify code={code} purpose="assessment" onDone={setMessage} />}
      {mode === "anonymous" && landing.anonymous_form && (
        <Player
          title={landing.anonymous_form.name}
          questions={landing.anonymous_form.questions}
          initial={{}}
          draftKey={`brt-tr-anon-${code.slice(0, 10)}`}
          consentNeeded={false}
          onSubmit={async (answers) => {
            const out = await pubApi.anonymous(code, answers);
            setMessage(out.already ? "You've already given feedback from this device - thank you!" : "Thank you - your feedback was recorded anonymously.");
          }}
        />
      )}
      {mode !== "menu" && (
        <button type="button" className="text-sm text-sky-300" onClick={() => setMode("menu")}>
          ← Back
        </button>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- self-registration and unsubscribe */

function Register({ code }: { code: string }) {
  const [page, setPage] = useState<{ session: string; start_local: string | null; open: boolean } | null>(null);
  const [form, setForm] = useState({ name: "", email: "", phone: "", organization: "", consent: false });
  const [error, setError] = useState("");
  const [result, setResult] = useState<string>("");
  useEffect(() => {
    pubApi
      .registration(code)
      .then(setPage)
      .catch((e: Error) => setError(e.message));
  }, [code]);
  if (error && !page) return <Shell title="Registration unavailable">{<p className="text-slate-300">{error}</p>}</Shell>;
  if (!page) return <Shell>{<p className="animate-pulse text-slate-400">Loading…</p>}</Shell>;
  if (result) return <Shell title={page.session}>{<p className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-4 text-emerald-200">{result}</p>}</Shell>;
  return (
    <Shell title={`Register: ${page.session}`}>
      <p className="text-sm text-slate-400">{page.start_local ? new Date(page.start_local).toLocaleString() : ""}</p>
      {!page.open ? (
        <p className="text-slate-300">Registration for this session is closed.</p>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            pubApi
              .register(code, form)
              .then((r) =>
                setResult(
                  r.status === "waitlisted"
                    ? `The session is full - you're number ${r.waitlist_position} on the waitlist. We'll email you if a seat opens.`
                    : "You're registered. Check your email for the confirmation and the pre-session assessment.",
                ),
              )
              .catch((err: Error) => setError(err.message));
          }}
        >
          {(
            [
              ["name", "Full name", "name"],
              ["email", "Email", "email"],
              ["phone", "Phone (optional)", "tel"],
              ["organization", "Organization (optional)", "organization"],
            ] as const
          ).map(([key, label, auto]) => (
            <label key={key} className="block space-y-1">
              <span className="text-sm text-slate-300">{label}</span>
              <input
                className={input}
                autoComplete={auto}
                required={key === "name" || key === "email"}
                type={key === "email" ? "email" : "text"}
                value={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
            </label>
          ))}
          <label className="flex min-h-11 items-start gap-3 text-sm text-slate-300">
            <input type="checkbox" className="mt-1 h-5 w-5 accent-sky-500" checked={form.consent} onChange={(e) => setForm({ ...form, consent: e.target.checked })} />
            <span>I agree that my details are used to run this training and measure its impact.</span>
          </label>
          {error && (
            <p role="alert" className="text-sm text-red-300">
              {error}
            </p>
          )}
          <button className={`${primary} w-full`} disabled={!form.consent}>
            Register
          </button>
        </form>
      )}
    </Shell>
  );
}

function Unsubscribe({ token }: { token: string }) {
  const [state, setState] = useState<"ask" | "done" | "error">("ask");
  return (
    <Shell title="Training reminders">
      {state === "ask" && (
        <>
          <p className="text-slate-300">Stop assessment reminders from this organization? You'll still receive invitations and changes to sessions you're registered for.</p>
          <button type="button" className={primary} onClick={() => pubApi.unsubscribe(token).then(() => setState("done")).catch(() => setState("error"))}>
            Stop reminders
          </button>
        </>
      )}
      {state === "done" && <p className="text-emerald-200">Done - you won't get reminders any more.</p>}
      {state === "error" && <p className="text-red-300">This link isn't valid.</p>}
    </Shell>
  );
}

export default function ParticipantPortal({ kind, value }: { kind: "a" | "q" | "r" | "u"; value: string }) {
  const view = useMemo(() => {
    if (kind === "a") return <AssessmentLink token={value} />;
    if (kind === "q") return <SessionQr code={value} />;
    if (kind === "r") return <Register code={value} />;
    return <Unsubscribe token={value} />;
  }, [kind, value]);
  return view;
}
