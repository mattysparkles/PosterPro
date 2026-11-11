import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/router';
import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import PageHeader from '../../components/ui/page-header';
import { censusApi } from '../../lib/api';
import { dispatchBrowserLabel } from '../../lib/inventoryPrintTransport';

const SESSION_STORAGE_KEY = 'posterpro.shelfCensus.sessionKey';
const PENDING_OPERATION_KEY = 'posterpro.shelfCensus.pendingOperation';
const initialLocation = { unit_id: '', section_code: '', shelf_level: '', position_code: '', location_type: 'shelf', accessibility: 'accessible' };

export default function ShelfCensusPage() {
  const inputRef = useRef(null);
  const operationIdRef = useRef(null);
  const operationIntentRef = useRef(null);
  const reprintOperationIdRef = useRef(null);
  const reprintOperationByItemRef = useRef({});
  const router = useRouter();
  const [sessionKey, setSessionKey] = useState('');
  const [location, setLocation] = useState(initialLocation);
  const [locations, setLocations] = useState([]);
  const [query, setQuery] = useState('');
  const [candidates, setCandidates] = useState([]);
  const [selected, setSelected] = useState(null);
  const [session, setSession] = useState(null);
  const [template, setTemplate] = useState(null);
  const [printSettings, setPrintSettings] = useState(null);
  const [printJobs, setPrintJobs] = useState([]);
  const [pairingCode, setPairingCode] = useState('');
  const [lastItem, setLastItem] = useState(null);
  const [labelPreview, setLabelPreview] = useState('');
  const [queuedNeedsBrowserDispatch, setQueuedNeedsBrowserDispatch] = useState(false);
  const [scannedItem, setScannedItem] = useState(null);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const focusInput = useCallback(() => inputRef.current?.focus(), []);
  const refresh = useCallback(async (key) => {
    const [saved, progress, labelTemplate, printing, recentJobs] = await Promise.all([censusApi('/locations'), censusApi(`/sessions/${encodeURIComponent(key)}`), censusApi('/label-template'), censusApi('/print-settings'), censusApi('/print-jobs?limit=20')]);
    setLocations(saved.items || []); setSession(progress);
    setTemplate(labelTemplate.template);
    setPrintSettings(printing);
    setPrintJobs(recentJobs.items || []);
    if (progress.active_location_key) {
      const found = (saved.items || []).find((item) => item.location_key === progress.active_location_key);
      if (found) setLocation({ ...initialLocation, ...found, position_code: found.position_code || '' });
    }
  }, []);

  useEffect(() => {
    let key = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!key) { key = crypto.randomUUID(); localStorage.setItem(SESSION_STORAGE_KEY, key); }
    setSessionKey(key); refresh(key).catch((err) => setError(err.message));
    focusInput();
  }, [focusInput, refresh]);

  useEffect(() => {
    if (!router.isReady || typeof router.query.item !== 'string') return;
    const token = router.query.item;
    setQuery(token);
    censusApi(`/search?query=${encodeURIComponent(token)}`).then((result) => { setScannedItem(result.scanned_item || null); focusInput(); }).catch((err) => setError(err.message));
  }, [focusInput, router.isReady, router.query.item]);

  const setField = (name, value) => setLocation((current) => ({ ...current, [name]: value }));
  const saveLocation = async (full = false) => {
    setError('');
    try {
      const saved = await censusApi('/locations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...location, is_full: full }) });
      setLocation({ ...location, ...saved, position_code: saved.position_code || '' });
      await censusApi(`/sessions/${encodeURIComponent(sessionKey)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active_location: full ? null : location }) });
      await refresh(sessionKey);
      if (full) setLocation(initialLocation);
      focusInput();
    } catch (err) { setError(err.message); }
  };

  const search = async (event) => {
    event?.preventDefault();
    const value = query.trim();
    if (value.length < 2) return;
    setLoading(true); setError(''); setSelected(null);
    try {
      const result = await censusApi(`/search?query=${encodeURIComponent(value)}`);
      setCandidates(result.items || []); setScannedItem(result.scanned_item || null);
      if (result.scanned_location) {
        const nextLocation = { ...initialLocation, ...result.scanned_location, position_code: result.scanned_location.position_code || '' };
        setLocation(nextLocation);
        await censusApi(`/sessions/${encodeURIComponent(sessionKey)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active_location: nextLocation }) });
        setQuery('');
      }
    }
    catch (err) { setError(err.message); }
    finally { setLoading(false); focusInput(); }
  };

  const confirm = async (event) => {
    event?.preventDefault();
    if (!selected && !query.trim()) return;
    setLoading(true); setError('');
    try {
      const activeKey = location.unit_id && location.section_code && location.shelf_level
        ? [location.unit_id, location.section_code.toUpperCase(), location.shelf_level.toUpperCase(), location.position_code?.trim().toUpperCase()].filter(Boolean).join('/') : '';
      if (!activeKey) throw new Error('Select and save the physical location first.');
      const savedLocation = await censusApi('/locations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(location) });
      const intent = JSON.stringify({ sessionKey, location: savedLocation.location_key, listingId: selected?.listing_id || null, title: selected ? null : query.trim(), allowDuplicate });
      if (!operationIdRef.current || operationIntentRef.current !== intent) {
        let pending = null;
        try { pending = JSON.parse(sessionStorage.getItem(PENDING_OPERATION_KEY) || 'null'); } catch { pending = null; }
        if (pending?.intent === intent && pending?.operation_id) operationIdRef.current = pending.operation_id;
        else operationIdRef.current = crypto.randomUUID();
        operationIntentRef.current = intent;
        sessionStorage.setItem(PENDING_OPERATION_KEY, JSON.stringify({ intent, operation_id: operationIdRef.current }));
      }
      const result = await censusApi('/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation_id: operationIdRef.current, session_key: sessionKey, location_key: savedLocation.location_key, listing_id: selected?.listing_id, provisional_title: selected ? undefined : query.trim(), description: selected ? undefined : query.trim(), allow_duplicate: allowDuplicate }) });
      operationIdRef.current = null;
      operationIntentRef.current = null;
      sessionStorage.removeItem(PENDING_OPERATION_KEY);
      setLastItem(result); setQuery(''); setCandidates([]); setSelected(null); setScannedItem(null); setAllowDuplicate(false);
      setQueuedNeedsBrowserDispatch(Boolean(result.replayed && result.print_mode === 'browser' && result.print_job_id));
      await refresh(sessionKey);
      if (result.print_job_id) {
        try {
          const layout = printSettings?.layout || 'qr';
          const rendered = await censusApi(`/items/${encodeURIComponent(result.item_uuid)}/label?layout=${layout}`);
          setLabelPreview(rendered.image_data_url);
          if (result.print_mode === 'browser' && !result.replayed) {
            dispatchBrowserLabel(rendered.image_data_url, { onReturn: focusInput, scalePercent: rendered.print_scale_percent });
            await censusApi(`/print-jobs/${result.print_job_id}/browser-dispatch`, { method: 'POST' });
            setQueuedNeedsBrowserDispatch(false);
          }
        } catch (printError) {
          if (result.print_mode === 'browser') setQueuedNeedsBrowserDispatch(true);
          setError(`Item saved; label preview/dispatch needs attention: ${printError.message}`);
        }
      }
      // Focus is also restored if the browser blocks or the operator closes printing.
      focusInput();
    } catch (err) { setError(err.message); }
    finally { setLoading(false); }
  };

  const reprint = async () => {
    if (!lastItem?.item_uuid) return;
    try {
      if (!reprintOperationIdRef.current) reprintOperationIdRef.current = crypto.randomUUID();
      const job = await censusApi(`/items/${encodeURIComponent(lastItem.item_uuid)}/reprint`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation_id: reprintOperationIdRef.current }) });
      reprintOperationIdRef.current = null;
      const layout = printSettings?.layout || 'qr';
      const label = await censusApi(`/items/${encodeURIComponent(lastItem.item_uuid)}/label?layout=${layout}`);
      setLabelPreview(label.image_data_url);
      if ((printSettings?.mode || 'browser') === 'browser' && !job.replayed) {
        dispatchBrowserLabel(label.image_data_url, { onReturn: focusInput, scalePercent: label.print_scale_percent });
        await censusApi(`/print-jobs/${job.job_id}/browser-dispatch`, { method: 'POST' });
      }
      await refresh(sessionKey);
    }
    catch (err) { setError(err.message); }
  };

  const reprintItem = async (itemUuid) => {
    try {
      if (!reprintOperationByItemRef.current[itemUuid]) reprintOperationByItemRef.current[itemUuid] = crypto.randomUUID();
      const job = await censusApi(`/items/${encodeURIComponent(itemUuid)}/reprint`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation_id: reprintOperationByItemRef.current[itemUuid] }) });
      delete reprintOperationByItemRef.current[itemUuid];
      if ((printSettings?.mode || 'browser') === 'browser' && !job.replayed) {
        const label = await censusApi(`/items/${encodeURIComponent(itemUuid)}/label?layout=${printSettings?.layout || 'qr'}`);
        dispatchBrowserLabel(label.image_data_url, { onReturn: focusInput, scalePercent: label.print_scale_percent });
        await censusApi(`/print-jobs/${job.job_id}/browser-dispatch`, { method: 'POST' });
      }
      await refresh(sessionKey);
    } catch (err) { setError(err.message); }
  };

  const reconcilePrintJob = async (job, resolution) => {
    try { await censusApi(`/print-jobs/${job.database_id}/reconcile`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ resolution }) }); await refresh(sessionKey); }
    catch (err) { setError(err.message); }
  };

  const dispatchQueuedLabel = async () => {
    if (!lastItem?.item_uuid || !lastItem?.print_job_id) return;
    try {
      const layout = printSettings?.layout || 'qr';
      const label = await censusApi(`/items/${encodeURIComponent(lastItem.item_uuid)}/label?layout=${layout}`);
      dispatchBrowserLabel(label.image_data_url, { onReturn: focusInput, scalePercent: label.print_scale_percent });
      await censusApi(`/print-jobs/${lastItem.print_job_id}/browser-dispatch`, { method: 'POST' });
      setQueuedNeedsBrowserDispatch(false);
    } catch (err) { setError(err.message); }
  };

  const undoLast = async () => {
    if (!lastItem?.item_uuid) return;
    try { await censusApi(`/items/${encodeURIComponent(lastItem.item_uuid)}/undo`, { method: 'POST' }); setLastItem(null); await refresh(sessionKey); focusInput(); }
    catch (err) { setError(err.message); }
  };

  const moveScannedItem = async () => {
    if (!scannedItem?.item_uuid) return;
    const destinationLocation = [location.unit_id, location.section_code, location.shelf_level, location.position_code].filter(Boolean).join('/');
    try {
      const result = await censusApi(`/items/${encodeURIComponent(scannedItem.item_uuid)}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation_id: crypto.randomUUID(), destination_location_key: destinationLocation, allow_duplicate: allowDuplicate }) });
      setScannedItem({ ...scannedItem, location_key: result.location_key }); setAllowDuplicate(false); await refresh(sessionKey); focusInput();
    } catch (err) { setError(err.message); }
  };

  const saveTemplate = async () => {
    try { const result = await censusApi('/label-template', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(template) }); setTemplate(result.template); }
    catch (err) { setError(err.message); }
  };

  const savePrintSettings = async (next = printSettings) => {
    try {
      const result = await censusApi('/print-settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(next) });
      setPrintSettings((current) => ({ ...current, ...result })); await refresh(sessionKey);
    } catch (err) { setError(err.message); }
  };

  const createPairingCode = async () => {
    try { const result = await censusApi('/print-agents/pairing-codes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ display_name: 'Windows WT20 agent' }) }); setPairingCode(result.pairing_code); }
    catch (err) { setError(err.message); }
  };

  const setAgentEnabled = async (agent, enabled) => {
    try { await censusApi(`/print-agents/${agent.id}/enable`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled, confirm_hardware_tested: enabled }) }); await refresh(sessionKey); }
    catch (err) { setError(err.message); }
  };

  const revokeAgent = async (agent) => {
    try { await censusApi(`/print-agents/${agent.id}/revoke`, { method: 'POST' }); await refresh(sessionKey); }
    catch (err) { setError(err.message); }
  };

  useEffect(() => {
    if (!sessionKey || printSettings?.mode !== 'automatic_agent') return undefined;
    const timer = setInterval(() => refresh(sessionKey).catch(() => {}), 5000);
    return () => clearInterval(timer);
  }, [printSettings?.mode, refresh, sessionKey]);

  const printCalibration = async () => {
    try { const result = await censusApi('/label-template/test'); dispatchBrowserLabel(result.image_data_url, { onReturn: focusInput, scalePercent: result.print_scale_percent }); }
    catch (err) { setError(err.message); }
  };

  return <AppShell active="/inventory" title="Shelf Census">
    <main className="mx-auto max-w-6xl space-y-5 p-4 md:p-6">
      <PageHeader title="Shelf Census / Physical Inventory" description="Match boxed merchandise to its existing PosterPro record, save its location, and continue without resetting the shelf." />
      <section className="grid gap-3 rounded-2xl border p-4 md:grid-cols-4">
        <label className="text-sm font-semibold">Storage unit<input aria-label="Storage unit" className="mt-1 w-full rounded-lg border p-3 text-lg" placeholder="01175" value={location.unit_id} onChange={(e) => setField('unit_id', e.target.value)} /></label>
        <label className="text-sm font-semibold">Shelving section<input aria-label="Shelving section" className="mt-1 w-full rounded-lg border p-3 text-lg" placeholder="A" value={location.section_code} onChange={(e) => setField('section_code', e.target.value)} /></label>
        <label className="text-sm font-semibold">Shelf level / bulk area<input aria-label="Shelf level" className="mt-1 w-full rounded-lg border p-3 text-lg" placeholder="01" value={location.shelf_level} onChange={(e) => setField('shelf_level', e.target.value)} /></label>
        <label className="text-sm font-semibold">Position (optional)<input aria-label="Position" className="mt-1 w-full rounded-lg border p-3 text-lg" placeholder="02" value={location.position_code} onChange={(e) => setField('position_code', e.target.value)} /></label>
        <label className="text-sm font-semibold">Physical accessibility<select aria-label="Physical accessibility" className="mt-1 w-full rounded-lg border p-3 text-lg" value={location.accessibility} onChange={(e) => setField('accessibility', e.target.value)}><option value="accessible">Accessible / pickable</option><option value="staged_inaccessible">Staged / inaccessible</option><option value="missing_unresolved">Missing / unresolved</option><option value="shipped_removed">Shipped / removed</option></select></label>
        <div className="flex flex-wrap items-center gap-2 md:col-span-4"><Button onClick={() => saveLocation(false)}>Save / change location</Button><Button variant="outline" onClick={() => saveLocation(true)}>Mark full & advance</Button><span className="font-bold">Active: {location.unit_id ? [location.unit_id, location.section_code, location.shelf_level, location.position_code].filter(Boolean).join(' / ') : 'Choose a location'}</span><span className="text-sm text-slate-600">Change shelves explicitly; progress stays saved.</span></div>
        {locations.length ? <div className="text-sm md:col-span-4">Recent saved locations: {locations.slice(-8).map((item) => <button className="mr-2 underline" key={item.location_key} onClick={() => { setLocation({ ...initialLocation, ...item }); focusInput(); }}>{item.location_key}</button>)}</div> : null}
      </section>
      <section className="rounded-2xl border bg-white p-4 shadow-sm">
        <div className="mb-2 flex items-center justify-between"><h2 className="text-xl font-bold">Product identification</h2><Button variant="outline" onClick={focusInput}>Refocus input</Button></div>
        <form onSubmit={candidates.length ? (e) => { e.preventDefault(); if (selected) confirm(e); else setSelected(candidates[0]); } : search}>
          <input ref={inputRef} autoFocus aria-label="Dictate or scan product identification" className="w-full rounded-xl border-2 border-blue-600 p-5 text-2xl" placeholder="Dictate or scan a product, then press Enter to search" value={query} onChange={(e) => { setQuery(e.target.value); setCandidates([]); setSelected(null); setScannedItem(null); }} onKeyDown={(e) => { if (e.altKey && /^[1-9]$/.test(e.key) && candidates[Number(e.key) - 1]) { e.preventDefault(); setSelected(candidates[Number(e.key) - 1]); } }} />
        </form>
        <div className="mt-3 flex flex-wrap gap-2"><Button onClick={() => search()} disabled={loading || query.trim().length < 2}>Search catalog</Button><Button onClick={() => confirm()} disabled={loading || (!selected && !query.trim())}>{selected ? 'Confirm selected product' : 'Save unmatched as provisional'}</Button>{lastItem ? <>{queuedNeedsBrowserDispatch && lastItem.print_mode === 'browser' && lastItem.print_job_id ? <Button variant="outline" onClick={dispatchQueuedLabel}>Print queued label</Button> : null}<Button variant="outline" onClick={reprint}>Reprint Last Label</Button><Button variant="outline" onClick={undoLast}>Undo Last Action</Button></> : null}</div>
        {selected ? <><p className="mt-3 rounded-lg bg-green-50 p-3 font-semibold">Confirming: {selected.title} · {selected.sku || 'SKU not present; identity will be preserved'} · PosterPro listing #{selected.listing_id}</p><label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={allowDuplicate} onChange={(e) => setAllowDuplicate(e.target.checked)} /> I physically confirmed another separate unit at this location (required only to override duplicate protection)</label></> : null}
        {scannedItem ? <div role="status" className="mt-3 rounded-lg bg-blue-50 p-3">Verified label identity: <b>{scannedItem.title}</b> · SKU {scannedItem.sku || '—'} · live location {scannedItem.location_key} · {scannedItem.status}. Printed location is informational; database location is authoritative.{scannedItem.status === 'present' && location.unit_id ? <div className="mt-2 flex flex-wrap items-center gap-2"><Button variant="outline" onClick={moveScannedItem}>Move scanned item to active location</Button><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={allowDuplicate} onChange={(e) => setAllowDuplicate(e.target.checked)} /> Confirm separate physical unit at destination</label></div> : null}</div> : null}
        {labelPreview ? <div className="mt-4 overflow-x-auto rounded border bg-slate-50 p-3"><div className="mb-2 text-sm font-semibold">58 × 40 mm preview · 203 DPI source · device paper output remains unverified</div><img src={labelPreview} alt="Inventory label preview at 58 by 40 millimeters" style={{ width: '58mm', height: '40mm', maxWidth: 'none', imageRendering: 'pixelated' }} /></div> : null}
        {candidates.length ? <div className="mt-4 space-y-2" role="listbox" aria-label="Ranked product matches">{candidates.map((item, index) => <button type="button" role="option" aria-selected={selected?.listing_id === item.listing_id} key={item.listing_id} onClick={() => { setSelected(item); focusInput(); }} className={`flex w-full items-center gap-3 rounded-xl border p-3 text-left ${selected?.listing_id === item.listing_id ? 'border-blue-600 bg-blue-50' : ''}`}><span className="text-2xl font-bold">{index + 1}</span>{item.image ? <img src={item.image} alt="" className="h-16 w-16 rounded object-cover" /> : null}<span><b>{item.title}</b><br />SKU {item.sku || '—'} · {item.brand || ''} {item.model || ''} · {item.source || 'catalog'}<br />{item.status} · Current locations: {item.locations.join(', ') || 'not census-assigned'}</span></button>)}</div> : null}
        {lastItem ? <p className="mt-4 rounded-lg bg-amber-50 p-3">Saved {lastItem.title} at {lastItem.location_key}. Label job #{lastItem.print_job_id} is queued; browser print is available with “Reprint Last Label”. Physical printer completion is not verified.</p> : null}
        {error ? <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-red-800">{error}</p> : null}
      </section>
      <details className="rounded-2xl border p-4"><summary className="cursor-pointer text-lg font-bold">58 × 40 mm label template and calibration</summary>
        <p className="my-2 text-sm text-slate-600">Template v{template?.version || 2}; coordinates and text sizes use millimeters at native 203 DPI (464 × 320 dots). Phase 1 saved 300-DPI pixel positions are converted to millimeters on load. Paper size is 58 × 40 mm at 100%; the edge margin and clear preprint areas still need one authorized physical calibration.</p>
        {template ? <div className="grid grid-cols-2 gap-2 md:grid-cols-4">{Object.entries(template).filter(([key,value]) => typeof value === 'number' && (key.endsWith('_mm') || key.endsWith('_modules') || key.endsWith('_dots') || key === 'print_scale_percent')).map(([key,value]) => <label className="text-xs font-semibold" key={key}>{key.replaceAll('_',' ')}<input type="number" step={key.endsWith('_mm') ? '0.1' : '1'} className="mt-1 w-full rounded border p-2 text-sm" value={value} onChange={(e) => setTemplate((current) => ({ ...current, [key]: Number(e.target.value) }))} /></label>)}</div> : null}
        <div className="mt-3 flex flex-wrap gap-2"><Button onClick={saveTemplate} disabled={!template}>Save template</Button><Button variant="outline" onClick={printCalibration}>Print calibration test</Button></div>
      </details>
      <section className="rounded-2xl border bg-white p-4"><h2 className="text-lg font-bold">Label delivery</h2><div className="mt-2 grid gap-3 md:grid-cols-3"><label className="text-sm font-semibold">Print mode<select aria-label="Print mode" className="mt-1 w-full rounded border p-3" value={printSettings?.mode || 'browser'} onChange={(e) => savePrintSettings({ ...printSettings, mode: e.target.value })}><option value="browser">Browser print fallback</option><option value="automatic_agent">Automatic Windows agent</option><option value="queue_only">Queue for later</option><option value="disabled">Printing disabled</option></select></label><label className="text-sm font-semibold">Label layout<select className="mt-1 w-full rounded border p-3" value={printSettings?.layout || 'qr'} onChange={(e) => savePrintSettings({ ...printSettings, layout: e.target.value })}><option value="qr">QR only (default until physical tests)</option><option value="code128">Code 128 only</option><option value="qr_code128">QR + Code 128 (test before use)</option></select></label><div className="flex items-end"><Button variant="outline" onClick={createPairingCode}>Create Windows agent pairing code</Button></div></div>{pairingCode ? <p className="mt-3 rounded bg-amber-50 p-3 text-sm">One-time pairing code (expires in 10 minutes): <code>{pairingCode}</code>. Enter it into the explicitly launched Windows agent; this is not the long-lived agent credential.</p> : null}<div className="mt-3 space-y-2 text-sm">{printSettings?.agents?.length ? printSettings.agents.map((agent) => <div className="flex flex-wrap items-center gap-2" key={agent.id}><span>{agent.display_name} · {agent.printer_name} · {agent.status} · {agent.enabled ? 'enabled' : 'disabled'} · {agent.hardware_tested ? 'operator-marked physically tested' : 'not physically tested'}{agent.last_seen_at ? ` · last contact ${new Date(agent.last_seen_at).toLocaleString()}` : ''}</span>{agent.paired && agent.status === 'online' && !agent.enabled ? <Button variant="outline" onClick={() => setAgentEnabled(agent, true)}>Enable after physical tests</Button> : null}{agent.enabled ? <Button variant="outline" onClick={() => setAgentEnabled(agent, false)}>Disable</Button> : null}{agent.paired ? <Button variant="outline" onClick={() => revokeAgent(agent)}>Revoke</Button> : null}</div>) : <p>No Windows print agent paired. Automatic printing remains unavailable.</p>}</div></section>
      <section className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">{[['Processed',session?.items_processed],['Matched',session?.matched],['Unmatched',session?.unmatched],['Duplicate warnings',session?.duplicate_warnings],['Labels pending',session?.labels_pending],['Browser dispatched',session?.labels_browser_dispatched],['Spooler accepted',session?.labels_spooler_accepted],['Operator reported printed',session?.labels_operator_reported_printed],['Unknown outcome',session?.labels_unknown]].map(([label,value]) => <div className="rounded-xl border p-3" key={label}><div className="text-sm text-slate-600">{label}</div><div className="text-2xl font-bold">{value ?? 0}</div></div>)}</section>
      <section className="rounded-2xl border bg-white p-4"><h2 className="text-lg font-bold">Recent label jobs</h2><div className="mt-2 space-y-2 text-sm">{printJobs.length ? printJobs.map((job) => <div className="flex flex-wrap items-center gap-2 border-b py-2" key={job.job_id || job.database_id}><span>{job.status} · {job.item_uuid || 'item unavailable'}{job.spooler_job_id ? ` · Windows job ${job.spooler_job_id}` : ''}{job.error ? ` · ${job.error}` : ''}{job.outcome_note ? ` · ${job.outcome_note}` : ''}</span>{['UNKNOWN','SPOOLER_ACCEPTED','dispatched','FAILED'].includes(job.status) ? <><Button variant="outline" onClick={() => reconcilePrintJob(job, 'operator_saw_label')}>I saw the label</Button><Button variant="outline" onClick={() => reconcilePrintJob(job, 'operator_did_not_see_label')}>I did not see the label</Button></> : null}{job.item_uuid && !['queued','CLAIMED','RENDERED','SUBMITTING','UNKNOWN','dispatched'].includes(job.status) ? <Button variant="outline" onClick={() => reprintItem(job.item_uuid)}>Create reprint job</Button> : null}</div>) : <p>No label jobs have been created.</p>}</div><p className="mt-2 text-xs text-slate-600">A spooler acknowledgment or operator report is not an independent physical-print sensor result. Reprints create a new job and never add stock.</p></section>
      <p className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm">Print mode: {printSettings?.mode || 'browser'} · Windows target “Sparkles SKU Printer” uses a persistent outbound agent when paired · spooler acceptance is not proof of paper output; physical completion remains unverified.</p>
      <p className="text-sm text-slate-600">Session {sessionKey.slice(0, 8)} · Refresh-safe · Voice dictation waits for explicit Enter or button submission. Labels are generated at 203 DPI for a 58 × 40 mm page; driver scaling remains 100%.</p>
    </main>
  </AppShell>;
}
