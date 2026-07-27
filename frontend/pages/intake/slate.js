import Head from 'next/head';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Expand, FolderOpen, QrCode, Save } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';

import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import Input from '../../components/ui/input';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import { useAuth } from '../../contexts/AuthContext';
import { createIntakeSlate, fetchIntakeSessions, fetchIntakeSettings } from '../../lib/api';

const DEFAULT_FORM = {
  session_id: '',
  item_id: '',
  item_prefix: 'SP',
  box_id: '',
  box_prefix: 'BX',
  location: '',
  title: '',
  brand: '',
  model: '',
  condition: '',
  notes: '',
  flaws: '',
  weight: '',
  length: '',
  width: '',
  height: '',
  packed: false,
  boundary_position: 'start',
  internal_notes: '',
  mark_packed: false,
  increment_box: false,
  same_box: false,
};

const OFFLINE_SLATE_SETTINGS_KEY = 'posterpro-intake-slate-settings';
const OFFLINE_SLATE_COUNTER_KEY = 'posterpro-intake-slate-counters';

function Field({ label, children, hint }) {
  return (
    <label className="grid gap-2 text-sm">
      <span className="font-semibold text-[var(--pp-text)]">{label}</span>
      {children}
      {hint ? <span className="text-xs text-[var(--pp-muted)]">{hint}</span> : null}
    </label>
  );
}

