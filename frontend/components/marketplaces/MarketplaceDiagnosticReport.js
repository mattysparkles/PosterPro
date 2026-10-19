import Button from '../ui/button';
import toast from 'react-hot-toast';

export const MARKETPLACE_DIAGNOSTIC_URLS = {
  facebook: 'https://www.facebook.com/marketplace/create/item',
  mercari: 'https://www.mercari.com/sell/',
  poshmark: 'https://poshmark.com/create-listing',
  vinted: 'https://www.vinted.com/items/new',
  offerup: 'https://offerup.com/item/new',
};

function safeSummary(diagnostic) {
  if (!diagnostic) return '';
  const result = diagnostic.result || {};
  const safeValue = (value) => {
    if (typeof value !== 'string' && typeof value !== 'number') return null;
    const text = String(value).slice(0, 160);
    return /@|password|cookie|token|secret|bearer|https?:\/\/|[?&][a-z0-9_-]+=|\b\d{3}[- .]?\d{3}[- .]?\d{4}\b/i.test(text) ? null : text;
  };
  const safeSelector = (selector) => {
    if (!selector || typeof selector !== 'object') return undefined;
    return {
      field: selector.field,
      page_path: typeof selector.page_path === 'string' ? selector.page_path.split(/[?#]/, 1)[0].slice(0, 300) : null,
      selectors_tried: (selector.selectors_tried || []).filter((item) => typeof item === 'string').slice(0, 8),
      expected_label_hints: (selector.expected_label_hints || []).map(safeValue).filter(Boolean).slice(0, 8),
      controls: (selector.controls || []).slice(0, 8).map((control) => ({
        tag: control.tag,
        role: control.role,
        aria_label: safeValue(control.aria_label),
        name: safeValue(control.name),
        placeholder: safeValue(control.placeholder),
        nearby_label: safeValue(control.nearby_label),
        option_labels: (control.option_labels || []).map(safeValue).filter(Boolean).slice(0, 12),
      })),
    };
  };
  const fields = (result.field_results || []).map((field) => ({
    field: field.field,
    required: Boolean(field.required),
    detected: Boolean(field.detected),
    attempted: Boolean(field.attempted),
    filled: Boolean(field.filled),
    verified_value: safeValue(field.verified_value),
    error_code: field.error_code || null,
    selector_diagnostic: field.filled ? undefined : safeSelector(field.selector_diagnostic),
    interaction_diagnostic: field.interaction_diagnostic || undefined,
  }));
  return JSON.stringify({
    diagnostic_id: diagnostic.id || null,
    marketplace: diagnostic.marketplace,
    status: diagnostic.status,
    requested_at: diagnostic.requested_at || diagnostic.created_at || null,
    completed_at: diagnostic.completed_at || null,
    extension: diagnostic.device ? {
      device_name: safeValue(diagnostic.device.name),
      browser: safeValue(diagnostic.device.browser),
      version: diagnostic.device.extension_version,
      minimum_compatible_version: diagnostic.minimum_version,
      background_version: result.background_version || null,
      content_script_version: result.content_script_version || null,
      content_script_build_id: safeValue(result.content_script_build_id),
      content_script_injected_at: result.content_script_injected_at || null,
      page_loaded_at: result.page_loaded_at || null,
    } : null,
    login_state: result.login_state || 'UNKNOWN',
    form_detected: Boolean(result.form_detected),
    form_readiness: result.form_readiness || null,
    page_evidence: result.page_evidence || null,
    capability_ready: Boolean(result.capability_ready),
    submission_performed: false,
    error_code: diagnostic.error_code || result.error_code || null,
    error_detail: safeValue(diagnostic.error_detail || result.error_detail),
    execution_diagnostic: result.execution_diagnostic || null,
    field_results: fields,
  }, null, 2);
}

export default function MarketplaceDiagnosticReport({ marketplace, diagnostic, history = [], onRun, running = false }) {
  const title = marketplace === 'facebook' ? 'Facebook Marketplace' : `${marketplace?.[0]?.toUpperCase() || ''}${marketplace?.slice(1) || ''}`;
  const result = diagnostic?.result || {};
  const summaryText = safeSummary(diagnostic);
  const copy = async () => {
    const text = safeSummary(diagnostic);
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      toast.success('Safe diagnostic summary copied.');
    } catch {
      toast.error('Clipboard access was blocked. Select the safe summary from the test details and copy it manually.');
    }
  };
  const failedFields = (entry) => (entry?.result?.field_results || []).filter((field) => field.required && !field.filled).map((field) => field.field).join(', ') || 'none';
  const activeStatuses = ['QUEUED', 'CLAIMED', 'NAVIGATING', 'FORM_DETECTED', 'TESTING_FIELDS', 'FORM_FILLING', 'SUBMITTING'];
  const isRunning = running || activeStatuses.includes(String(diagnostic?.status || '').toUpperCase());
  const passed = Boolean(result.capability_ready) && String(diagnostic?.status || '').toUpperCase() === 'COMPLETED';
  const terminal = ['COMPLETED', 'FAILED', 'LOGIN_REQUIRED', 'BLOCKED_EXTERNAL', 'CANCELLED'].includes(String(diagnostic?.status || '').toUpperCase());
  const failed = Boolean(diagnostic && terminal && !passed);
  const diagnosticLabel = result.capability_ready
    ? 'CAPABILITY READY'
    : diagnostic?.status === 'COMPLETED' && result.form_detected
      ? 'FORM OPENED · FIELD CHECK INCOMPLETE'
      : diagnostic
        ? 'NEEDS ATTENTION'
        : 'NOT TESTED';
  const externalBlockMessage = diagnostic?.status === 'BLOCKED_EXTERNAL' && !diagnostic?.error_detail && !result?.error_detail
    ? `PosterPro could not see a live ${title} page or create form in the paired Chrome profile. Open ${title} there, sign in, leave the create form open, and run the diagnostic again. No listing was submitted.`
    : null;

  return (
    <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4" aria-label={`${title} real form diagnostic`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-slate-950">Latest real-form test</h3>
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${result.capability_ready ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-900'}`}>
          {diagnosticLabel}
        </span>
      </div>
      {isRunning ? <div role="status" className="mt-4 flex items-center gap-4 rounded-2xl border border-blue-200 bg-blue-50 p-5 text-blue-950"><div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-4 border-blue-300 text-2xl font-bold text-blue-700" aria-hidden="true">…</div><div><p className="text-base font-bold">Test will begin momentarily</p><p className="mt-1 text-sm leading-6">PosterPro is checking the live form in the background. This can take up to a couple of minutes. Please do not click Run again.</p><p className="mt-1 text-xs font-semibold uppercase tracking-wide text-blue-700">{String(diagnostic?.status || 'QUEUED').replaceAll('_', ' ')}</p></div></div> : null}
      {passed ? <div role="status" className="mt-4 flex items-center gap-4 rounded-2xl border-2 border-emerald-300 bg-emerald-50 p-5 text-emerald-950"><div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-3xl font-bold text-white" aria-label="Connected">✓</div><div><p className="text-lg font-bold">Connected and verified</p><p className="mt-1 text-sm">PosterPro confirmed the required marketplace form fields. No listing was submitted.</p></div></div> : null}
      {failed ? <div role="alert" className="mt-4 flex items-center gap-4 rounded-2xl border-2 border-rose-300 bg-rose-50 p-5 text-rose-950"><div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-rose-600 text-3xl font-bold text-white" aria-label="Failed">×</div><div><p className="text-lg font-bold">Connection test failed</p><p className="mt-1 text-sm">{diagnostic.error_detail || result.error_detail || externalBlockMessage || result.error_code || 'PosterPro could not verify the marketplace form.'}</p><p className="mt-1 text-xs font-semibold uppercase tracking-wide text-rose-700">No listing was submitted.</p></div></div> : null}
      {diagnostic ? <>
      <div className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
          <p><b>Login state:</b> {result.login_state || diagnostic.status || 'UNKNOWN'}</p>
          <p><b>Form detected:</b> {result.form_detected ? 'YES' : 'NO'}</p>
        <p><b>Capability ready:</b> {result.capability_ready ? 'YES' : result.form_detected ? 'NO — form opened, required verification did not pass' : 'NO'}</p>
        </div>
        <p className="mt-1 text-xs text-slate-600">Run {diagnostic.id} · {diagnostic.completed_at || diagnostic.requested_at || diagnostic.created_at || 'time unavailable'} · {diagnostic.device?.name || (diagnostic.device_id ? `Device ${diagnostic.device_id}` : 'device not reported')} · {diagnostic.device?.browser || 'browser unknown'} · v{diagnostic.device?.extension_version || 'unknown'} (minimum {diagnostic.minimum_version || 'unknown'})</p>
        {result.background_version || result.content_script_version ? <p className="mt-1 text-xs text-slate-700"><b>Runtime code:</b> background {result.background_version || 'unknown'} · page content script {result.content_script_version || 'unknown'} · build {result.content_script_build_id || 'unknown'} · injected {result.content_script_injected_at || 'unknown'}</p> : null}
        {result.form_readiness ? <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs"><p className="font-semibold text-slate-800">Sell-form readiness: {result.form_readiness.form_ready_wait_started ? `waited ${result.form_readiness.form_ready_wait_ms ?? 'unknown'} ms` : 'not started'}</p><p className="mt-1 text-slate-700">Anchors: {Object.entries(result.form_readiness.form_ready_anchors || {}).filter(([key]) => key !== 'core_anchor_count').map(([key, value]) => `${key} ${value ? 'yes' : 'no'}`).join(' · ') || 'none'}{Number.isInteger(result.form_readiness.form_ready_anchors?.core_anchor_count) ? ` · core ${result.form_readiness.form_ready_anchors.core_anchor_count}/3` : ''}</p></div> : null}
        {result.page_evidence ? <details className="mt-3"><summary className="cursor-pointer text-sm font-semibold text-slate-800">Safe page/readiness evidence</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs">{JSON.stringify(result.page_evidence, null, 2)}</pre></details> : null}
        {(result.field_results || []).length ? <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[760px] border-collapse text-left text-xs"><thead><tr className="border-b text-slate-600">{['Field', 'Required', 'Detected', 'Attempted', 'Filled', 'Safe verified value', 'Error code'].map((column) => <th key={column} className="px-2 py-2 font-semibold">{column}</th>)}</tr></thead><tbody>{result.field_results.map((field) => <tr key={field.field} className="border-b border-slate-100"><td className="px-2 py-2 font-semibold">{field.field}</td><td className="px-2 py-2">{field.required ? 'YES' : 'NO'}</td><td className="px-2 py-2">{field.detected ? 'YES' : 'NO'}</td><td className="px-2 py-2">{field.attempted ? 'YES' : 'NO'}</td><td className="px-2 py-2">{field.filled ? 'YES' : 'NO'}</td><td className="max-w-64 truncate px-2 py-2" title={field.verified_value || ''}>{field.verified_value || '—'}</td><td className="px-2 py-2 text-rose-700">{field.error_code || '—'}</td></tr>)}</tbody></table></div> : null}
        {(result.field_results || []).some((field) => field.selector_diagnostic) ? <details className="mt-3"><summary className="cursor-pointer text-sm font-semibold text-slate-800">Safe selector diagnostics (failed fields only)</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs">{JSON.stringify((result.field_results || []).filter((field) => field.selector_diagnostic).map((field) => field.selector_diagnostic), null, 2)}</pre></details> : null}
        {(result.field_results || []).some((field) => field.interaction_diagnostic) ? <details className="mt-3"><summary className="cursor-pointer text-sm font-semibold text-slate-800">Marketplace interaction evidence</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs">{JSON.stringify((result.field_results || []).filter((field) => field.interaction_diagnostic).map((field) => ({ field: field.field, ...field.interaction_diagnostic })), null, 2)}</pre></details> : null}
        {result.execution_diagnostic ? <details className="mt-3" open={failed}><summary className="cursor-pointer text-sm font-semibold text-rose-800">Extension execution trace</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-rose-50 p-3 text-xs">{JSON.stringify(result.execution_diagnostic, null, 2)}</pre></details> : null}
      </> : <p className="mt-2 text-sm text-slate-600">No real form test has been run for {title} yet.</p>}
      {diagnostic && diagnostic.status === 'COMPLETED' && result.form_detected && !result.capability_ready ? <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">Facebook opened the Marketplace form and attempted the field fill. PosterPro still needs explicit confirmation for the required field(s) listed below; this safe test never submits a listing.</p> : null}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button type="button" disabled={isRunning} onClick={onRun}>{isRunning ? 'Test running…' : diagnostic ? 'Run again' : `START ${marketplace?.toUpperCase()} REAL FORM TEST`}</Button>
        <a className="inline-flex min-h-10 items-center rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-800" href={MARKETPLACE_DIAGNOSTIC_URLS[marketplace] || '#'} target="_blank" rel="noreferrer">Open marketplace</a>
        <Button type="button" variant="outline" disabled={!diagnostic} onClick={copy}>Copy diagnostic summary</Button>
      </div>
      {result.login_state === 'LOGIN_REQUIRED' ? <p className="mt-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-950">This marketplace is signed out in the paired browser. Open the marketplace, sign in normally in that same browser, then choose Run again. Do not enter the marketplace password in PosterPro.</p> : null}
      <p className="mt-2 text-xs text-slate-500">The copied summary contains only diagnostic field/status metadata. It excludes page contents, URL query strings, credentials, cookies, and browser storage.</p>
      {diagnostic ? <details className="mt-2"><summary className="cursor-pointer text-xs font-semibold text-slate-700">Show copyable safe summary</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs">{summaryText}</pre></details> : null}
      <p className="mt-3 text-xs leading-5 text-slate-500">The check starts in the background and can take up to a couple of minutes. Start it once and wait for the large result indicator; repeated clicks are ignored while it is running.</p>
      {history.length ? <div className="mt-5"><h4 className="text-sm font-semibold text-slate-900">Recent test history</h4><div className="mt-2 space-y-2">{history.map((entry) => <div key={entry.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 p-2 text-xs"><span>{entry.requested_at || entry.created_at || 'Time unavailable'} · {entry.device?.name || (entry.device_id ? `Device ${entry.device_id}` : 'device unknown')} ({entry.device?.browser || 'browser unknown'} v{entry.device?.extension_version || 'unknown'})</span><span className={entry.result?.capability_ready ? 'font-semibold text-emerald-700' : 'font-semibold text-amber-800'}>{entry.result?.capability_ready ? 'PASSED' : String(entry.status || 'UNKNOWN').replaceAll('_', ' ')} · failed required: {failedFields(entry)}</span></div>)}</div></div> : null}
    </section>
  );
}
