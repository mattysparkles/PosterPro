import { useEffect, useState } from 'react';
import Button from '../ui/button';
import { fetchOnboardingState, restartOnboarding, skipOnboardingForNow, startOnboarding } from '../../lib/api';

export default function GuidedSetupCard({ className = '' }) {
  const [snapshot, setSnapshot] = useState(null);
  const [confirmSkip, setConfirmSkip] = useState(false);
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    let live = true;
    fetchOnboardingState().then((value) => { if (live) setSnapshot(value); }).catch(() => {});
    return () => { live = false; };
  }, []);

  const complete = Boolean(snapshot?.completed);
  const dismissed = Boolean(snapshot?.welcome_dismissed);
  const showWelcome = Boolean(snapshot && !snapshot.started && !dismissed && !complete);
  const completed = Number(snapshot?.progress?.completed || 0);
  const required = Number(snapshot?.progress?.required || 0);
  return (
    <>
    <section className={`rounded-2xl border border-blue-200 bg-gradient-to-r from-blue-50 to-white p-4 shadow-sm sm:p-5 ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-blue-700">Guided setup</p>
          <h2 className="mt-1 text-lg font-semibold text-slate-900">
            {complete ? 'PosterPro setup complete' : dismissed || snapshot?.started ? 'Continue setting up PosterPro' : 'Finish setting up PosterPro'}
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            {complete ? 'Review your verified connections or run setup again.' : snapshot ? `${completed} of ${required} required steps verified. We’ll guide you one step at a time.` : 'Check your connections and follow the next setup step.'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2"><Button href="/onboarding">{complete ? 'Review connections' : snapshot?.started || dismissed ? 'Continue setup' : 'Start setup'}</Button>{complete ? <Button variant="outline" onClick={async () => { await restartOnboarding(); window.location.assign('/onboarding'); }}>Restart guided setup</Button> : null}</div>
      </div>
    </section>
    {showWelcome ? <div role="dialog" aria-modal="true" aria-labelledby="setup-welcome-title" className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/55 p-4"><div className="w-full max-w-xl rounded-3xl bg-white p-6 shadow-2xl sm:p-8"><p className="text-xs font-bold uppercase tracking-wider text-blue-700">Welcome</p><h2 id="setup-welcome-title" className="mt-2 text-2xl font-semibold text-slate-950">Welcome to PosterPro.</h2><p className="mt-3 text-sm leading-6 text-slate-600">I’ll help you get everything ready, one small step at a time. PosterPro checks each step, and you can skip anything you don’t want to use and come back later.</p><div className="mt-6 flex flex-wrap gap-2"><Button disabled={starting} onClick={async () => { setStarting(true); try { await startOnboarding(); window.location.assign('/onboarding'); } finally { setStarting(false); } }}>Start setup</Button><Button variant="outline" disabled={starting} onClick={() => setConfirmSkip(true)}>Not right now</Button></div></div></div> : null}
    {confirmSkip ? <div role="dialog" aria-modal="true" className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/60 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl"><h2 className="text-xl font-semibold">Are you sure?</h2><p className="mt-2 text-sm leading-6 text-slate-600">PosterPro works best after connecting your selling accounts and AI tools. You can restart setup anytime from Dashboard or Settings.</p><div className="mt-5 flex flex-wrap gap-2"><Button onClick={async () => { setConfirmSkip(false); await startOnboarding(); window.location.assign('/onboarding'); }}>Continue setup</Button><Button variant="outline" onClick={async () => { setConfirmSkip(false); const next = await skipOnboardingForNow(); setSnapshot(next); }}>Skip for now</Button></div></div></div> : null}
    </>
  );
}
