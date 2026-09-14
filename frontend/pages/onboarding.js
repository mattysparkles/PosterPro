import { useCallback, useEffect, useMemo, useState } from 'react';
import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import PageHeader from '../components/ui/page-header';
import {
  chooseOnboardingAiMode,
  fetchOnboardingState,
  recordOnboardingEvent,
  restartOnboarding,
  saveOnboardingSelection,
  saveOnboardingStep,
  skipOnboardingForNow,
  skipOnboardingTask,
  startOnboarding,
  testOnboardingOpenAiKey,
  verifyOnboardingTask,
} from '../lib/api';

const DESTINATIONS = [
  ['ebay', 'eBay'], ['facebook', 'Facebook Marketplace'], ['mercari', 'Mercari'],
  ['poshmark', 'Poshmark'], ['vinted', 'Vinted'], ['etsy', 'Etsy'], ['offerup', 'OfferUp'],
];

const STATUS_COPY = {
  CONNECTED: ['Connected', 'Your saved connection passed its recorded check.'],
  CONNECTED_BUT_NEEDS_ATTENTION: ['Sign-in works; finish seller setup', 'The eBay account is connected, but policies or the shipping location are not all verified yet.'],
  ONLINE: ['Online', 'The browser connection checked in recently.'],
  CONNECTED_UNVERIFIED: ['Needs a test', 'A connection is saved, but it has not passed a recent capability check.'],
  COMING_SOON: ['Coming soon', 'Paid PosterPro plan activation is not live here. No payment or upgrade has been activated.'],
  NOT_CONFIGURED: ['Not connected', 'Follow the steps below, then ask PosterPro to check again.'],
  AUTH_REQUIRED: ['Sign-in needed', 'Sign in to this service normally, then return and check again.'],
  EXPIRED: ['Reconnect needed', 'The saved connection may have expired. Reconnect it in Settings.'],
  EXTENSION_REQUIRED: ['Browser connection needed', 'Install and authorize the PosterPro browser connection once.'],
  OFFLINE: ['Browser is offline', 'Open the browser where PosterPro is installed and leave it running.'],
  OPERATOR_TEST_REQUIRED: ['Final check not available yet', 'This deployment has not shipped the non-submitting marketplace form test. You may still configure the account, but PosterPro will not call it automation-ready.'],
  PARTIAL: ['Partially ready', 'This feature is useful but is not yet a complete price-protection system.'],
  NEEDS_ATTENTION: ['Not ready yet', 'One or more selected connections still needs a successful check.'],
  READY: ['Check complete', 'Selected connections are ready at the verified level shown above. No live listing was published.'],
  NOT_STARTED: ['Not started', 'Choose this step when you are ready.'],
  COMPLETE: ['Complete', 'Your choice has been saved.'],
  SKIPPED: ['Skipped for now', 'This is still unresolved and will not be counted as ready.'],
};

