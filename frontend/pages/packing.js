import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Clipboard, Mic, Package, ScanLine, Square, Tag, Truck } from 'lucide-react';
import toast from 'react-hot-toast';
import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import Input from '../components/ui/input';
import { attachIntakeSlatePhoto, attachIntakeSlateVoiceNote, createIntakeSlate, dispatchScannerScan, fetchScannerCommands, fetchScannerSession, findInventoryItem, moveInventoryStock } from '../lib/api';
import { completeIntakeEvent, failIntakeEvent, loadIntakeOfflineState, pendingIntakeEvents, queueIntakeEvent, saveIntakeOfflineState, syncIntakeJournal } from '../lib/intakeOfflineJournal';

const sessionKey = 'posterpro.packing.session.v1';
const makeId = () => (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`);
const blobToDataUrl = (blob) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(blob); });
const fileFromDataUrl = (dataUrl, name, type) => { const [meta, encoded] = String(dataUrl || '').split(','); if (!encoded) throw new Error('Queued photo data is incomplete.'); const mime = type || /data:([^;]+)/.exec(meta)?.[1] || 'image/jpeg'; const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0)); return new File([bytes], name || 'packing-photo.jpg', { type: mime }); };
const REGISTERED_OFFLINE_COMMANDS = new Set(['NEW ITEM', 'NEXT ITEM', 'FINISH ITEM', 'VOICE NOTE', 'NEXT TOTE', 'SELECT TOTE', 'NEW TOTE', 'NEW PACKAGE', 'FINISH PACKAGE', 'PRINT LABEL', 'REPRINT LABEL', 'SET COPIES', 'ADD QUANTITY', 'REMOVE QUANTITY', 'UNDO', 'SESSION STATUS', 'FIND ITEM', 'IDENTIFY', 'MOVE ITEM', 'AUDIT TOTE', 'PACK INTO TOTE', 'STORE OUTSIDE TOTE', 'BUNDLE', 'START BUNDLE', 'ADD TO BUNDLE', 'FINISH BUNDLE', 'CALIBRATE PHOTO TIME', 'FINISH SESSION']);
const LOCAL_STATE_COMMANDS = new Set(['new_item', 'next_item', 'finish_item', 'next_tote', 'select_tote', 'new_tote', 'find_item', 'add_quantity', 'remove_quantity', 'finish_session']);
const SERVER_ACTION_COMMANDS = new Set(['new_package', 'print_label', 'reprint_label', 'new_tote', 'find_item', 'move_item', 'start_bundle', 'add_to_bundle', 'finish_bundle']);
function packingCommand(value) {
  const normalized = String(value || '').trim().replace(/\s+/g, ' ').toUpperCase();
  const label = normalized.startsWith('POSTERPRO:COMMAND:') ? normalized.slice('POSTERPRO:COMMAND:'.length).replaceAll('_', ' ') : normalized;
  if (label === 'BUNDLE' || label === 'START BUNDLE') return 'start_bundle';
  return REGISTERED_OFFLINE_COMMANDS.has(label) ? label.replaceAll(' ', '_').toLowerCase() : null;
}
function nextPackingState(scan, current, eventId) {
  const next = { ...current, current: current.current ? { ...current.current } : null };
  const command = packingCommand(scan);
  if (next.awaiting_find_query && !command) {
    next.last_inventory_lookup = { found: false, query: String(scan || '').trim(), message: 'Lookup is waiting for sync.' };
    next.awaiting_find_query = false;
    next.current = null;
  } else if (next.awaiting_move_query && !command) {
    next.last_inventory_lookup = { found: false, query: String(scan || '').trim(), move_mode: true, message: 'Move lookup is waiting for sync.' };
    next.awaiting_move_query = false;
    next.current = null;
  } else if (next.awaiting_bundle_scan && !command) {
    next.awaiting_bundle_scan = false;
    next.current = null;
  } else if (next.awaiting_tote_code && !command) {
    next.tote = String(scan || '').trim().toUpperCase();
    next.awaiting_tote_code = false;
    next.current = null;
  } else if (!command) {
    next.current = { barcode: scan, product: null, quantity: next.quantity, slateId: null, captureEventId: eventId };
  } else if (command === 'new_item' || command === 'next_item' || command === 'finish_item') next.current = null;
  else if (command === 'next_tote' || command === 'select_tote') { next.tote = ''; next.current = null; }
  else if (command === 'new_tote') { next.awaiting_tote_code = true; next.awaiting_find_query = false; }
  else if (command === 'find_item') { next.awaiting_find_query = true; next.awaiting_tote_code = false; }
  else if (command === 'move_item') { next.awaiting_move_query = true; next.awaiting_find_query = false; next.awaiting_tote_code = false; }
  else if (command === 'start_bundle') {
    next.bundle_draft = { bundle_id: `PENDING-${eventId}`, status: 'collecting', components: [], stock_reserved: false, listing_created: false };
    next.awaiting_bundle_scan = false;
  }
  else if (command === 'add_to_bundle') { next.awaiting_bundle_scan = true; next.current = null; }
  else if (command === 'finish_bundle' && next.bundle_draft) { next.bundle_draft = { ...next.bundle_draft, status: 'finish_pending_sync' }; }
  else if (command === 'add_quantity') next.quantity = Number(next.quantity || 1) + 1;
  else if (command === 'remove_quantity') next.quantity = Math.max(1, Number(next.quantity || 1) - 1);
  else if (command === 'finish_session') { next.current = null; next.tote = ''; }
  return next;
}

function InventoryMoveControl({ lookup, session, setSession }) {
  const [sourceKey, setSourceKey] = useState('');
  const [quantity, setQuantity] = useState(1);
  const [destinationKind, setDestinationKind] = useState(session.current_package_code ? 'package' : 'tote');
  const [busy, setBusy] = useState(false);
  const item = lookup?.products?.[0];
  if (!lookup?.move_mode) return null;
  if (!item || lookup.products.length !== 1) return <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">Move is blocked: this scan did not resolve to exactly one owned product. Use FIND ITEM or resolve the barcode/product identity first.</p>;
  const locations = (item.locations || []).filter((location) => location.move_eligible && location.available > 0);
  const source = locations.find((location) => location.location_key === sourceKey) || locations[0];
  const effectiveQuantity = Math.min(quantity, source?.available || 1);
  const destinationCode = destinationKind === 'package' ? session.current_package_code : session.tote;
  const performMove = async () => {
    if (!destinationCode) { toast.error(`Select an active destination ${destinationKind} first.`); return; }
    if (!source || effectiveQuantity < 1) { toast.error('Choose a source and a quantity within available stock.'); return; }
    setBusy(true);
    try {
      const destination = await findInventoryItem(destinationCode);
      const destinationKey = destinationKind === 'package' ? destination?.package?.package_uuid : destination?.tote?.container_uuid;
      if (destination?.match_type !== destinationKind || !destinationKey) throw new Error(`Destination ${destinationKind} ${destinationCode} was not found in this operator inventory.`);
      const result = await moveInventoryStock({ product_id: item.product_id, quantity: effectiveQuantity, source_location_key: source.location_key, destination_location_key: destinationKey }, makeId());
      const refreshed = item.barcode ? await findInventoryItem(item.barcode) : null;
      setSession((current) => ({ ...current, last_inventory_lookup: { ...(refreshed || lookup), move_mode: true, query: item.barcode || lookup.query } }));
      toast.success(`Moved ${result.quantity} unit(s) to ${destinationKind} ${destinationCode}.`);
    } catch (error) { toast.error(error?.message || 'Inventory move failed.'); }
    finally { setBusy(false); }
  };
  return <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
    <p className="font-semibold">Move stock · {item.title || item.barcode}</p>
    {locations.length ? <div className="mt-2 flex flex-wrap items-end gap-2">
      <label className="grid gap-1">Source location<select aria-label="Move source location" className="rounded border px-2 py-2" value={source?.location_key || ''} onChange={(event) => setSourceKey(event.target.value)}>{locations.map((location) => <option key={location.location_key} value={location.location_key}>{location.label} · {location.available} available</option>)}</select></label>
      <label className="grid gap-1">Quantity<input aria-label="Move quantity" className="w-24 rounded border px-2 py-2" type="number" min="1" max={source?.available || 1} value={effectiveQuantity} onChange={(event) => setQuantity(Math.max(1, Number(event.target.value) || 1))}/></label>
      {session.current_package_code ? <label className="grid gap-1">Destination type<select aria-label="Move destination type" className="rounded border px-2 py-2" value={destinationKind} onChange={(event) => setDestinationKind(event.target.value)}><option value="package">Active package ({session.current_package_code})</option>{session.tote ? <option value="tote">Active tote ({session.tote})</option> : null}</select></label> : null}
      <p className="pb-2">Destination: {destinationCode || `select an active ${destinationKind} above`}</p>
      <Button disabled={busy || !source || !destinationCode} onClick={performMove}>{busy ? 'Moving…' : 'Confirm move'}</Button>
    </div> : <p className="mt-1">No owned location has available stock for this product.</p>}
    <p className="mt-2 text-xs text-amber-900">Reserved units cannot be moved. Confirm the source, destination and quantity before applying.</p>
  </div>;
}

export default function PackingWorkstation() {
  const [session, setSession] = useState({ id: makeId(), tote: '', quantity: 1, current: null, history: [] });
  const [manual, setManual] = useState('');
  const [commands, setCommands] = useState([]);
  const [recording, setRecording] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const scannerBuffer = useRef(''); const scannerTimer = useRef(null);
  const recorder = useRef(null); const audioChunks = useRef([]); const photoInput = useRef(null);
  const syncing = useRef(false);
  useEffect(() => { try { const saved = JSON.parse(localStorage.getItem(sessionKey)); if (saved?.id) { setSession(saved); fetchScannerSession(saved.id).then((remote) => { if (remote?.workstation_state) setSession((current) => ({ ...current, ...remote.workstation_state, id: saved.id, history: current.history })); }).catch(() => {}); } } catch {} }, []);
  useEffect(() => { localStorage.setItem(sessionKey, JSON.stringify(session)); }, [session]);
  useEffect(() => { fetchScannerCommands().then((r) => setCommands(r.commands || [])).catch(() => {}); }, []);
  const sendPackingEvent = useCallback(async (event) => {
    const payload = event.payload || {};
    if (event.type === 'packing_scan') {
      const result = await dispatchScannerScan(payload.scan, {
        sessionId: event.sessionId,
        operationId: event.id,
        workstationState: payload.workstationState,
      });
      let slateId = null;
      if (result.kind === 'barcode') {
        const created = await createIntakeSlate({
          session_id: event.sessionId,
          client_event_id: event.id,
          location: payload.workstationState?.tote || undefined,
          title: result.product?.title || undefined,
          quantity: String(payload.workstationState?.quantity || 1),
          notes: `Packing scanner capture: ${result.barcode}`,
        });
        slateId = created?.slate?.id || null;
        if (!slateId) throw new Error('Capture boundary was not confirmed by the server.');
        await saveIntakeOfflineState(`capture:${event.id}`, { slateId });
      }
      setSession((prior) => {
      const next = { ...prior, history: [{ at: event.createdAt, eventId: event.id, scan: payload.scan, result }, ...prior.history.filter((entry) => entry.eventId !== event.id)].slice(0, 30) };
      if (payload.workstationStateAfter) Object.assign(next, payload.workstationStateAfter, { history: next.history });
      if (result.workstation_state) Object.assign(next, result.workstation_state, { history: next.history });
      if (result.kind === 'barcode') next.current = { barcode: result.barcode, product: result.product, quantity: next.quantity, slateId, captureEventId: event.id };
      else if (next.current?.captureEventId === event.id) next.current = null;
      return next;
      });
      return result;
    }
    const capture = payload.slateId ? { slateId: payload.slateId } : await loadIntakeOfflineState(`capture:${payload.captureEventId}`);
    if (!capture?.slateId) throw new Error('The item boundary is not synced yet; media remains queued.');
    if (event.type === 'packing_photo') {
      await attachIntakeSlatePhoto(capture.slateId, fileFromDataUrl(payload.dataUrl, payload.name, payload.mimeType), event.id);
      return;
    }
    if (event.type === 'packing_voice') {
      await attachIntakeSlateVoiceNote(capture.slateId, { voice_audio_data_url: payload.dataUrl, client_event_id: event.id });
      return;
    }
    throw new Error(`Unsupported offline event: ${event.type}`);
  }, []);
  const syncPending = useCallback(async () => {
    if (typeof navigator === 'undefined' || !navigator.onLine || syncing.current) return;
    syncing.current = true;
    try {
      const replay = await syncIntakeJournal(sendPackingEvent);
      if (replay.failed === 0) {
        const remote = await fetchScannerSession(session.id);
        if (remote?.workstation_state) setSession((current) => ({ ...current, ...remote.workstation_state, id: session.id, history: current.history }));
      }
    }
    finally { syncing.current = false; setPendingCount((await pendingIntakeEvents()).length); }
  }, [sendPackingEvent, session.id]);
  useEffect(() => {
    pendingIntakeEvents().then((events) => setPendingCount(events.length)).catch(() => {});
    const onOnline = () => syncPending();
    window.addEventListener('online', onOnline);
    if (navigator.onLine) syncPending();
    return () => window.removeEventListener('online', onOnline);
  }, [syncPending]);
  const submit = useCallback(async (value) => {
    const scan = String(value || '').trim(); if (!scan) return;
    const eventId = makeId();
    const workstationStateAfter = nextPackingState(scan, session, eventId);
    const event = await queueIntakeEvent({ id: eventId, type: 'packing_scan', sessionId: session.id, payload: { scan, workstationState: { tote: session.tote, quantity: session.quantity, current: session.current }, workstationStateAfter } });
    setPendingCount((count) => count + 1);
    if (!navigator.onLine) {
      const command = packingCommand(scan);
      const result = command ? { kind: 'command', command, display: command.replaceAll('_', ' ').toUpperCase(), execution: LOCAL_STATE_COMMANDS.has(command) ? 'local_state_updated' : SERVER_ACTION_COMMANDS.has(command) ? 'server_action_pending_sync' : 'registered_action_not_implemented' } : { kind: 'pending_sync', display: 'Scan saved offline' };
      setSession((prior) => {
        const next = { ...prior, history: [{ at: event.createdAt, eventId, scan, result }, ...prior.history].slice(0, 30) };
        Object.assign(next, workstationStateAfter, { history: next.history });
        return next;
      });
      toast.success(result.kind === 'command' && result.execution === 'registered_action_not_implemented' ? `${result.display} recorded offline; its inventory action is not implemented yet.` : result.kind === 'command' && result.execution === 'server_action_pending_sync' ? `${result.display} saved offline; its server action will run after synchronization.` : 'Scan saved on this device; it will sync when connected.');
      return;
    }
    try {
      const result = await dispatchScannerScan(scan, { sessionId: session.id, operationId: eventId, workstationState: { tote: session.tote, quantity: session.quantity, current: session.current } });
      let captureSlate = null;
      // Barcode capture creates a durable item boundary before photos/voice
      // arrive. It is intentionally best-effort: an unavailable network must
      // never discard the scanner event that was already journaled server-side.
      if (result.kind === 'barcode') {
        try {
          const created = await createIntakeSlate({
          session_id: session.id, location: session.tote || undefined,
            client_event_id: eventId,
            title: result.product?.title || undefined, quantity: String(session.quantity || 1),
            notes: `Packing scanner capture: ${result.barcode}`,
          });
          captureSlate = created?.slate?.id || null;
        } catch (captureError) {
          toast.error('Scanner event saved; capture boundary will need an online retry.');
        }
      }
      setSession((prior) => {
        const next = { ...prior, history: [{ at: new Date().toISOString(), eventId, scan, result }, ...prior.history].slice(0, 30) };
        if (result.workstation_state) Object.assign(next, result.workstation_state);
        if (result.kind === 'barcode') next.current = { barcode: result.barcode, product: result.product, quantity: prior.quantity, slateId: captureSlate, captureEventId: eventId };
        if (result.kind === 'tote' && result.tote?.code) { next.tote = result.tote.code; next.current = null; }
        if (result.kind === 'inventory_lookup') { next.last_inventory_lookup = result.lookup; next.awaiting_find_query = false; next.current = null; }
        if (result.kind === 'inventory_move_selection') { next.last_inventory_lookup = { ...result.lookup, move_mode: true }; next.awaiting_move_query = false; next.current = null; }
        if (result.bundle_draft) next.bundle_draft = result.bundle_draft;
        if (result.kind === 'bundle_component_selection') { next.bundle_draft = result.bundle_draft; next.awaiting_bundle_scan = false; next.current = null; }
        if (result.command === 'new_tote') { next.awaiting_tote_code = true; next.awaiting_find_query = false; }
        if (result.command === 'find_item') { next.awaiting_find_query = true; next.awaiting_tote_code = false; }
        if (result.command === 'move_item') { next.awaiting_move_query = true; next.awaiting_find_query = false; next.awaiting_tote_code = false; }
        if (result.command === 'next_tote' || result.command === 'select_tote') { next.tote = ''; next.current = null; }
        if (result.command === 'new_item') next.current = null;
        if (result.command === 'add_quantity') next.quantity += 1;
        if (result.command === 'remove_quantity') next.quantity = Math.max(1, next.quantity - 1);
        if (result.command === 'finish_session') { next.current = null; next.tote = ''; }
        if (result.package?.package_code) next.current_package_code = result.package.package_code;
        if (result.label?.printable_url) next.last_printable_label_url = result.label.printable_url;
        return next;
      });
      await completeIntakeEvent(eventId);
      if (result.blocker) toast.error(result.blocker);
      else if (result.prompt) toast.success(result.prompt);
      else if (result.tote?.code) toast.success(`${result.tote.created ? 'Created' : 'Selected'} tote ${result.tote.code}.`);
      else if (result.kind === 'inventory_lookup') toast.success(result.lookup?.found ? 'Inventory location found.' : 'No matching owned inventory record.');
      else if (result.kind === 'inventory_move_selection') toast.success(result.lookup?.found ? 'Review source, destination and quantity before moving stock.' : 'No matching owned inventory record.');
      else if (result.kind === 'bundle_component_selection') toast.success(`Added ${result.bundle_component.quantity} × ${result.bundle_component.title} to the bundle capture. Stock remains unchanged.`);
      else if (result.warning) toast.success(result.warning);
      else toast.success(result.package?.package_code ? `Created package ${result.package.package_code}.` : result.label?.job_id ? `Label job #${result.label.job_id} queued; open the printable label from this workstation.` : result.kind === 'barcode' ? (result.product ? `Identified: ${result.product.title}` : 'Barcode recorded — product needs identification') : result.kind === 'command' && !LOCAL_STATE_COMMANDS.has(result.command) ? `${result.display || scan} recognized; the inventory action is not implemented yet.` : `${result.display || scan}`);
    } catch (error) {
      await failIntakeEvent(eventId, error?.message || error);
      setSession((prior) => ({ ...prior, ...workstationStateAfter, history: [{ at: event.createdAt, eventId, scan, result: { kind: 'pending_sync', display: 'Scan saved for retry' } }, ...prior.history].slice(0, 30) }));
      toast.error(error?.message || 'Scan is saved and will retry when connected.');
    }
    finally { setPendingCount((await pendingIntakeEvents()).length); }
  }, [session, session.id]);
  const readBlobDataUrl = (blob) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(blob); });
  const startVoice = async () => {
    if (!session.current?.slateId && !session.current?.captureEventId) { toast.error('Scan an item first so this voice note has a capture group.'); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunks.current = [];
      const active = new MediaRecorder(stream);
      active.ondataavailable = (event) => { if (event.data.size) audioChunks.current.push(event.data); };
      active.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop()); setRecording(false);
        try {
          const audio = new Blob(audioChunks.current, { type: active.mimeType || 'audio/webm' });
          if (audio.size > 25_000_000) throw new Error('Voice recording exceeds the offline queue limit of 25 MB.');
          const dataUrl = await readBlobDataUrl(audio);
          const queued = await queueIntakeEvent({ type: 'packing_voice', sessionId: session.id, payload: { slateId: session.current?.slateId || null, captureEventId: session.current?.captureEventId || null, dataUrl } });
          setPendingCount((count) => count + 1);
          if (navigator.onLine) await syncPending();
          const remaining = await pendingIntakeEvents();
          toast.success(remaining.some((event) => event.id === queued.id) ? 'Voice note safely queued for sync.' : 'Voice note attached to this item.');
        } catch (error) { toast.error(error?.message || 'Voice note could not be saved. Keep this screen open and retry.'); }
      };
      recorder.current = active; active.start(); setRecording(true); toast.success('Recording voice note.');
    } catch (error) { toast.error('Microphone permission is required to record a voice note.'); }
  };
  const stopVoice = () => recorder.current?.state === 'recording' && recorder.current.stop();
  const addPhoto = async (event) => {
    const file = event.target.files?.[0]; event.target.value = '';
    if (!file) return;
    if (!session.current?.slateId && !session.current?.captureEventId) { toast.error('Scan an item first so this photo has a capture group.'); return; }
    if (file.size > 15_000_000) { toast.error('Photo exceeds the 15 MB durable workstation queue limit.'); return; }
    try {
      const dataUrl = await blobToDataUrl(file);
      const queued = await queueIntakeEvent({ type: 'packing_photo', sessionId: session.id, payload: { slateId: session.current?.slateId || null, captureEventId: session.current?.captureEventId || null, dataUrl, name: file.name, mimeType: file.type } });
      setPendingCount((count) => count + 1);
      if (navigator.onLine) await syncPending();
      const remaining = await pendingIntakeEvents();
      toast.success(remaining.some((item) => item.id === queued.id) ? 'Photo safely queued for sync.' : 'Photo attached to this item’s capture group.');
    } catch (error) { toast.error(error?.message || 'Photo could not be queued.'); }
  };
  useEffect(() => {
    const onKey = (event) => {
      const tag = event.target?.tagName; if (tag === 'INPUT' || tag === 'TEXTAREA' || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === 'Enter' && scannerBuffer.current) { event.preventDefault(); submit(scannerBuffer.current); scannerBuffer.current = ''; return; }
      if (event.key.length !== 1) return;
      scannerBuffer.current += event.key;
      clearTimeout(scannerTimer.current);
      scannerTimer.current = setTimeout(() => { scannerBuffer.current = ''; }, 180);
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [submit]);
  const command = (label) => submit(`POSTERPRO:COMMAND:${label.replaceAll(' ', '_')}`);
  return <AppShell active="/packing" title="Packing workstation" contentWidth="wide">
    <div className="space-y-5">
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-widest text-slate-500">Continuous capture</p><h1 className="text-2xl font-bold text-slate-900">Scan · Photo · Voice · Pack</h1><p className="mt-1 text-sm text-slate-600">Keyboard-wedge and Bluetooth scans work anywhere on this screen—scan then press the reader’s Enter suffix.</p></div><div className="flex items-center gap-2"><span className={`rounded-full px-3 py-1 text-sm font-semibold ${pendingCount ? 'bg-amber-100 text-amber-900' : 'bg-sky-100 text-sky-800'}`}>{pendingCount ? `${pendingCount} capture event(s) waiting to sync` : 'Session active'}</span>{pendingCount ? <Button variant="secondary" onClick={syncPending}>Sync queued capture</Button> : null}</div></div>
        {session.awaiting_tote_code ? <p role="status" className="mt-3 rounded-lg bg-amber-50 p-3 text-sm font-medium text-amber-900">Scan or enter the new tote code next. It will not be treated as a product barcode.</p> : null}
        {session.awaiting_find_query ? <p role="status" className="mt-3 rounded-lg bg-sky-50 p-3 text-sm font-medium text-sky-900">Scan the item&apos;s barcode or enter its private inventory code to find its storage location.</p> : null}
        {session.awaiting_move_query ? <p role="status" className="mt-3 rounded-lg bg-amber-50 p-3 text-sm font-medium text-amber-900">Scan a product barcode to inspect available stock before moving it. No stock moves until you confirm source, destination and quantity.</p> : null}
        {session.awaiting_bundle_scan ? <p role="status" className="mt-3 rounded-lg bg-violet-50 p-3 text-sm font-medium text-violet-900">Scan an existing product barcode to add it to the active bundle capture. This records a review draft only; it does not move or reserve stock.</p> : null}
        <div className="mt-5 grid gap-3 md:grid-cols-3"><Input label="Active tote" value={session.tote} onChange={(e) => setSession({ ...session, tote: e.target.value })} placeholder="AO9" /><Input label="Quantity" type="number" min="1" value={session.quantity} onChange={(e) => setSession({ ...session, quantity: Math.max(1, Number(e.target.value) || 1) })} /><form onSubmit={(e) => { e.preventDefault(); submit(manual); setManual(''); }}><Input label="Manual / camera scan" value={manual} onChange={(e) => setManual(e.target.value)} placeholder="Scan or type barcode" /></form></div>
        <div className="mt-4 flex flex-wrap gap-2"><Button onClick={() => command('NEW ITEM')}><ScanLine size={16}/> New item</Button><Button variant="secondary" onClick={recording ? stopVoice : startVoice}>{recording ? <Square size={16}/> : <Mic size={16}/>} {recording ? 'Stop voice note' : 'Voice note'}</Button><input ref={photoInput} className="hidden" type="file" accept="image/*" capture="environment" onChange={addPhoto}/><Button variant="secondary" onClick={() => photoInput.current?.click()}><Camera size={16}/> Photograph</Button><Button variant="secondary" onClick={() => command('NEW PACKAGE')}><Package size={16}/> New package</Button><Button variant="secondary" onClick={() => command('PRINT LABEL')}><Tag size={16}/> Print label</Button><Button variant="secondary" onClick={() => command('NEXT TOTE')}><Truck size={16}/> Next tote</Button><Button variant="secondary" onClick={() => command('MOVE ITEM')}>Move item</Button><Button variant="secondary" onClick={() => command('BUNDLE')}>Start bundle</Button><Button variant="secondary" onClick={() => command('ADD TO BUNDLE')}>Add scanned item</Button><Button variant="secondary" onClick={() => command('FINISH BUNDLE')}>Finish bundle</Button></div>
      </section>
      {session.bundle_draft ? <section className="rounded-2xl border border-violet-200 bg-violet-50 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2"><div><h2 className="font-semibold text-violet-950">Bundle capture · {session.bundle_draft.bundle_id}</h2><p className="text-sm text-violet-900">Status: {session.bundle_draft.status} · {session.bundle_draft.stock_reserved ? 'stock reserved' : 'no stock reserved'} · {session.bundle_draft.listing_created ? 'listing linked' : 'not a listing'}</p></div><span className="rounded bg-white px-2 py-1 text-xs font-semibold text-violet-900">Review-only grouping</span></div>
        {session.bundle_draft.components?.length ? <ul className="mt-3 space-y-1 text-sm">{session.bundle_draft.components.map((component) => <li key={component.product_id}>{component.title || component.product_key} · {component.barcode || 'no barcode'} × {component.quantity} · {component.available_at_capture} available at scan</li>)}</ul> : <p className="mt-3 text-sm text-violet-900">No components scanned yet.</p>}
        {session.bundle_history?.length ? <div className="mt-3 border-t border-violet-200 pt-3"><p className="text-xs font-semibold uppercase tracking-wide text-violet-900">Earlier bundle captures</p><ul className="mt-1 space-y-1 text-sm">{session.bundle_history.slice(-5).map((bundle) => <li key={bundle.bundle_id}>{bundle.bundle_id} · {bundle.status} · {bundle.components?.map((component) => `${component.title} ×${component.quantity}`).join(', ')}</li>)}</ul></div> : null}
        <p className="mt-3 text-xs text-violet-900">Finishing saves a review checkpoint only. Bundle identity, component reservation, consolidated stock, and marketplace listing are not created.</p>
      </section> : null}
        <section className="grid gap-5 lg:grid-cols-2"><div className="rounded-2xl border border-slate-200 bg-white p-5"><h2 className="font-bold text-slate-900">Current item and package</h2>{session.current ? <div className="mt-3 rounded-xl bg-slate-50 p-4"><p className="font-semibold">{session.current.product?.title || 'Unidentified barcode'}</p><p className="text-sm text-slate-600">{session.current.barcode} · Qty {session.current.quantity} · Tote {session.tote || 'not selected'}</p><p className="mt-1 text-xs text-slate-500">Capture group #{session.current.slateId || 'awaiting connection'}</p></div> : <p className="mt-3 text-sm text-slate-500">Scan NEW ITEM then a product barcode. Photograph and voice controls attach evidence to the resulting capture group.</p>}{session.current_package_code ? <p className="mt-3 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900">Active package: <a className="font-semibold underline" href={`/inventory/packages/${encodeURIComponent(session.current_package_code)}`}>{session.current_package_code}</a> · open package</p> : <p className="mt-3 text-sm text-slate-500">No active package. Scan NEW PACKAGE to create a durable package identity.</p>}{session.last_printable_label_url ? <Button className="mt-2" variant="outline" onClick={() => window.open(`/api${session.last_printable_label_url}`, '_blank', 'noopener,noreferrer')}>Open printable package label</Button> : null}{session.last_inventory_lookup ? <div className="mt-3 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm"><p className="font-semibold">Inventory lookup · {session.last_inventory_lookup.found ? 'Match' : 'No match'}</p><p>{session.last_inventory_lookup.message || session.last_inventory_lookup.query}</p>{(session.last_inventory_lookup.products || []).map((product) => <div className="mt-2" key={product.product_id}><p className="font-medium">{product.title} · {product.barcode || product.product_key}</p>{product.locations?.map((location) => <p key={location.location_key} className="text-slate-700">{location.label}: available {location.available} / on hand {location.on_hand}</p>)}</div>)}{session.last_inventory_lookup.tote ? <p className="mt-1">Tote {session.last_inventory_lookup.tote.code}: {session.last_inventory_lookup.tote.contents?.map((item) => `${item.title} ×${item.on_hand}`).join(', ') || 'empty'}</p> : null}{session.last_inventory_lookup.package ? <p className="mt-1">Package {session.last_inventory_lookup.package.package_code}: {session.last_inventory_lookup.package.items?.map((item) => `${item.title} ×${item.on_hand}`).join(', ') || 'empty'}</p> : null}</div> : null}{session.last_inventory_lookup ? <InventoryMoveControl lookup={session.last_inventory_lookup} session={session} setSession={setSession}/> : null}<div className="mt-4 flex flex-wrap gap-2"><Button variant="secondary" onClick={() => command('FINISH ITEM')}>Finish item</Button><Button variant="secondary" onClick={() => command('SESSION STATUS')}>Session status</Button><Button variant="secondary" onClick={() => command('FIND ITEM')}>Find item</Button><Button variant="secondary" onClick={() => command('MOVE ITEM')}>Move item</Button><Button variant="secondary" onClick={() => command('BUNDLE')}>Start bundle</Button><Button variant="secondary" onClick={() => command('ADD TO BUNDLE')}>Add scanned item</Button><Button variant="secondary" onClick={() => command('FINISH BUNDLE')}>Finish bundle</Button></div></div><div className="rounded-2xl border border-slate-200 bg-white p-5"><h2 className="font-bold text-slate-900">Recent scanner journal</h2><div className="mt-3 max-h-64 space-y-2 overflow-auto">{session.history.length ? session.history.map((entry, i) => <div key={`${entry.at}-${i}`} className="rounded-lg border border-slate-100 px-3 py-2 text-sm"><span className="font-semibold">{entry.result.display || entry.scan}</span><span className="ml-2 text-slate-500">{entry.result.kind}</span></div>) : <p className="text-sm text-slate-500">Scanner events are journaled here and survive reload.</p>}</div></div></section>
      <details className="rounded-2xl border border-slate-200 bg-white p-5"><summary className="cursor-pointer font-bold"><Clipboard className="mr-2 inline" size={16}/> Command sheet ({commands.length})</summary><div className="mt-3 flex flex-wrap gap-2">{commands.map((item) => <Button key={item.command} variant="secondary" onClick={() => submit(item.payload)}>{item.label}</Button>)}</div></details>
    </div>
  </AppShell>;
}
