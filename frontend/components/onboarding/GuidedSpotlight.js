import { useEffect, useState } from 'react';
import Button from '../ui/button';

function clamp(value, min, max) { return Math.max(min, Math.min(value, max)); }

export default function GuidedSpotlight({ steps = [], onClose }) {
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState(null);
  const current = steps[index];

  useEffect(() => {
    if (!current || typeof window === 'undefined') return undefined;
    let element = null;
    let retryTimer = null;
    const update = () => {
      element = document.querySelector(current.selector);
      if (!element) { setRect(null); return; }
      const bounds = element.getBoundingClientRect();
      setRect({ top: bounds.top - 7, left: bounds.left - 7, width: bounds.width + 14, height: bounds.height + 14 });
    };
    element = document.querySelector(current.selector);
    if (!element) {
      retryTimer = window.setTimeout(update, 180);
      return () => { if (retryTimer) window.clearTimeout(retryTimer); };
    }
    element.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    const previousFocus = document.activeElement;
    const hadTabIndex = element.hasAttribute('tabindex');
    if (!hadTabIndex && !element.matches('button,a,input,textarea,select,[tabindex]')) element.setAttribute('tabindex', '-1');
    element.focus({ preventScroll: true });
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    const escape = (event) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', escape);
    return () => {
      if (retryTimer) window.clearTimeout(retryTimer);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      document.removeEventListener('keydown', escape);
      if (!hadTabIndex) element.removeAttribute('tabindex');
      if (previousFocus?.isConnected) previousFocus.focus?.({ preventScroll: true });
    };
  }, [current, onClose]);

  if (!current) return null;
  const target = rect || { top: window.innerHeight * 0.35, left: 24, width: 1, height: 1 };
  const bubbleWidth = Math.min(400, window.innerWidth - 32);
  const bubbleLeft = clamp(target.left, 16, Math.max(16, window.innerWidth - bubbleWidth - 16));
  const bubbleTop = target.top + target.height + 16 + 190 > window.innerHeight
    ? clamp(target.top - 210, 16, window.innerHeight - 210)
    : target.top + target.height + 16;
  const top = { position: 'fixed', inset: 0, height: Math.max(0, target.top), background: 'rgba(9,16,28,.68)', zIndex: 119, pointerEvents: 'auto' };
  const left = { position: 'fixed', top: target.top, left: 0, width: Math.max(0, target.left), height: target.height, background: 'rgba(9,16,28,.68)', zIndex: 119, pointerEvents: 'auto' };
  const right = { position: 'fixed', top: target.top, left: target.left + target.width, right: 0, height: target.height, background: 'rgba(9,16,28,.68)', zIndex: 119, pointerEvents: 'auto' };
  const bottom = { position: 'fixed', top: target.top + target.height, left: 0, right: 0, bottom: 0, background: 'rgba(9,16,28,.68)', zIndex: 119, pointerEvents: 'auto' };
  return (
    <>
      <div aria-hidden="true" style={top} />
      <div aria-hidden="true" style={left} />
      <div aria-hidden="true" style={right} />
      <div aria-hidden="true" style={bottom} />
      <div aria-hidden="true" style={{ position: 'fixed', top: target.top, left: target.left, width: target.width, height: target.height, border: '3px solid #fbbf24', borderRadius: 14, boxShadow: '0 0 0 3px rgba(255,255,255,.95)', zIndex: 120, pointerEvents: 'none' }} />
      <section role="dialog" aria-modal="true" aria-labelledby="setup-spotlight-title" aria-describedby="setup-spotlight-body" className="fixed z-[121] w-[min(400px,calc(100vw-32px))] rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl" style={{ top: bubbleTop, left: bubbleLeft }}>
        <p className="text-xs font-bold uppercase tracking-wide text-blue-700">Guided tour · {index + 1} of {steps.length}</p>
        <h2 id="setup-spotlight-title" className="mt-2 text-lg font-semibold text-slate-950">{current.title}</h2>
        <p id="setup-spotlight-body" className="mt-2 text-sm leading-6 text-slate-700">{current.body}</p>
        {!rect ? <p className="mt-2 text-xs text-amber-800">This control is not visible on this screen. Close the tour and use the setup button below.</p> : null}
        <div className="mt-4 flex flex-wrap justify-between gap-2">
          <Button type="button" variant="tertiary" size="sm" onClick={onClose}>Skip tour</Button>
          <div className="flex gap-2">
            <Button type="button" variant="secondary" size="sm" disabled={index === 0} onClick={() => setIndex((value) => Math.max(0, value - 1))}>Back</Button>
            <Button type="button" size="sm" onClick={() => index + 1 >= steps.length ? onClose() : setIndex((value) => value + 1)}>{index + 1 >= steps.length ? 'Done' : 'Next'}</Button>
          </div>
        </div>
      </section>
    </>
  );
}
