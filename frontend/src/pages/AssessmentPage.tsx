import { useEffect, useState } from 'react';
import { request } from '../api/client';
type Question = { key: string; prompt: string; options: string[]; outcome: string };
export default function AssessmentPage({ token }: { token: string }) {
  const [form, setForm] = useState<{ title: string; phase: string; questions: Question[] } | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [confidence, setConfidence] = useState(3);
  const [feedback, setFeedback] = useState('');
  const [anonymous, setAnonymous] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { request<typeof form>(`/api/assessments/${encodeURIComponent(token)}`).then(setForm).catch(e => setError(String(e))); }, [token]);
  async function submit() {
    setBusy(true); setError('');
    try { await request(`/api/assessments/${encodeURIComponent(token)}`, { method: 'POST', body: JSON.stringify({ answers, confidence, feedback, anonymous }) }); setDone(true); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <main className="min-h-screen bg-slate-950 p-5 text-slate-100"><div className="mx-auto max-w-xl space-y-6 py-8">
    <p className="text-sm text-sky-300">BRT 360 · Training & Learning</p>
    <h1 className="text-2xl font-bold">{done ? 'Thank you for your response' : form?.title ?? 'Assessment'}</h1>
    {error && <p role="alert" className="text-red-300">{error}</p>}
    {form && !done && <form className="space-y-6" onSubmit={e => { e.preventDefault(); void submit(); }}>
      <p className="text-slate-400">{form.phase === 'pre' ? 'Before your session' : 'After your session'} · Complete every question.</p>
      {form.questions.map(q => <fieldset key={q.key} className="space-y-2 rounded-xl border border-slate-700 p-4"><legend className="px-2 font-semibold">{q.prompt}</legend>{q.options.map((o, i) => <label key={i} className="flex min-h-11 items-center gap-3"><input required type="radio" name={q.key} checked={answers[q.key] === i} onChange={() => setAnswers({ ...answers, [q.key]: i })} />{o}</label>)}</fieldset>)}
      <label className="block">Confidence in applying this learning (1–5)<select className="mt-2 w-full rounded border border-slate-600 bg-slate-900 p-3" value={confidence} onChange={e => setConfidence(Number(e.target.value))}>{[1,2,3,4,5].map(n => <option key={n}>{n}</option>)}</select></label>
      <label className="block">Feedback<textarea maxLength={4000} className="mt-2 w-full rounded border border-slate-600 bg-slate-900 p-3" value={feedback} onChange={e => setFeedback(e.target.value)} /></label>
      <label className="flex items-center gap-3"><input type="checkbox" checked={anonymous} onChange={e => setAnonymous(e.target.checked)} />Submit anonymously</label>
      <p className="text-xs text-slate-400">Anonymous answers are included in group results without your name. Identified answers allow your before/after results to be compared.</p>
      <button disabled={busy} className="w-full rounded bg-sky-600 p-3 font-bold disabled:opacity-50">{busy ? 'Submitting…' : 'Submit assessment'}</button>
    </form>}
  </div></main>;
}