export default function OnboardingPage() {
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmSkip, setConfirmSkip] = useState(false);
  const [selected, setSelected] = useState([]);
  const [aiMode, setAiMode] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [verification, setVerification] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const next = await fetchOnboardingState();
      setSnapshot(next);
      setSelected(next.selected_marketplaces || []);
      setAiMode(next.tasks?.find((task) => task.id === 'ai')?.mode || '');
      setError('');
    } catch (caught) {
      setError(caught.message || 'Setup could not be loaded. Try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const taskList = useMemo(() => snapshot?.tasks || [], [snapshot]);
  const currentId = snapshot?.current_step || 'welcome';
  const currentTask = taskList.find((task) => task.id === currentId) || taskList.find((task) => task.status !== 'COMPLETE' && task.status !== 'CONNECTED' && task.status !== 'ONLINE' && task.status !== 'READY') || taskList[0];
  const stepIndex = Math.max(0, taskList.findIndex((task) => task.id === currentTask?.id));

  const run = async (fn) => {
    setBusy(true); setError('');
    try { const value = await fn(); if (value?.tasks) setSnapshot(value); else if (value?.state?.tasks) { setSnapshot(value.state); setVerification(value.verification || null); } else await refresh(); return value; }
    catch (caught) { setError(caught.message || 'That setup step could not be saved.'); return null; }
    finally { setBusy(false); }
  };

  const goToTask = async (taskId) => run(() => saveOnboardingStep(taskId));
  const next = async () => {
    const nextTask = taskList[stepIndex + 1];
    if (nextTask) await goToTask(nextTask.id);
    else await goToTask('test_workflow');
  };

  const status = String(currentTask?.status || 'NOT_STARTED').toUpperCase();
  const statusCopy = STATUS_COPY[status] || [status.replaceAll('_', ' '), currentTask?.message || 'PosterPro has not verified this step yet.'];

  useEffect(() => {
    if (snapshot?.started && currentTask?.id === 'ai') void recordOnboardingEvent('AI_SETUP_VIEWED', 'ai').catch(() => {});
  }, [snapshot?.started, currentTask?.id]);

  return (
    <AppShell active="/onboarding" title="Guided setup" contentWidth="default">
      <PageHeader eyebrow="One step at a time" title="Set up PosterPro" description="Choose what you want to connect. PosterPro checks each step and tells you what is—and is not—ready." actions={<Button variant="outline" onClick={() => run(restartOnboarding)}>Restart setup</Button>} />
      {error ? <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div> : null}
      {loading && !snapshot ? <div className="rounded-2xl border bg-white p-6 text-slate-600">Loading your saved setup…</div> : null}
      {snapshot && !snapshot.started && !snapshot.welcome_dismissed ? (
        <section className="mx-auto max-w-3xl rounded-3xl border border-slate-200 bg-white p-6 shadow-sm sm:p-10">
          <p className="text-sm font-semibold uppercase tracking-wider text-blue-700">Welcome to PosterPro</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">I’ll help you get everything ready.</h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600">We’ll do one small step at a time. PosterPro will check each connection before calling it ready. You can skip steps and come back whenever you like.</p>
          <div className="mt-7 flex flex-wrap gap-3"><Button disabled={busy} onClick={() => run(startOnboarding)}>Start setup</Button><Button variant="outline" disabled={busy} onClick={() => setConfirmSkip(true)}>Not right now</Button></div>
        </section>
      ) : null}
      {confirmSkip ? <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-semibold">Are you sure?</h2><p className="mt-2 text-sm leading-6 text-slate-600">PosterPro works best after connecting the services you plan to use. You can restart setup anytime from Dashboard or Settings.</p><div className="mt-5 flex flex-wrap gap-2"><Button onClick={() => { setConfirmSkip(false); void run(startOnboarding); }}>Continue setup</Button><Button variant="outline" onClick={async () => { setConfirmSkip(false); await run(skipOnboardingForNow); }}>Skip for now</Button></div></div></div> : null}
      {snapshot?.started && currentTask ? (
        <div className="mx-auto max-w-4xl space-y-5">
          <section className="rounded-2xl border bg-white p-5 shadow-sm sm:p-7">
            <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm font-medium text-slate-500">Step {stepIndex + 1} of {taskList.length}</p><p className="text-sm font-semibold text-slate-700">{snapshot.progress.completed} of {snapshot.progress.required} required steps verified</p></div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-blue-600" style={{ width: `${Math.min(100, Math.round(100 * snapshot.progress.completed / Math.max(1, snapshot.progress.required)))}%` }} /></div>
            <p className="mt-6 text-xs font-semibold uppercase tracking-wider text-blue-700">{currentTask.category}</p>
            <h1 className="mt-2 text-2xl font-semibold text-slate-950">{currentTask.title}</h1>
            <p className="mt-2 text-sm leading-6 text-slate-600">{currentTask.purpose}</p>
            {currentTask.id !== 'choose_marketplaces' && currentTask.id !== 'ai' ? <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4"><p className="font-semibold text-slate-900">{statusCopy[0]}</p><p className="mt-1 text-sm text-slate-600">{currentTask.message || statusCopy[1]}</p></div> : null}

            {currentTask.id === 'choose_marketplaces' ? <div className="mt-5 grid gap-3 sm:grid-cols-2">{DESTINATIONS.map(([id, label]) => { const checked = selected.includes(id); return <button key={id} type="button" aria-pressed={checked} onClick={() => setSelected((current) => checked ? current.filter((value) => value !== id) : [...current, id])} className={`rounded-xl border p-4 text-left font-semibold transition ${checked ? 'border-blue-500 bg-blue-50 text-blue-900 ring-2 ring-blue-100' : 'border-slate-200 bg-white text-slate-800 hover:border-slate-400'}`}>{checked ? '✓ ' : ''}{label}</button>; })}<p className="sm:col-span-2 text-sm text-slate-500">Choose all you want, or choose none for now. You can add marketplaces later in Settings.</p><div className="sm:col-span-2"><Button disabled={busy} onClick={() => run(() => saveOnboardingSelection(selected))}>Save choices and continue</Button></div></div> : null}

            {currentTask.id === 'ai' ? <div className="mt-5 space-y-4"><div className="grid gap-3 md:grid-cols-2"><button type="button" onClick={async () => { setAiMode('BYO_OPENAI'); await run(() => chooseOnboardingAiMode('BYO_OPENAI')); }} className={`rounded-2xl border p-5 text-left ${aiMode === 'BYO_OPENAI' ? 'border-blue-500 bg-blue-50 ring-2 ring-blue-100' : 'border-slate-200 bg-white'}`}><span className="text-xs font-bold uppercase tracking-wide text-blue-700">Available now</span><h2 className="mt-2 text-lg font-semibold">Use my own OpenAI account</h2><p className="mt-2 text-sm leading-6 text-slate-600">You connect your key. OpenAI bills your account directly; PosterPro stores it encrypted and never shows it again.</p><p className="mt-3 text-xs text-slate-500">Best for Free plan and users who want direct control.</p></button><button type="button" onClick={async () => { setAiMode('POSTERPRO_SPONSORED'); await run(() => chooseOnboardingAiMode('POSTERPRO_SPONSORED')); }} className={`rounded-2xl border p-5 text-left ${aiMode === 'POSTERPRO_SPONSORED' ? 'border-violet-500 bg-violet-50 ring-2 ring-violet-100' : 'border-violet-200 bg-violet-50/40'}`}><span className="text-xs font-bold uppercase tracking-wide text-violet-700">Paid plan · Coming soon</span><h2 className="mt-2 text-lg font-semibold">Use PosterPro AI</h2><p className="mt-2 text-sm leading-6 text-slate-600">Skip OpenAI setup. PosterPro plans to provide AI on eligible paid plans; paid activation is not available on this deployment.</p><p className="mt-3 text-xs font-semibold text-violet-800">No payment or premium access will be activated here.</p></button></div>
              {aiMode === 'BYO_OPENAI' ? <div className="rounded-2xl border border-slate-200 p-5"><h2 className="font-semibold">Connect your OpenAI account</h2><ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-slate-700"><li>Click <b>Open OpenAI API Keys</b> and sign in.</li><li>Choose <b>Create new secret key</b>, name it PosterPro, and copy it.</li><li>Paste it here, then click <b>Test and connect</b>.</li></ol><p className="mt-3 text-sm text-slate-600"><b>API key:</b> a private password that lets PosterPro use your AI account.</p><a className="mt-4 inline-flex rounded-lg border px-3 py-2 text-sm font-semibold text-blue-700" href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer">Open OpenAI API Keys</a><label className="mt-4 block text-sm font-medium">Paste your key<input autoComplete="off" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-3" placeholder="OpenAI secret key" /></label><div className="mt-3 flex flex-wrap gap-2"><Button disabled={busy || apiKey.length < 16} onClick={async () => { const result = await run(() => testOnboardingOpenAiKey(apiKey)); if (result?.state === 'CONNECTED') setApiKey(''); }}>Test and connect</Button><Button variant="outline" onClick={() => setApiKey('')}>Clear key</Button></div></div> : null}
              {aiMode === 'POSTERPRO_SPONSORED' ? <div role="status" className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950">PosterPro Managed AI is coming soon here. No plan checkout or activation is available on this deployment. You can connect your own OpenAI account or skip AI for now. <a className="ml-1 font-semibold underline" href="#plans">View upcoming plan information</a></div> : null}
              {status === 'CONNECTED' ? <Button variant="outline" disabled={busy} onClick={() => run(() => verifyOnboardingTask('ai'))}>Test AI again</Button> : null}
              {status === 'CONNECTED' ? <Button disabled={busy} onClick={next}>Continue setup</Button> : <Button variant="outline" disabled={busy} onClick={async () => { await run(() => chooseOnboardingAiMode('DISABLED')); await next(); }}>Skip AI for now</Button>}
            </div> : null}

            {currentTask.id?.startsWith('marketplace:') || currentTask.id === 'google_photos' || currentTask.id === 'browser_extension' ? <div className="mt-5 flex flex-wrap gap-2"><Button href={currentTask.deep_link || '/settings'} variant="outline">Open setup</Button><Button variant="outline" disabled={busy} onClick={() => run(() => verifyOnboardingTask(currentTask.id))}>{currentTask.id === 'marketplace:ebay' ? 'Run eBay account test' : 'Refresh saved status'}</Button></div> : null}
            {verification ? <div role="status" className="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950"><b>{String(verification.level || '').replaceAll('_', ' ')}</b>: {verification.message}</div> : null}
            {currentTask.id === 'pricing' ? <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">PosterPro’s current pricing tools can provide estimates, but a complete sold-comparable and high-value-variant protection workflow is not yet verified. Review prices carefully before publishing.</div> : null}
            {currentTask.id === 'test_workflow' ? <div className="mt-4 space-y-2">{taskList.map((task) => <div key={task.id} className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm"><span>{task.title}</span><span className="font-semibold text-slate-700">{String(task.status || 'NOT_STARTED').replaceAll('_', ' ')}</span></div>)}</div> : null}
          </section>
          <div className="flex flex-wrap justify-between gap-2"><div className="flex gap-2"><Button variant="outline" disabled={stepIndex <= 0 || busy} onClick={() => goToTask(taskList[Math.max(0, stepIndex - 1)].id)}>Back</Button><Button variant="ghost" disabled={busy} onClick={async () => { await run(() => skipOnboardingTask(currentTask.id)); await next(); }}>Skip this step</Button></div><div className="flex gap-2"><Button variant="outline" disabled={busy} onClick={() => setConfirmSkip(true)}>Not right now</Button>{currentTask.id !== 'choose_marketplaces' && currentTask.id !== 'ai' ? <Button disabled={busy} onClick={next}>Next step</Button> : null}</div></div>
          <p className="text-center text-xs text-slate-500">Need help? Open the step’s setup page for detailed instructions. PosterPro never asks for marketplace passwords or browser cookies.</p>
        </div>
      ) : null}
      {snapshot?.completed ? <section className="rounded-2xl border border-green-200 bg-green-50 p-6"><h2 className="text-xl font-semibold text-green-950">PosterPro is ready at the verified level shown.</h2><p className="mt-2 text-sm text-green-900">No live marketplace listing was published during setup. Review the connection status above before crossposting.</p><div className="mt-4 flex flex-wrap gap-2"><Button href="/inventory">Add your first item</Button><Button href="/listings" variant="outline">Open listings</Button><Button href="/app" variant="outline">Dashboard</Button></div></section> : null}
      <section id="plans" className="mx-auto mt-8 max-w-4xl rounded-2xl border border-violet-200 bg-violet-50/60 p-5"><p className="text-xs font-bold uppercase tracking-wider text-violet-800">Plan preview · coming soon</p><h2 className="mt-2 text-lg font-semibold text-slate-950">A simpler AI option is planned.</h2><p className="mt-2 text-sm leading-6 text-slate-700">PosterPro is preparing Free, Basic, and Premium options. Plan prices, billing, included AI allowances, and activation are not available on this deployment. No upgrade or payment can be completed here.</p></section>
    </AppShell>
  );
}
