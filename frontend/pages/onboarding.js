import { useCallback, useEffect, useMemo, useState } from 'react';
import AppShell from '../components/layout/AppShell';
import GuidedSpotlight from '../components/onboarding/GuidedSpotlight';
import Button from '../components/ui/button';
import PageHeader from '../components/ui/page-header';
import {
  chooseOnboardingAiMode,
  askOnboardingHelp,
  fetchLatestMarketplaceDiagnostic,
  fetchMarketplaceDiagnostic,
  fetchOnboardingState,
  recordOnboardingEvent,
  restartOnboarding,
  saveOnboardingSelection,
  saveOnboardingStep,
  skipOnboardingForNow,
  skipOnboardingTask,
  startMarketplaceDiagnostic,
  startOnboarding,
  startGooglePhotosOAuth,
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
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpQuestion, setHelpQuestion] = useState('');
  const [helpAnswer, setHelpAnswer] = useState(null);
  const [helpBusy, setHelpBusy] = useState(false);
  const [tourOpen, setTourOpen] = useState(false);
  const [diagnostic, setDiagnostic] = useState(null);

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
  const diagnosticMarketplace = currentTask?.id?.startsWith('marketplace:') ? currentTask.id.split(':')[1] : '';
  const statusCopy = STATUS_COPY[status] || [status.replaceAll('_', ' '), currentTask?.message || 'PosterPro has not verified this step yet.'];
  const tourSteps = useMemo(() => {
    if (!currentTask) return [];
    if (currentTask.id === 'choose_marketplaces') return [{ selector: '[data-setup-spotlight="marketplace-choice"]', title: 'Choose your selling destinations', body: 'Choose only the marketplaces you want to set up now. You can return and change these later.' }];
    if (currentTask.id === 'ai') return aiMode === 'BYO_OPENAI'
      ? [{ selector: '[data-setup-spotlight="ai-mode-choice"]', title: 'Choose your AI connection', body: 'Your own OpenAI account is available today. PosterPro Managed AI is visible as Coming Soon and cannot be activated here.' }, { selector: '[data-setup-spotlight="ai-key"]', title: 'Paste your private key', body: 'Paste the OpenAI key you just created. PosterPro stores it encrypted and does not show it again.' }, { selector: '[data-setup-spotlight="ai-test"]', title: 'Test the connection', body: 'Choose this button to send a small test request. Setup advances only after OpenAI answers successfully.' }]
      : [{ selector: '[data-setup-spotlight="ai-mode-choice"]', title: 'Choose how PosterPro uses AI', body: 'Choose your own OpenAI key for setup available today, or review the truthful Managed AI coming-soon option.' }];
    if (currentTask.id === 'google_photos') return [{ selector: '[data-setup-spotlight="google-connect"]', title: 'Authorize Google Photos', body: 'PosterPro opens Google sign-in. Choose your account and approve the request, then return here and check status. This does not upload a test photo.' }];
    if (currentTask.id === 'browser_extension') return [{ selector: '[data-setup-spotlight="task-guidance"]', title: 'Connect your browser once', body: 'Follow these install and pairing steps in the browser profile you use for your marketplaces. Heartbeat and job claims run automatically afterwards.' }];
    return [{ selector: '[data-setup-spotlight="task-open-button"]', title: 'Open the service', body: 'Use this button to open the correct setup page. Sign in there normally; do not paste marketplace passwords into PosterPro.' }, { selector: '[data-setup-spotlight="task-guidance"]', title: 'Follow the exact steps', body: 'This panel explains what to click, what you should see, and how to recover. A saved login is not the same as a verified listing-form test.' }];
  }, [currentTask, aiMode]);
  const closeTour = useCallback(() => setTourOpen(false), []);
  const connectGoogle = async () => {
    setBusy(true); setError('');
    try {
      const result = await startGooglePhotosOAuth();
      if (!result?.auth_url) throw new Error('Google did not return a sign-in page. Open Google Photos setup and try again.');
      window.location.assign(result.auth_url);
    } catch (caught) {
      const detail = caught?.detail?.message || caught?.message || 'PosterPro could not start Google sign-in.';
      setError(`${detail} If PosterPro says its Google connection is not configured, contact support. Do not change the shared Google credentials.`);
    } finally { setBusy(false); }
  };
  const askForHelp = async (event) => {
    event.preventDefault();
    if (!helpQuestion.trim() || !currentTask?.id) return;
    setHelpBusy(true); setHelpAnswer(null);
    try {
      const result = await askOnboardingHelp(currentTask.id, helpQuestion.trim());
      setHelpAnswer(result);
    } catch (caught) {
      setHelpAnswer({ answer: caught.message || 'PosterPro could not answer just now. Follow the step-by-step guidance above or contact support without sharing passwords or keys.', mode: 'STEP_GUIDANCE', ai_assisted: false });
    } finally { setHelpBusy(false); }
  };

  useEffect(() => {
    if (snapshot?.started && currentTask?.id === 'ai') void recordOnboardingEvent('AI_SETUP_VIEWED', 'ai').catch(() => {});
  }, [snapshot?.started, currentTask?.id]);

  useEffect(() => {
    let active = true;
    if (!['facebook', 'mercari', 'poshmark', 'vinted', 'offerup'].includes(diagnosticMarketplace)) {
      setDiagnostic(null);
      return () => { active = false; };
    }
    fetchLatestMarketplaceDiagnostic(diagnosticMarketplace).then((value) => { if (active) setDiagnostic(value?.status === 'NOT_RUN' ? null : value); }).catch(() => {});
    return () => { active = false; };
  }, [diagnosticMarketplace]);

  useEffect(() => {
    if (!diagnostic?.id || !['QUEUED', 'CLAIMED', 'NAVIGATING', 'FORM_DETECTED', 'TESTING_FIELDS'].includes(String(diagnostic.status).toUpperCase())) return undefined;
    const timer = setInterval(() => fetchMarketplaceDiagnostic(diagnostic.id).then(setDiagnostic).catch(() => {}), 2500);
    return () => clearInterval(timer);
  }, [diagnostic?.id, diagnostic?.status]);

  const runMarketplaceDiagnostic = async () => {
    if (!diagnosticMarketplace) return;
    setBusy(true); setError('');
    try { setDiagnostic(await startMarketplaceDiagnostic(diagnosticMarketplace)); }
    catch (caught) { setError(caught.message || 'PosterPro could not start the browser form test. Pair the extension and try again.'); }
    finally { setBusy(false); }
  };

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
            <div className="mt-3 flex justify-end"><Button variant="outline" size="sm" onClick={() => setTourOpen(true)}>Show me where to click</Button></div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-blue-600" style={{ width: `${Math.min(100, Math.round(100 * snapshot.progress.completed / Math.max(1, snapshot.progress.required)))}%` }} /></div>
            <p className="mt-6 text-xs font-semibold uppercase tracking-wider text-blue-700">{currentTask.category}</p>
            <h1 className="mt-2 text-2xl font-semibold text-slate-950">{currentTask.title}</h1>
            <p className="mt-2 text-sm leading-6 text-slate-600">{currentTask.purpose}</p>
            {currentTask.id !== 'choose_marketplaces' && currentTask.id !== 'ai' ? <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4"><p className="font-semibold text-slate-900">{statusCopy[0]}</p><p className="mt-1 text-sm text-slate-600">{currentTask.message || statusCopy[1]}</p></div> : null}

            {currentTask.id === 'choose_marketplaces' ? <div className="mt-5 grid gap-3 sm:grid-cols-2">{DESTINATIONS.map(([id, label]) => { const checked = selected.includes(id); return <button data-setup-spotlight={id === 'ebay' ? 'marketplace-choice' : undefined} key={id} type="button" aria-pressed={checked} onClick={() => setSelected((current) => checked ? current.filter((value) => value !== id) : [...current, id])} className={`rounded-xl border p-4 text-left font-semibold transition ${checked ? 'border-blue-500 bg-blue-50 text-blue-900 ring-2 ring-blue-100' : 'border-slate-200 bg-white text-slate-800 hover:border-slate-400'}`}>{checked ? '✓ ' : ''}{label}</button>; })}<p className="sm:col-span-2 text-sm text-slate-500">Choose all you want, or choose none for now. You can add marketplaces later in Settings.</p><div className="sm:col-span-2"><Button disabled={busy} onClick={() => run(() => saveOnboardingSelection(selected))}>Save choices and continue</Button></div></div> : null}

            {currentTask.id === 'ai' ? <div data-setup-spotlight="ai-mode-choice" className="mt-5 space-y-4"><div className="grid gap-3 md:grid-cols-2"><button type="button" onClick={async () => { setAiMode('BYO_OPENAI'); await run(() => chooseOnboardingAiMode('BYO_OPENAI')); }} className={`rounded-2xl border p-5 text-left ${aiMode === 'BYO_OPENAI' ? 'border-blue-500 bg-blue-50 ring-2 ring-blue-100' : 'border-slate-200 bg-white'}`}><span className="text-xs font-bold uppercase tracking-wide text-blue-700">Available now</span><h2 className="mt-2 text-lg font-semibold">Use my own OpenAI account</h2><p className="mt-2 text-sm leading-6 text-slate-600">You connect your key. OpenAI bills your account directly; PosterPro stores it encrypted and never shows it again.</p><p className="mt-3 text-xs text-slate-500">Best for Free plan and users who want direct control.</p></button><button type="button" onClick={async () => { setAiMode('POSTERPRO_SPONSORED'); await run(() => chooseOnboardingAiMode('POSTERPRO_SPONSORED')); }} className={`rounded-2xl border p-5 text-left ${aiMode === 'POSTERPRO_SPONSORED' ? 'border-violet-500 bg-violet-50 ring-2 ring-violet-100' : 'border-violet-200 bg-violet-50/40'}`}><span className="text-xs font-bold uppercase tracking-wide text-violet-700">Paid plan · Coming soon</span><h2 className="mt-2 text-lg font-semibold">Use PosterPro AI</h2><p className="mt-2 text-sm leading-6 text-slate-600">Skip OpenAI setup. PosterPro plans to provide AI on eligible paid plans; paid activation is not available on this deployment.</p><p className="mt-3 text-xs font-semibold text-violet-800">No payment or premium access will be activated here.</p></button></div>
              {aiMode === 'BYO_OPENAI' ? <div className="rounded-2xl border border-slate-200 p-5"><h2 className="font-semibold">Connect your OpenAI account</h2><ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-slate-700"><li>Click <b>Open OpenAI API Keys</b> and sign in.</li><li>Choose <b>Create new secret key</b>, name it PosterPro, and copy it.</li><li>Paste it here, then click <b>Test and connect</b>.</li></ol><p className="mt-3 text-sm text-slate-600"><b>API key:</b> a private password that lets PosterPro use your AI account.</p><a className="mt-4 inline-flex rounded-lg border px-3 py-2 text-sm font-semibold text-blue-700" href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer">Open OpenAI API Keys</a><label data-setup-spotlight="ai-key" className="mt-4 block text-sm font-medium">Paste your key<input autoComplete="off" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-3" placeholder="OpenAI secret key" /></label><div className="mt-3 flex flex-wrap gap-2"><Button data-setup-spotlight="ai-test" disabled={busy || apiKey.length < 16} onClick={async () => { const result = await run(() => testOnboardingOpenAiKey(apiKey)); if (result?.state === 'CONNECTED') setApiKey(''); }}>Test and connect</Button><Button variant="outline" onClick={() => setApiKey('')}>Clear key</Button></div></div> : null}
              {aiMode === 'POSTERPRO_SPONSORED' ? <div role="status" className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950">PosterPro Managed AI is coming soon here. No plan checkout or activation is available on this deployment. You can connect your own OpenAI account or skip AI for now. <a className="ml-1 font-semibold underline" href="#plans">View upcoming plan information</a></div> : null}
              {status === 'CONNECTED' ? <Button variant="outline" disabled={busy} onClick={() => run(() => verifyOnboardingTask('ai'))}>Test AI again</Button> : null}
              {status === 'CONNECTED' ? <Button disabled={busy} onClick={next}>Continue setup</Button> : <Button variant="outline" disabled={busy} onClick={async () => { await run(() => chooseOnboardingAiMode('DISABLED')); await next(); }}>Skip AI for now</Button>}
            </div> : null}

            {currentTask.id === 'google_photos' ? <div className="mt-3 flex flex-wrap gap-2"><Button data-setup-spotlight="google-connect" disabled={busy} onClick={connectGoogle}>{status === 'EXPIRED' ? 'Reconnect Google Photos' : 'Connect Google Photos'}</Button></div> : null}
            {['facebook', 'mercari', 'poshmark', 'vinted', 'offerup'].includes(diagnosticMarketplace) ? <section className="mt-5 rounded-2xl border border-slate-200 bg-slate-50 p-5" aria-label={`${diagnosticMarketplace} real form test`}><h2 className="font-semibold text-slate-950">Test the real {diagnosticMarketplace === 'facebook' ? 'Facebook Marketplace' : diagnosticMarketplace} form</h2><p className="mt-2 text-sm leading-6 text-slate-700">PosterPro will open the create form in your paired browser, use a clearly marked synthetic test, report which fields it could fill, and stop without submitting. Your marketplace password stays in your browser.</p><Button className="mt-4" disabled={busy || ['QUEUED', 'CLAIMED', 'NAVIGATING', 'FORM_DETECTED', 'TESTING_FIELDS'].includes(String(diagnostic?.status || '').toUpperCase())} onClick={runMarketplaceDiagnostic}>Start {diagnosticMarketplace === 'facebook' ? 'Facebook' : diagnosticMarketplace} real form test</Button>{diagnostic ? <div className="mt-4 rounded-xl border bg-white p-4"><p className="font-semibold">Test state: {String(diagnostic.status || 'UNKNOWN').replaceAll('_', ' ')}</p>{diagnostic.status === 'LOGIN_REQUIRED' ? <p className="mt-2 text-sm">You are signed out in this browser. Open the marketplace, sign in normally, then run the test again.</p> : null}{diagnostic.error_detail ? <p className="mt-2 text-sm text-amber-800">{diagnostic.error_detail}</p> : null}{diagnostic.result?.field_results?.length ? <ul className="mt-3 grid gap-2 sm:grid-cols-2">{diagnostic.result.field_results.map((field) => <li key={field.field} className="rounded-lg border p-2 text-sm"><span className="font-semibold">{field.filled ? '✓' : '○'} {field.field}</span><span className="ml-2 text-slate-600">{field.filled ? field.verified_value : field.error_code || 'not detected'}</span></li>)}</ul> : null}<p className="mt-3 text-xs text-slate-500">This diagnostic never submits or publishes a listing.</p></div> : null}</section> : null}
            {currentTask.guidance ? <div data-setup-spotlight="task-guidance" className="mt-5 rounded-2xl border border-blue-100 bg-blue-50/60 p-5"><h2 className="font-semibold text-slate-950">Do this one step at a time</h2><ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-slate-700">{(currentTask.guidance.steps || []).map((step, index) => <li key={`${currentTask.id}-guide-${index}`}>{step}</li>)}</ol>{currentTask.guidance.expect ? <p className="mt-4 rounded-xl bg-white p-3 text-sm leading-6 text-slate-700"><b>What you should see:</b> {currentTask.guidance.expect}</p> : null}{currentTask.guidance.troubleshooting ? <p className="mt-3 text-sm leading-6 text-slate-700"><b>If it does not work:</b> {currentTask.guidance.troubleshooting}</p> : null}<div className="mt-4 flex flex-wrap gap-2">{currentTask.guidance.open_url ? <a data-setup-spotlight="task-open-button" href={currentTask.guidance.open_url} target="_blank" rel="noreferrer" className="inline-flex min-h-10 items-center rounded-lg border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800">{currentTask.guidance.open_label || 'Open website'}</a> : currentTask.deep_link ? <Button data-setup-spotlight="task-open-button" href={currentTask.deep_link} variant="secondary">{currentTask.guidance.open_label || 'Open setup'}</Button> : null}<Button variant="outline" disabled={busy} onClick={() => run(() => verifyOnboardingTask(currentTask.id))}>Check this step again</Button></div></div> : null}
            {verification ? <div role="status" className="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950"><b>{String(verification.level || '').replaceAll('_', ' ')}</b>: {verification.message}</div> : null}
            {currentTask.id === 'pricing' ? <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">PosterPro’s current pricing tools can provide estimates, but a complete sold-comparable and high-value-variant protection workflow is not yet verified. Review prices carefully before publishing.</div> : null}
            {currentTask.id === 'test_workflow' ? <div className="mt-4 space-y-2">{taskList.map((task) => <div key={task.id} className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm"><span>{task.title}</span><span className="font-semibold text-slate-700">{String(task.status || 'NOT_STARTED').replaceAll('_', ' ')}</span></div>)}</div> : null}
          </section>
          <div className="flex flex-wrap justify-between gap-2"><div className="flex gap-2"><Button variant="outline" disabled={stepIndex <= 0 || busy} onClick={() => goToTask(taskList[Math.max(0, stepIndex - 1)].id)}>Back</Button><Button variant="ghost" disabled={busy} onClick={async () => { await run(() => skipOnboardingTask(currentTask.id)); await next(); }}>Skip this step</Button></div><div className="flex gap-2"><Button variant="outline" disabled={busy} onClick={() => setConfirmSkip(true)}>Not right now</Button>{currentTask.id !== 'choose_marketplaces' && currentTask.id !== 'ai' ? <Button disabled={busy} onClick={next}>Next step</Button> : null}</div></div>
          <section className="rounded-2xl border border-violet-200 bg-violet-50/60 p-4" aria-label="Ask PosterPro for setup help"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-semibold text-slate-950">Need help? Ask PosterPro</p><p className="mt-1 text-sm text-slate-600">Ask about this step. PosterPro uses the connected AI provider when available; otherwise it gives the saved step-specific guidance.</p></div><Button type="button" variant="outline" onClick={() => { setHelpOpen((open) => !open); setHelpAnswer(null); }}> {helpOpen ? 'Close help' : 'Ask PosterPro'} </Button></div>{helpOpen ? <form className="mt-4 space-y-3" onSubmit={askForHelp}><label className="block text-sm font-medium text-slate-800">What is confusing about “{currentTask.title}”?<textarea value={helpQuestion} onChange={(event) => setHelpQuestion(event.target.value)} maxLength={1000} rows={3} className="mt-1 block w-full rounded-xl border border-slate-300 bg-white p-3 text-sm" placeholder="For example: I do not see the button you mentioned." /></label><Button type="submit" disabled={helpBusy || !helpQuestion.trim()}>{helpBusy ? 'Getting help…' : 'Send question'}</Button>{helpAnswer ? <div role="status" className="rounded-xl border border-violet-200 bg-white p-4 text-sm leading-6 text-slate-800"><p>{helpAnswer.answer}</p><p className="mt-2 text-xs text-slate-500">{helpAnswer.ai_assisted ? 'AI-assisted using this setup step only.' : 'Step-specific saved guidance.'} Chat is not saved. Do not include passwords, API keys, cookies, or tokens.</p></div> : null}</form> : null}</section>
        </div>
      ) : null}
      {snapshot?.completed ? <section className="rounded-2xl border border-green-200 bg-green-50 p-6"><h2 className="text-xl font-semibold text-green-950">PosterPro is ready at the verified level shown.</h2><p className="mt-2 text-sm text-green-900">No live marketplace listing was published during setup. Review the connection status above before crossposting.</p><div className="mt-4 flex flex-wrap gap-2"><Button href="/inventory">Add your first item</Button><Button href="/listings" variant="outline">Open listings</Button><Button href="/app" variant="outline">Dashboard</Button></div></section> : null}
      {tourOpen && currentTask ? <GuidedSpotlight steps={tourSteps} onClose={closeTour} /> : null}
      <section id="plans" className="mx-auto mt-8 max-w-4xl rounded-2xl border border-violet-200 bg-violet-50/60 p-5"><p className="text-xs font-bold uppercase tracking-wider text-violet-800">Plan preview · coming soon</p><h2 className="mt-2 text-lg font-semibold text-slate-950">A simpler AI option is planned.</h2><p className="mt-2 text-sm leading-6 text-slate-700">PosterPro is preparing Free, Basic, and Premium options. Plan prices, billing, included AI allowances, and activation are not available on this deployment. No upgrade or payment can be completed here.</p></section>
    </AppShell>
  );
}
