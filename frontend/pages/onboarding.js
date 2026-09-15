import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AppShell from '../components/layout/AppShell';
import GuidedSpotlight from '../components/onboarding/GuidedSpotlight';
import MarketplaceDiagnosticReport from '../components/marketplaces/MarketplaceDiagnosticReport';
import Button from '../components/ui/button';
import PageHeader from '../components/ui/page-header';
import {
  chooseOnboardingAiMode,
  askOnboardingHelp,
  fetchLatestMarketplaceDiagnostic,
  fetchMarketplaceDiagnosticHistory,
  fetchMarketplaceDiagnostic,
  fetchMarketplaceExtensionDevices,
  createMarketplaceExtensionPairingCode,
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

function versionLessThan(installed, required) {
  const left = String(installed || '').split('.').map((part) => Number.parseInt(part, 10) || 0);
  const right = String(required || '').split('.').map((part) => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    if ((left[index] || 0) !== (right[index] || 0)) return (left[index] || 0) < (right[index] || 0);
  }
  return false;
}

const STATUS_COPY = {
  CONNECTED: ['Connected', 'Your saved connection passed its recorded check.'],
  CONNECTED_BUT_NEEDS_ATTENTION: ['Sign-in works; finish seller setup', 'The eBay account is connected, but policies or the shipping location are not all verified yet.'],
  ONLINE: ['Online', 'The browser connection checked in recently.'],
  CONNECTED_UNVERIFIED: ['Needs a test', 'A connection is saved, but it has not passed a recent capability check.'],
  COMING_SOON: ['Coming soon', 'Paid PosterPro plan activation is not live here. No payment or upgrade has been activated.'],
  NOT_CONFIGURED: ['Not connected', 'Follow the steps below, then ask PosterPro to check again.'],
  AUTH_REQUIRED: ['Sign-in needed', 'Sign in to this service normally, then return and check again.'],
  EXPIRED: ['Reconnect needed', 'The saved connection may have expired. Reconnect it in Settings.'],
  EXTENSION_REQUIRED: ['Browser connection needed', 'Install the PosterPro extension in Chrome or Edge.'],
  OFFLINE: ['Browser is offline', 'Open PosterPro in the browser where the extension is installed.'],
  OPERATOR_TEST_REQUIRED: ['Run the real-browser test', 'PosterPro will test the live form in your paired browser, report required fields, and stop without submitting.'],
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
  const [diagnosticHistory, setDiagnosticHistory] = useState([]);
  const [extensionState, setExtensionState] = useState({ devices: [], current_version: 'unknown', minimum_version: 'unknown' });
  const [extensionDetected, setExtensionDetected] = useState(false);
  const [detectedExtensionVersion, setDetectedExtensionVersion] = useState('');
  const [browserExtensionDeviceId, setBrowserExtensionDeviceId] = useState(null);
  const [installGuideOpen, setInstallGuideOpen] = useState(false);
  const browserVerificationDeviceRef = useRef(null);

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

  useEffect(() => {
    const receiveExtensionPresence = (event) => {
      if (event.source !== window || event.origin !== window.location.origin) return;
      const message = event.data || {};
      if (message.source !== 'posterpro-extension') return;
      if (message.type === 'PRESENCE' || message.type === 'AUTHORIZED') {
        setExtensionDetected(true);
        setDetectedExtensionVersion(String(message.version || ''));
        const deviceId = Number(message.device_id || message.device?.id);
        if (Number.isInteger(deviceId) && deviceId > 0) setBrowserExtensionDeviceId(deviceId);
      }
      if (message.type === 'AUTHORIZED') {
        void fetchMarketplaceExtensionDevices().then((value) => setExtensionState(value || { devices: [] })).catch(() => {});
      }
    };
    window.addEventListener('message', receiveExtensionPresence);
    window.postMessage({ source: 'posterpro-settings', type: 'CHECK_EXTENSION' }, window.location.origin);
    return () => window.removeEventListener('message', receiveExtensionPresence);
  }, []);

  const taskList = useMemo(() => snapshot?.tasks || [], [snapshot]);
  const currentId = snapshot?.current_step || 'welcome';
  const currentTask = taskList.find((task) => task.id === currentId) || taskList.find((task) => task.status !== 'COMPLETE' && task.status !== 'CONNECTED' && task.status !== 'ONLINE' && task.status !== 'READY') || taskList[0];
  const stepIndex = Math.max(0, taskList.findIndex((task) => task.id === currentTask?.id));

  useEffect(() => {
    let active = true;
    const refreshDevices = async () => {
      try {
        const value = await fetchMarketplaceExtensionDevices();
        if (!active) return;
        const next = value || { devices: [] };
        setExtensionState(next);
        const now = Date.now();
        const onlineDevice = (next.devices || []).filter((device) => !device.revoked && browserExtensionDeviceId && device.id === browserExtensionDeviceId && device.last_seen_at && now - new Date(device.last_seen_at).getTime() < 120000 && !device.update_required).sort((a, b) => new Date(b.last_seen_at) - new Date(a.last_seen_at))[0];
        if (onlineDevice && currentTask?.id === 'browser_extension' && browserVerificationDeviceRef.current !== onlineDevice.id) {
          browserVerificationDeviceRef.current = onlineDevice.id;
          const result = await verifyOnboardingTask('browser_extension');
          if (!active) return;
          if (result?.tasks) setSnapshot(result);
          else if (result?.state?.tasks) setSnapshot(result.state);
          else setSnapshot(await fetchOnboardingState());
        }
      } catch { /* keep last good state visible while polling */ }
    };
    void refreshDevices();
    const timer = setInterval(refreshDevices, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [browserExtensionDeviceId, currentTask?.id]);

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
  const browserOnline = Boolean(browserExtensionDeviceId && (extensionState.devices || []).some((device) => !device.revoked && device.id === browserExtensionDeviceId && !device.update_required && device.last_seen_at && Date.now() - new Date(device.last_seen_at).getTime() < 120000));
  const extensionNeedsUpdate = (extensionState.devices || []).some((device) => !device.revoked && device.id === browserExtensionDeviceId && device.last_seen_at && Date.now() - new Date(device.last_seen_at).getTime() < 120000 && device.update_required)
    || Boolean(extensionDetected && detectedExtensionVersion && versionLessThan(detectedExtensionVersion, extensionState.minimum_version));
  const tourSteps = useMemo(() => {
    if (!currentTask) return [];
    if (currentTask.id === 'choose_marketplaces') return [{ selector: '[data-setup-spotlight="marketplace-choice"]', title: 'Choose your selling destinations', body: 'Choose only the marketplaces you want to set up now. You can return and change these later.' }];
    if (currentTask.id === 'ai') return aiMode === 'BYO_OPENAI'
      ? [{ selector: '[data-setup-spotlight="ai-mode-choice"]', title: 'Choose your AI connection', body: 'Your own OpenAI account is available today. PosterPro Managed AI is visible as Coming Soon and cannot be activated here.' }, { selector: '[data-setup-spotlight="ai-key"]', title: 'Paste your private key', body: 'Paste the OpenAI key you just created. PosterPro stores it encrypted and does not show it again.' }, { selector: '[data-setup-spotlight="ai-test"]', title: 'Test the connection', body: 'Choose this button to send a small test request. Setup advances only after OpenAI answers successfully.' }]
      : [{ selector: '[data-setup-spotlight="ai-mode-choice"]', title: 'Choose how PosterPro uses AI', body: 'Choose your own OpenAI key for setup available today, or review the truthful Managed AI coming-soon option.' }];
    if (currentTask.id === 'google_photos') return [{ selector: '[data-setup-spotlight="google-connect"]', title: 'Authorize Google Photos', body: 'PosterPro opens Google sign-in. Choose your account and approve the request, then return here and check status. This does not upload a test photo.' }];
    if (currentTask.id === 'browser_extension') return [{ selector: '[data-setup-spotlight="browser-primary-action"]', title: 'Connect your browser', body: 'PosterPro checks for this connection automatically. If the extension is not installed yet, use the install button and follow the short Chrome or Edge steps.' }];
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
    fetchMarketplaceDiagnosticHistory(diagnosticMarketplace, 5).then((value) => { if (active) setDiagnosticHistory(Array.isArray(value) ? value : []); }).catch(() => {});
    return () => { active = false; };
  }, [diagnosticMarketplace]);

  useEffect(() => {
    if (!diagnostic?.id || ['QUEUED', 'CLAIMED', 'NAVIGATING', 'FORM_DETECTED', 'TESTING_FIELDS'].includes(String(diagnostic.status).toUpperCase())) return;
    fetchMarketplaceDiagnosticHistory(diagnostic.marketplace || diagnosticMarketplace, 5).then((value) => setDiagnosticHistory(Array.isArray(value) ? value : [])).catch(() => {});
  }, [diagnostic?.id, diagnostic?.status, diagnosticMarketplace]);

  useEffect(() => {
    if (!diagnostic?.id || !['QUEUED', 'CLAIMED', 'NAVIGATING', 'FORM_DETECTED', 'TESTING_FIELDS'].includes(String(diagnostic.status).toUpperCase())) return undefined;
    const timer = setInterval(() => fetchMarketplaceDiagnostic(diagnostic.id).then(setDiagnostic).catch(() => {}), 2500);
    return () => clearInterval(timer);
  }, [diagnostic?.id, diagnostic?.status]);

  const runMarketplaceDiagnostic = async () => {
    if (!diagnosticMarketplace) return;
    setBusy(true); setError('');
    try { setDiagnostic(await startMarketplaceDiagnostic(diagnosticMarketplace)); }
    catch (caught) { setError(caught.message || 'PosterPro could not start the browser form test. Connect this browser, then try again.'); }
    finally { setBusy(false); }
  };

  const connectThisBrowser = async () => {
    setBusy(true); setError('');
    try {
      const result = await createMarketplaceExtensionPairingCode('PosterPro browser');
      window.postMessage({ source: 'posterpro-settings', type: 'PAIR_EXTENSION', pairing_code: result.pairing_code }, window.location.origin);
      setExtensionDetected(true);
    } catch (caught) {
      setError(caught.message || 'PosterPro could not connect this browser. Reload this page and try again.');
    } finally { setBusy(false); }
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
            {currentTask.id !== 'choose_marketplaces' && currentTask.id !== 'ai' && currentTask.id !== 'browser_extension' ? <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4"><p className="font-semibold text-slate-900">{statusCopy[0]}</p><p className="mt-1 text-sm text-slate-600">{currentTask.message || statusCopy[1]}</p></div> : null}

            {currentTask.id === 'choose_marketplaces' ? <div data-setup-spotlight="marketplace-choice" className="mt-5 space-y-4"><div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => setSelected(DESTINATIONS.map(([id]) => id))}>Select all</Button><Button variant="outline" size="sm" onClick={() => setSelected([])}>Clear all</Button></div><div className="grid gap-3 sm:grid-cols-2">{DESTINATIONS.map(([id, label]) => { const checked = selected.includes(id); const note = id === 'ebay' ? 'Connect your seller account directly.' : 'Uses the PosterPro browser connection.'; return <label key={id} className={`flex min-h-[84px] cursor-pointer items-center gap-4 rounded-xl border p-4 transition focus-within:ring-2 focus-within:ring-blue-600 ${checked ? 'border-blue-600 bg-blue-50 ring-1 ring-blue-200' : 'border-slate-300 bg-white hover:border-slate-500'}`}><input type="checkbox" checked={checked} onChange={() => setSelected((current) => checked ? current.filter((value) => value !== id) : [...current, id])} className="h-5 w-5 shrink-0 accent-blue-700" aria-label={`Set up ${label}`} /><span><span className="block text-base font-semibold text-slate-950">{label}</span><span className="mt-1 block text-sm text-slate-600">{note}</span></span></label>; })}</div><p className="text-sm text-slate-600">Only the marketplaces you select become setup steps. You can change this later.</p><Button disabled={busy} onClick={() => run(() => saveOnboardingSelection(selected))}>Save choices and continue</Button></div> : null}

            {currentTask.id === 'ai' ? <div data-setup-spotlight="ai-mode-choice" className="mt-5 space-y-4"><div className="grid gap-3 md:grid-cols-2"><button type="button" onClick={async () => { setAiMode('BYO_OPENAI'); await run(() => chooseOnboardingAiMode('BYO_OPENAI')); }} className={`rounded-2xl border p-5 text-left ${aiMode === 'BYO_OPENAI' ? 'border-blue-500 bg-blue-50 ring-2 ring-blue-100' : 'border-slate-200 bg-white'}`}><span className="text-xs font-bold uppercase tracking-wide text-blue-700">Available now</span><h2 className="mt-2 text-lg font-semibold">Use my own OpenAI account</h2><p className="mt-2 text-sm leading-6 text-slate-600">You connect your key. OpenAI bills your account directly; PosterPro stores it encrypted and never shows it again.</p><p className="mt-3 text-xs text-slate-500">Best for Free plan and users who want direct control.</p></button><button type="button" onClick={async () => { setAiMode('POSTERPRO_SPONSORED'); await run(() => chooseOnboardingAiMode('POSTERPRO_SPONSORED')); }} className={`rounded-2xl border p-5 text-left ${aiMode === 'POSTERPRO_SPONSORED' ? 'border-violet-500 bg-violet-50 ring-2 ring-violet-100' : 'border-violet-200 bg-violet-50'}`}><span className="text-xs font-bold uppercase tracking-wide text-violet-700">Paid plan · Coming soon</span><h2 className="mt-2 text-lg font-semibold">Use PosterPro AI</h2><p className="mt-2 text-sm leading-6 text-slate-600">Skip OpenAI setup. PosterPro plans to provide AI on eligible paid plans; paid activation is not available on this deployment.</p><p className="mt-3 text-xs font-semibold text-violet-800">No payment or premium access will be activated here.</p></button></div>
              {aiMode === 'BYO_OPENAI' ? <div className="rounded-2xl border border-slate-200 p-5"><h2 className="font-semibold">Connect your OpenAI account</h2><ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-slate-700"><li>Click <b>Open OpenAI API Keys</b> and sign in.</li><li>Choose <b>Create new secret key</b>, name it PosterPro, and copy it.</li><li>Paste it here, then click <b>Test and connect</b>.</li></ol><p className="mt-3 text-sm text-slate-600"><b>API key:</b> a private password that lets PosterPro use your AI account.</p><a className="mt-4 inline-flex rounded-lg border px-3 py-2 text-sm font-semibold text-blue-700" href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer">Open OpenAI API Keys</a><label data-setup-spotlight="ai-key" className="mt-4 block text-sm font-medium">Paste your key<input autoComplete="off" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-3" placeholder="OpenAI secret key" /></label><div className="mt-3 flex flex-wrap gap-2"><Button data-setup-spotlight="ai-test" disabled={busy || apiKey.length < 16} onClick={async () => { const result = await run(() => testOnboardingOpenAiKey(apiKey)); if (result?.state === 'CONNECTED') setApiKey(''); }}>Test and connect</Button><Button variant="outline" onClick={() => setApiKey('')}>Clear key</Button></div></div> : null}
              {aiMode === 'POSTERPRO_SPONSORED' ? <div role="status" className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950">PosterPro Managed AI is coming soon here. No plan checkout or activation is available on this deployment. You can connect your own OpenAI account or skip AI for now. <a className="ml-1 font-semibold underline" href="#plans">View upcoming plan information</a></div> : null}
              {status === 'CONNECTED' ? <Button variant="outline" disabled={busy} onClick={() => run(() => verifyOnboardingTask('ai'))}>Test AI again</Button> : null}
              {status === 'CONNECTED' ? <Button disabled={busy} onClick={next}>Continue setup</Button> : <Button variant="outline" disabled={busy} onClick={async () => { await run(() => chooseOnboardingAiMode('DISABLED')); await next(); }}>Skip AI for now</Button>}
            </div> : null}

            {currentTask.id === 'browser_extension' ? <div className="mt-5 rounded-2xl border border-slate-200 bg-white p-5" data-setup-spotlight="task-guidance"><h2 className="text-lg font-semibold text-slate-950">Connect your browser</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-700">PosterPro uses a small extension in Chrome or Edge to work with marketplace accounts you already use. Your passwords stay in your browser.</p>{extensionNeedsUpdate ? <div role="status" className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-4"><p className="font-semibold text-amber-950">PosterPro extension update required</p><p className="mt-1 text-sm text-amber-900">Install version {extensionState.current_version || 'current'} or newer. Your older browser connections will remain saved.</p><Button className="mt-3" href="/api/browser-extension/download" variant="default" data-setup-spotlight="browser-primary-action">Update extension</Button></div> : browserOnline ? <div role="status" className="mt-4 rounded-xl border border-green-300 bg-green-50 p-4"><p className="font-semibold text-green-950">Browser connected ✓</p><p className="mt-1 text-sm text-green-900">{(extensionState.devices || []).find((device) => device.id === browserExtensionDeviceId)?.browser || 'Chrome or Edge'} · PosterPro extension v{(extensionState.devices || []).find((device) => device.id === browserExtensionDeviceId)?.extension_version || extensionState.current_version}</p><p className="mt-1 text-sm text-green-900">Connection is checked automatically. Continue when you are ready.</p><Button className="mt-3" disabled={busy} onClick={next}>Continue</Button></div> : extensionDetected ? <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-4"><p className="font-semibold text-blue-950">PosterPro found the extension{detectedExtensionVersion ? ` · v${detectedExtensionVersion}` : ''}.</p><p className="mt-1 text-sm text-blue-900">Connect this browser once. PosterPro will confirm the connection automatically.</p><Button data-posterpro-extension-authorize="true" data-setup-spotlight="browser-primary-action" className="mt-3" disabled={busy} onClick={connectThisBrowser}>Connect this browser</Button></div> : <div className="mt-4"><Button data-setup-spotlight="browser-primary-action" disabled={busy} onClick={() => setInstallGuideOpen(true)}>Install &amp; connect browser extension</Button><p className="mt-2 text-sm text-slate-600">After installation, PosterPro will detect the extension automatically.</p></div>}{installGuideOpen ? <div role="dialog" aria-modal="true" aria-labelledby="browser-install-title" className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/70 p-4"><section className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-2xl border border-slate-200 bg-white p-6 text-slate-950 shadow-2xl"><h2 id="browser-install-title" className="text-xl font-semibold">Install PosterPro in Chrome or Edge</h2><p className="mt-2 text-sm leading-6 text-slate-700">Browsers require a short manual step for this downloaded extension. You do not need to enter a code.</p><ol className="mt-4 list-decimal space-y-3 pl-5 text-sm leading-6 text-slate-800"><li><a className="font-semibold text-blue-800 underline" href="/api/browser-extension/download" download>Download the PosterPro extension</a>, then extract the ZIP file.</li><li>Open the Extensions page for Chrome or Edge in this browser.</li><li>Turn on <b>Developer mode</b>, choose <b>Load unpacked</b>, then select the extracted <b>posterpro-extension</b> folder.</li><li>Return to this page. PosterPro will find the extension and offer one button to connect it.</li></ol><div className="mt-5 flex flex-wrap gap-2"><a className="inline-flex min-h-11 items-center rounded-xl border border-slate-300 bg-white px-4 font-semibold text-slate-800" href="chrome://extensions/">Open Chrome Extensions</a><a className="inline-flex min-h-11 items-center rounded-xl border border-slate-300 bg-white px-4 font-semibold text-slate-800" href="edge://extensions/">Open Edge Extensions</a><Button variant="outline" onClick={() => window.location.reload()}>I installed it — detect extension</Button><Button variant="ghost" onClick={() => setInstallGuideOpen(false)}>Close</Button></div></section></div> : null}</div> : null}
            {currentTask.id === 'google_photos' ? (['CONNECTED', 'COMPLETE'].includes(status) ? <div role="status" className="mt-5 rounded-xl border border-green-300 bg-green-50 p-4"><p className="font-semibold text-green-950">Google Photos connected ✓</p><p className="mt-1 text-sm text-green-900">PosterPro can use this connection for photo intake.</p><Button className="mt-3" disabled={busy} onClick={next}>Continue</Button></div> : <div className="mt-3 flex flex-wrap gap-2"><Button data-setup-spotlight="google-connect" disabled={busy} onClick={connectGoogle}>{status === 'EXPIRED' ? 'Reconnect Google Photos' : 'Connect Google Photos'}</Button></div>) : null}
            {['facebook', 'mercari', 'poshmark', 'vinted', 'offerup'].includes(diagnosticMarketplace) ? <section className="mt-5 rounded-2xl border border-slate-200 bg-slate-50 p-5" aria-label={`${diagnosticMarketplace} real form test`}><h2 className="font-semibold text-slate-950">START {diagnosticMarketplace.toUpperCase()} REAL FORM TEST</h2><p className="mt-2 text-sm leading-6 text-slate-700">PosterPro opens the create form in your paired browser, uses a synthetic “DO NOT PUBLISH” sample, reports safe form structure and each field result, then stops without submitting.</p><MarketplaceDiagnosticReport marketplace={diagnosticMarketplace} diagnostic={diagnostic} history={diagnosticHistory} running={busy} onRun={runMarketplaceDiagnostic} />{diagnostic?.status === 'LOGIN_REQUIRED' ? <p className="mt-2 text-sm text-amber-900">Sign in normally to {diagnosticMarketplace} in this same browser, then choose Run again. PosterPro never asks for that password.</p> : null}</section> : null}
            {currentTask.guidance && currentTask.id !== 'browser_extension' && !(currentTask.id === 'google_photos' && ['CONNECTED', 'COMPLETE'].includes(status)) ? <div data-setup-spotlight="task-guidance" className="mt-5 rounded-2xl border border-blue-100 bg-blue-50 p-5"><h2 className="font-semibold text-slate-950">Do this one step at a time</h2><ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-slate-700">{(currentTask.guidance.steps || []).map((step, index) => <li key={`${currentTask.id}-guide-${index}`}>{step}</li>)}</ol>{currentTask.guidance.expect ? <p className="mt-4 rounded-xl bg-white p-3 text-sm leading-6 text-slate-700"><b>What you should see:</b> {currentTask.guidance.expect}</p> : null}{currentTask.guidance.troubleshooting ? <p className="mt-3 text-sm leading-6 text-slate-700"><b>If it does not work:</b> {currentTask.guidance.troubleshooting}</p> : null}<div className="mt-4 flex flex-wrap gap-2">{currentTask.guidance.open_url ? <a data-setup-spotlight="task-open-button" href={currentTask.guidance.open_url} target="_blank" rel="noreferrer" className="inline-flex min-h-10 items-center rounded-lg border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800">{currentTask.guidance.open_label || 'Open website'}</a> : currentTask.deep_link ? <Button data-setup-spotlight="task-open-button" href={currentTask.deep_link} variant="secondary">{currentTask.guidance.open_label || 'Open setup'}</Button> : null}<Button variant="outline" disabled={busy} onClick={() => run(() => verifyOnboardingTask(currentTask.id))}>Check this step again</Button></div></div> : null}
            {verification ? <div role="status" className="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950"><b>{String(verification.level || '').replaceAll('_', ' ')}</b>: {verification.message}</div> : null}
            {currentTask.id === 'pricing' ? <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">PosterPro’s current pricing tools can provide estimates, but a complete sold-comparable and high-value-variant protection workflow is not yet verified. Review prices carefully before publishing.</div> : null}
            {currentTask.id === 'test_workflow' ? <div className="mt-4 space-y-2">{taskList.map((task) => <div key={task.id} className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm"><span>{task.title}</span><span className="font-semibold text-slate-700">{String(task.status || 'NOT_STARTED').replaceAll('_', ' ')}</span></div>)}</div> : null}
          </section>
          <div className="flex flex-wrap justify-between gap-2"><div className="flex gap-2"><Button variant="outline" disabled={stepIndex <= 0 || busy} onClick={() => goToTask(taskList[Math.max(0, stepIndex - 1)].id)}>Back</Button><Button variant="ghost" disabled={busy} onClick={async () => { await run(() => skipOnboardingTask(currentTask.id)); await next(); }}>Skip this step</Button></div><div className="flex gap-2"><Button variant="outline" disabled={busy} onClick={() => setConfirmSkip(true)}>Not right now</Button>{currentTask.id !== 'choose_marketplaces' && currentTask.id !== 'ai' && (currentTask.id !== 'browser_extension' || browserOnline) ? <Button disabled={busy} onClick={next}>Next step</Button> : null}</div></div>
          <section className="rounded-2xl border border-violet-200 bg-violet-50/60 p-4" aria-label="Ask PosterPro for setup help"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-semibold text-slate-950">Need help? Ask PosterPro</p><p className="mt-1 text-sm text-slate-600">Ask about this step. PosterPro uses the connected AI provider when available; otherwise it gives the saved step-specific guidance.</p></div><Button type="button" variant="outline" onClick={() => { setHelpOpen((open) => !open); setHelpAnswer(null); }}> {helpOpen ? 'Close help' : 'Ask PosterPro'} </Button></div>{helpOpen ? <form className="mt-4 space-y-3" onSubmit={askForHelp}><label className="block text-sm font-medium text-slate-800">What is confusing about “{currentTask.title}”?<textarea value={helpQuestion} onChange={(event) => setHelpQuestion(event.target.value)} maxLength={1000} rows={3} className="mt-1 block w-full rounded-xl border border-slate-300 bg-white p-3 text-sm" placeholder="For example: I do not see the button you mentioned." /></label><Button type="submit" disabled={helpBusy || !helpQuestion.trim()}>{helpBusy ? 'Getting help…' : 'Send question'}</Button>{helpAnswer ? <div role="status" className="rounded-xl border border-violet-200 bg-white p-4 text-sm leading-6 text-slate-800"><p>{helpAnswer.answer}</p><p className="mt-2 text-xs text-slate-500">{helpAnswer.ai_assisted ? 'AI-assisted using this setup step only.' : 'Step-specific saved guidance.'} Chat is not saved. Do not include passwords, API keys, cookies, or tokens.</p></div> : null}</form> : null}</section>
        </div>
      ) : null}
      {snapshot?.completed ? <section className="rounded-2xl border border-green-200 bg-green-50 p-6"><h2 className="text-xl font-semibold text-green-950">PosterPro is ready at the verified level shown.</h2><p className="mt-2 text-sm text-green-900">No live marketplace listing was published during setup. Review the connection status above before crossposting.</p><div className="mt-4 flex flex-wrap gap-2"><Button href="/inventory">Add your first item</Button><Button href="/listings" variant="outline">Open listings</Button><Button href="/app" variant="outline">Dashboard</Button></div></section> : null}
      {tourOpen && currentTask ? <GuidedSpotlight steps={tourSteps} onClose={closeTour} /> : null}
      <section id="plans" className="mx-auto mt-8 max-w-4xl rounded-2xl border border-violet-200 bg-violet-50/60 p-5"><p className="text-xs font-bold uppercase tracking-wider text-violet-800">Plan preview · coming soon</p><h2 className="mt-2 text-lg font-semibold text-slate-950">A simpler AI option is planned.</h2><p className="mt-2 text-sm leading-6 text-slate-700">PosterPro is preparing Free, Basic, and Premium options. Plan prices, billing, included AI allowances, and activation are not available on this deployment. No upgrade or payment can be completed here.</p></section>
    </AppShell>
  );
}