export default function IntakeSlatePage() {
  const { user } = useAuth();
  const [settings, setSettings] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [slateResult, setSlateResult] = useState(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [offlineMode, setOfflineMode] = useState(false);

  useEffect(() => {
    if (!user?.id) return;
    (async () => {
      try {
        const [settingsPayload, sessionsPayload] = await Promise.all([
          fetchIntakeSettings(),
          fetchIntakeSessions(),
        ]);
        setSettings(settingsPayload || null);
        setSessions(sessionsPayload?.sessions || []);
        setForm((current) => ({
          ...current,
          session_id: sessionsPayload?.sessions?.[0]?.session_id || current.session_id,
          item_prefix: settingsPayload?.default_item_prefix || 'SP',
          box_prefix: settingsPayload?.default_box_prefix || 'BX',
          location: settingsPayload?.default_location || '',
        }));
        if (typeof window !== 'undefined') {
          window.localStorage.setItem(
            OFFLINE_SLATE_SETTINGS_KEY,
            JSON.stringify({
              session_id: sessionsPayload?.sessions?.[0]?.session_id || '',
              item_prefix: settingsPayload?.default_item_prefix || 'SP',
              box_prefix: settingsPayload?.default_box_prefix || 'BX',
              location: settingsPayload?.default_location || '',
            }),
          );
        }
      } catch (error) {
        let offlineDefaults = null;
        if (typeof window !== 'undefined') {
          try {
            offlineDefaults = JSON.parse(window.localStorage.getItem(OFFLINE_SLATE_SETTINGS_KEY) || 'null');
          } catch {
            offlineDefaults = null;
          }
        }
        if (offlineDefaults) {
          setOfflineMode(true);
          setForm((current) => ({
            ...current,
            session_id: offlineDefaults.session_id || current.session_id,
            item_prefix: offlineDefaults.item_prefix || current.item_prefix,
            box_prefix: offlineDefaults.box_prefix || current.box_prefix,
            location: offlineDefaults.location || current.location,
          }));
          toast.error('Intake defaults could not be loaded from the server. Offline slate mode is available.');
          return;
        }
        toast.error(error.message || 'Failed to load intake slate defaults.');
      }
    })();
  }, [user?.id]);

  const sessionOptions = useMemo(() => sessions.map((session) => session.session_id), [sessions]);

  const onChange = (key, value) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const buildOfflinePayload = () => {
    const now = new Date();
    let counters = { date: '', item: 0, box: 0 };
    if (typeof window !== 'undefined') {
      try {
        counters = JSON.parse(window.localStorage.getItem(OFFLINE_SLATE_COUNTER_KEY) || '{"date":"","item":0,"box":0}');
      } catch {
        counters = { date: '', item: 0, box: 0 };
      }
    }
    const dateToken = now.toISOString().slice(0, 10).replaceAll('-', '');
    const nextCounters = counters.date === dateToken
      ? { date: dateToken, item: Number(counters.item || 0) + 1, box: Number(counters.box || 0) + 1 }
      : { date: dateToken, item: 1, box: 1 };
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(OFFLINE_SLATE_COUNTER_KEY, JSON.stringify(nextCounters));
    }
    const itemId = form.item_id?.trim() || `${(form.item_prefix || 'SP').toUpperCase()}-${dateToken}-${String(nextCounters.item).padStart(4, '0')}`;
    const boxId = form.box_id?.trim() || `${(form.box_prefix || 'BX').toUpperCase()}-${String(nextCounters.box).padStart(4, '0')}`;
    return {
      type: 'posterpro_head_slate',
      version: 1,
      session_id: form.session_id?.trim() || settings?.default_session_naming_pattern || 'offline-session',
      item_id: itemId,
      box_id: boxId,
      location: form.location?.trim() || '',
      title: form.title?.trim() || '',
      brand: form.brand?.trim() || '',
      model: form.model?.trim() || '',
      condition: form.condition?.trim() || '',
      notes: form.notes?.trim() || '',
      flaws: form.flaws?.trim() || '',
      weight: form.weight?.trim() || '',
      length: form.length?.trim() || '',
      width: form.width?.trim() || '',
      height: form.height?.trim() || '',
      packed: Boolean(form.mark_packed || form.packed),
      boundary_position: form.boundary_position || 'start',
      created_at: now.toISOString(),
    };
  };

  const createOfflineSlate = () => {
    const qrPayload = buildOfflinePayload();
    setOfflineMode(true);
    setSlateResult({
      slate: {
        item_id: qrPayload.item_id,
        session_id: qrPayload.session_id,
        box_id: qrPayload.box_id,
        location: qrPayload.location,
        title: qrPayload.title,
        condition: qrPayload.condition,
        qr_payload_json: qrPayload,
      },
      qr_payload: qrPayload,
      qr_data_url: null,
      local_only: true,
    });
    toast.success(`Offline head slate prepared for ${qrPayload.item_id}.`);
  };

  const submit = async () => {
    setSaving(true);
    try {
      const payload = await createIntakeSlate(form);
      setSlateResult(payload || null);
      setOfflineMode(false);
      toast.success(`Head slate created for ${payload?.slate?.item_id || 'item'}.`);
    } catch (error) {
      toast.error(error.message || 'Failed to create head slate. Use Offline slate if you are working without signal.');
    } finally {
      setSaving(false);
    }
  };

  const nextItem = async () => {
    await submit();
    setForm((current) => ({
      ...current,
      item_id: '',
      title: '',
      brand: '',
      model: '',
      notes: '',
      flaws: '',
      internal_notes: '',
      packed: false,
      mark_packed: false,
    }));
  };

  const slate = slateResult?.slate || null;
  const qrPayload = slateResult?.qr_payload || null;
  const qrDataUrl = slateResult?.qr_data_url || null;

  return (
    <AppShell active="/intake" title="Intake Slate" contentWidth="wide">
      <Head>
        <link rel="manifest" href="/manifest.webmanifest" />
        <meta name="theme-color" content="#ffffff" />
      </Head>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Intake Slate"
          title="Generate the next item boundary marker"
          description="PosterPro head slates are photographed or screenshotted before each item. The QR payload becomes the machine-readable batch boundary that separates one item from the next inside the monitored album."
          actions={(
            <>
              <Button onClick={submit} disabled={saving}><Save size={16} /> {saving ? 'Saving…' : 'Save slate'}</Button>
              <Button onClick={createOfflineSlate} variant="secondary" disabled={saving}>Offline slate</Button>
              <Button onClick={nextItem} variant="secondary" disabled={saving}>Next item</Button>
              <Button href="/intake/queue" variant="outline"><FolderOpen size={16} /> Open queue</Button>
            </>
          )}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <SectionPanel title="Control mode" description="Fill the item metadata you know now. PosterPro uses this as intake truth and as a strong hint for downstream draft generation.">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Session ID" hint="Leave blank to use the default session naming pattern.">
                <Input list="intake-session-options" value={form.session_id} onChange={(event) => onChange('session_id', event.target.value)} placeholder={settings?.default_session_naming_pattern || '{date}-{location}'} />
                <datalist id="intake-session-options">
                  {sessionOptions.map((value) => <option key={value} value={value} />)}
                </datalist>
              </Field>
              <Field label="Storage location">
                <Input value={form.location} onChange={(event) => onChange('location', event.target.value)} placeholder="A-01" />
              </Field>
              <Field label="Item ID">
                <Input value={form.item_id} onChange={(event) => onChange('item_id', event.target.value)} placeholder="Auto-generated on save" />
              </Field>
              <Field label="Item prefix">
                <Input value={form.item_prefix} onChange={(event) => onChange('item_prefix', event.target.value)} placeholder="SP" />
              </Field>
              <Field label="Box ID">
                <Input value={form.box_id} onChange={(event) => onChange('box_id', event.target.value)} placeholder="Auto-generated on save" />
              </Field>
              <Field label="Box prefix">
                <Input value={form.box_prefix} onChange={(event) => onChange('box_prefix', event.target.value)} placeholder="BX" />
              </Field>
              <Field label="Optional title / item name">
                <Input value={form.title} onChange={(event) => onChange('title', event.target.value)} placeholder="Ryobi 40V charger" />
              </Field>
              <Field label="Condition">
                <Input value={form.condition} onChange={(event) => onChange('condition', event.target.value)} placeholder="Used" />
              </Field>
              <Field label="Boundary marker">
                <select className="rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-sm text-[var(--pp-text)]" value={form.boundary_position} onChange={(event) => onChange('boundary_position', event.target.value)}>
                  <option value="start">Start of item</option>
                  <option value="tail">Tail slate taken after photos</option>
                </select>
              </Field>
              <Field label="Brand">
                <Input value={form.brand} onChange={(event) => onChange('brand', event.target.value)} placeholder="Ryobi" />
              </Field>
              <Field label="Model">
                <Input value={form.model} onChange={(event) => onChange('model', event.target.value)} placeholder="Model number" />
              </Field>
              <Field label="Weight">
                <Input value={form.weight} onChange={(event) => onChange('weight', event.target.value)} placeholder="2 lb 8 oz" />
              </Field>
              <Field label="Dimensions">
                <div className="grid grid-cols-3 gap-2">
                  <Input value={form.length} onChange={(event) => onChange('length', event.target.value)} placeholder="L" />
                  <Input value={form.width} onChange={(event) => onChange('width', event.target.value)} placeholder="W" />
                  <Input value={form.height} onChange={(event) => onChange('height', event.target.value)} placeholder="H" />
                </div>
              </Field>
              <Field label="Notes">
                <textarea className="min-h-[108px] rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-sm text-[var(--pp-text)]" value={form.notes} onChange={(event) => onChange('notes', event.target.value)} placeholder="Accessories, known history, or notes for the draft description" />
              </Field>
              <Field label="Flaws / defects">
                <textarea className="min-h-[108px] rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-sm text-[var(--pp-text)]" value={form.flaws} onChange={(event) => onChange('flaws', event.target.value)} placeholder="Scratches, cracks, wear, missing pieces" />
              </Field>
              <Field label="Internal-only notes">
                <textarea className="min-h-[108px] rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-sm text-[var(--pp-text)]" value={form.internal_notes} onChange={(event) => onChange('internal_notes', event.target.value)} placeholder="Packing notes or operator-only reminders" />
              </Field>
              <div className="grid gap-3 rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 md:col-span-2">
                {[
                  ['same_box', 'Same box / tote for next item'],
                  ['increment_box', 'Increment box ID on next item'],
                  ['packed', 'Mark packed on slate'],
                ].map(([key, label]) => (
                  <label key={key} className="flex items-center gap-3 text-sm text-[var(--pp-text)]">
                    <input type="checkbox" checked={Boolean(form[key])} onChange={(event) => onChange(key, event.target.checked)} />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="mt-4 rounded-[18px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 text-sm text-[var(--pp-muted)]">
              <p className="font-semibold text-[var(--pp-text)]">{offlineMode ? 'Offline-capable slate mode is active.' : 'Live slate mode is active.'}</p>
              <p className="mt-2">
                Choose <strong>Tail slate taken after photos</strong> when you missed the opening slate and need PosterPro to recover the immediately preceding unassigned product photos into this item batch.
              </p>
            </div>
          </SectionPanel>

          <SectionPanel
            title="Fullscreen head slate"
            description="Use this screen as the photographed boundary marker. White background, black text, and a large QR payload are intentional so the screenshot or photographed screen stays machine-readable."
            action={<Button onClick={() => setFullscreen((current) => !current)} variant="secondary"><Expand size={16} /> {fullscreen ? 'Exit fullscreen' : 'Fullscreen slate'}</Button>}
          >
            <div className={fullscreen ? 'fixed inset-0 z-[100] overflow-auto bg-white p-6 text-black' : 'rounded-[28px] border border-[var(--pp-border)] bg-white p-6 text-black'}>
              <div className="mx-auto flex max-w-[820px] flex-col gap-8">
                <div>
                  <p className="text-[12px] font-semibold uppercase tracking-[0.22em] text-black/70">PosterPro Head Slate</p>
                  <h2 className="mt-4 text-[2.5rem] font-bold tracking-[-0.05em] text-black">{slate?.item_id || 'Create a slate to preview'}</h2>
                  <div className="mt-4 grid gap-3 text-lg md:grid-cols-2">
                    <p><span className="font-semibold">SESSION:</span> {slate?.session_id || form.session_id || '—'}</p>
                    <p><span className="font-semibold">BOX:</span> {slate?.box_id || form.box_id || '—'}</p>
                    <p><span className="font-semibold">LOC:</span> {slate?.location || form.location || '—'}</p>
                    <p><span className="font-semibold">DATE:</span> {(qrPayload?.created_at || '').replace('T', ' ').replace(/:[0-9]{2}(?:\.[0-9]+)?(?:[+-].*)?$/, '') || '—'}</p>
                    <p className="md:col-span-2"><span className="font-semibold">TITLE:</span> {slate?.title || form.title || '—'}</p>
                  </div>
                </div>
                <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
                  <div className="max-w-[430px] space-y-3 text-sm leading-7 text-black/80">
                    <p>This image is internal only. It marks the start of a new intake item and is excluded from public marketplace photos by default.</p>
                    <p>After photographing this slate, take the product photos, flaw photos, label photos, measurements, and packed-box photos. PosterPro will assign every following photo to this item until the next slate appears.</p>
                    {qrPayload?.boundary_position === 'tail' ? (
                      <p className="font-semibold">Tail slate mode: PosterPro will try to recover the photos immediately before this slate when they were missed at the beginning.</p>
                    ) : null}
                    {slateResult?.local_only ? (
                      <p className="font-semibold">Offline preview only: this slate is cached client-side for photographing now, but it will not exist in the server queue until you recreate or sync it online.</p>
                    ) : null}
                  </div>
                  <div className="flex min-h-[320px] min-w-[320px] items-center justify-center rounded-[28px] border-2 border-black p-4">
                    {qrDataUrl ? (
                      <img src={qrDataUrl} alt="PosterPro head slate QR code" className="h-full w-full object-contain" />
                    ) : qrPayload ? (
                      <QRCodeSVG value={JSON.stringify(qrPayload)} size={288} includeMargin />
                    ) : (
                      <QrCode size={64} />
                    )}
                  </div>
                </div>
              </div>
            </div>
          </SectionPanel>
        </div>
      </div>
    </AppShell>
  );
}
