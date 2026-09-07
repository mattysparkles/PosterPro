import Head from 'next/head';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import toast from 'react-hot-toast';
import { CheckCircle2, ChevronDown, CloudUpload, Download, Expand, FileJson2, FolderOpen, Mic, MicOff, Printer, QrCode, RefreshCcw, Save, Sparkles, Undo2, X, PencilLine } from 'lucide-react';

import AppShell from '../../components/layout/AppShell';
import GooglePhotosConnectionGuide from '../../components/google/GooglePhotosConnectionGuide';
import Button from '../../components/ui/button';
import Input from '../../components/ui/input';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import StatusPill from '../../components/ui/status-pill';
import { useAuth } from '../../contexts/AuthContext';
import {
  analyzeVoiceIntake,
  createIntakeSlate,
  fetchGooglePhotosStatus,
  fetchIntakeSessions,
  fetchIntakeSettings,
  getGooglePhotosConnectUrl,
  markIntakeLabelWrittenOnBox,
  printIntakeLabel,
  startGooglePhotosOAuth,
  updateIntakeSettings,
  runIntakeMonitor,
  retryIntakeSlateBridgeUpload,
  transcribeVoiceIntake,
} from '../../lib/api';

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
  price: '',
  condition: '',
  quantity: '1',
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
  label_copies: 2,
};

const OFFLINE_SLATE_SETTINGS_KEY = 'posterpro-intake-slate-settings';
const OFFLINE_SLATE_COUNTER_KEY = 'posterpro-intake-slate-counters';

function Field({ label, children, hint, badge = null }) {
  return (
    <label className="grid gap-2 text-sm">
      <span className="flex items-center justify-between gap-3 font-semibold text-[var(--pp-text)]">
        <span>{label}</span>
        {badge ? <span>{badge}</span> : null}
      </span>
      {children}
      {hint ? <span className="text-xs text-[var(--pp-muted)]">{hint}</span> : null}
    </label>
  );
}

function LabelPreview({ payload, renderedDataUrl }) {
  const itemLabel = payload?.display_item_number || payload?.item_id || '—';
  const boxLabel = payload?.display_box_number || payload?.box_id || '—';
  const title = payload?.title || 'PENDING IDENTIFICATION';
  const copies = payload?.label_copies || 2;
  const location = payload?.location || '—';
  const quantity = payload?.quantity || '1';
  const sessionId = payload?.session_id || '—';

  if (renderedDataUrl) {
    return <img src={renderedDataUrl} alt="Rendered PosterPro label preview" className="w-full rounded-[20px] border border-[var(--pp-border)] bg-white object-contain" />;
  }

  return (
    <div className="rounded-[22px] border border-[var(--pp-border)] bg-white p-4 text-black">
      <div className="rounded-[16px] border-2 border-black p-4">
        <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-black/70">PosterPro</p>
        <p className="mt-1 text-2xl font-bold tracking-[-0.04em]">{payload?.boundary_position === 'tail' ? 'TAIL SLATE' : 'HEAD SLATE'}</p>
        <p className="mt-3 text-lg font-semibold">{title}</p>
        <div className="mt-3 grid gap-1 text-sm">
          <p><span className="font-semibold">ITEM</span> {itemLabel}</p>
          <p><span className="font-semibold">BOX</span> {boxLabel}</p>
          <p><span className="font-semibold">LOCATION</span> {location}</p>
          <p><span className="font-semibold">QTY</span> {quantity} · <span className="font-semibold">COPIES</span> {copies}</p>
          <p><span className="font-semibold">SESSION</span> {sessionId}</p>
        </div>
        <div className="mt-4 flex items-center gap-4">
          <div className="flex h-28 w-28 items-center justify-center rounded-[16px] border border-black bg-white p-2">
            <QrCode size={56} />
          </div>
          <div className="text-xs leading-5 text-black/70">
            <p className="font-semibold text-black">Digital label preview</p>
            <p>The physical printer is deferred, but this digital 58mm-style label is saved and printable later.</p>
          </div>
        </div>
      </div>
    </div>
  );
}

function SectionStateRow({ label, value, status = 'default' }) {
  const tone = status === 'success'
    ? 'text-emerald-700 bg-emerald-50 border-emerald-200'
    : status === 'warning'
      ? 'text-amber-700 bg-amber-50 border-amber-200'
      : status === 'danger'
        ? 'text-red-700 bg-red-50 border-red-200'
        : 'text-slate-700 bg-slate-50 border-slate-200';
  return (
    <div className={`flex items-center justify-between gap-3 rounded-2xl border px-3 py-2 text-sm ${tone}`}>
      <span className="font-semibold">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  );
}

// Workflow dialogs must escape page wrappers and theme containers. Rendering
// at document.body prevents legacy transforms/overflow/opacity rules from
// making the backdrop bleed through the readable modal surface.
function ModalPortal({ children }) {
  if (typeof document === 'undefined') return null;
  return createPortal(children, document.body);
}

function VoicePromptModal({ open, onClose, transcript, form, analysis }) {
  if (!open) return null;
  const text = String(transcript || '').toLowerCase();
  const voice = analysis?.voice_intelligence || analysis || {};
  const topics = [
    {
      label: 'Item Name / Title',
      helper: 'Say the common name, brand name, or item title.',
      hit: Boolean(form?.title?.trim()) || /\b(title|item name)\b/.test(text),
    },
    {
      label: 'Brand / Model / Part Number',
      helper: 'Say the brand, model, OEM number, MPN, or part number.',
      hit: Boolean(form?.brand?.trim() || form?.model?.trim()) || /\b(brand|model|part number|mpn|oem)\b/.test(text),
    },
    {
      label: 'Price',
      helper: 'Optional. Mention asking price or target price if you know it.',
      hit: Boolean(form?.price?.trim()) || /\bprice\b/.test(text) || /\$\s*\d/.test(text),
    },
    {
      label: 'Weight / Size',
      helper: 'Optional. Mention weight, length, width, height, or dimensions.',
      hit: Boolean(form?.weight?.trim() || form?.length?.trim() || form?.width?.trim() || form?.height?.trim()) || /\b(weight|ounces?|pounds?|lbs?|dimensions?)\b/.test(text),
    },
    {
      label: 'Condition',
      helper: 'Say new, used, open box, for parts, or any visible flaws.',
      hit: Boolean(form?.condition?.trim()) || /\b(condition|new|used|open box|for parts|broken|flaw|defect)\b/.test(text),
    },
    {
      label: 'Quantity',
      helper: 'Say how many you have or how many should be listed together.',
      hit: Boolean(Number(form?.quantity || 0) > 1) || /\b(quantity|two|three|four|five|six|seven|eight|nine|ten|pair|double|triple)\b/.test(text),
    },
    {
      label: 'Marketplace targets',
      helper: 'Mention eBay, Facebook, Mercari, Poshmark, or Vinted if you want a target.',
      hit: Boolean((analysis?.marketplace_targets || []).length) || /\b(ebay|facebook|mercari|poshmark|vinted)\b/.test(text),
    },
    {
      label: 'Research / sold comps',
      helper: 'Ask PosterPro to research sold comps or verify the exact item.',
      hit: /\b(research|sold comps|comps|identify|verify)\b/.test(text) || Boolean(voice?.quality?.ready_for_photo_research),
    },
  ];

  return (
    <ModalPortal><div className="fixed inset-0 z-[2147483000] flex items-center justify-center bg-[#07111f]/95 p-4 backdrop-blur-sm">
      <div className="w-full max-w-3xl rounded-[28px] border border-white/10 bg-white shadow-[0_24px_90px_rgba(15,23,42,0.55)]">
        <div className="flex items-center justify-between gap-4 border-b border-slate-200 px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Voice guidance</p>
            <h3 className="text-2xl font-semibold text-slate-950">Trigger words are optional</h3>
            <p className="mt-1 text-sm text-slate-600">Use any wording you like. This checklist only shows the useful topics PosterPro has already heard.</p>
          </div>
          <Button onClick={onClose} variant="outline"><X size={16} /> Close</Button>
        </div>
        <div className="grid gap-4 p-6 md:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-sm font-semibold text-slate-900">Live transcript</p>
            <p className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-[18px] border border-slate-200 bg-white px-4 py-3 text-sm leading-6 text-slate-800">
              {transcript || 'Start a voice note and speak naturally. PosterPro will listen for useful details as you talk.'}
            </p>
          </div>
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-sm font-semibold text-slate-900">What to say</p>
            <div className="mt-3 grid gap-2">
              {topics.map((topic) => (
                <div key={topic.label} className={`flex items-start gap-3 rounded-[18px] border px-3 py-3 ${topic.hit ? 'border-emerald-200 bg-emerald-50 text-emerald-950' : 'border-slate-200 bg-white text-slate-900'}`}>
                  <div className={`mt-0.5 flex h-5 w-5 items-center justify-center rounded-full border ${topic.hit ? 'border-emerald-500 bg-emerald-500 text-white' : 'border-slate-300 bg-slate-100 text-slate-500'}`}>
                    <CheckCircle2 size={13} />
                  </div>
                  <div>
                    <p className="font-semibold">{topic.label}</p>
                    <p className="text-xs leading-5 opacity-80">{topic.helper}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div></ModalPortal>
  );
}

function AiInspectorModal({ open, onClose, payload }) {
  if (!open) return null;
  const transcript = payload?.voice_intelligence?.voice_transcript || payload?.voice_transcript || '';
  const structured = payload?.structured_listing_json || payload?.voice_intelligence?.structured_listing_json || {};
  const aiMetadata = payload?.ai_metadata || payload?.voice_intelligence?.ai_metadata || {};
  const validation = payload?.validation || {};
  const state = payload?.intelligence_state || payload?.voice_intelligence?.intelligence_state || {};
  const prettyStructured = JSON.stringify(structured, null, 2);
  return (
    <ModalPortal><div className="fixed inset-0 z-[2147483000] flex items-center justify-center bg-[#07111f]/95 p-4 backdrop-blur-sm">
      <div className="w-full max-w-4xl rounded-[28px] border border-[var(--pp-border)] bg-white shadow-2xl">
        <div className="flex items-center justify-between gap-4 border-b border-[var(--pp-border)] px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--pp-muted)]">AI Details</p>
            <h3 className="text-2xl font-semibold text-[var(--pp-text)]">Voice transcript and structured listing JSON</h3>
          </div>
          <Button onClick={onClose} variant="outline"><X size={16} /> Close</Button>
        </div>
        <div className="grid gap-4 p-6 lg:grid-cols-[0.95fr_1.05fr]">
          <div className="grid gap-4">
            <div className="rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
              <p className="text-sm font-semibold text-[var(--pp-text)]">Processing state</p>
              <div className="mt-3 grid gap-2">
                <SectionStateRow label="Voice recorded" value={state.voice_recorded ? '✓' : '—'} status={state.voice_recorded ? 'success' : 'default'} />
                <SectionStateRow label="Transcript sent" value={state.transcript_sent ? '✓' : '—'} status={state.transcript_sent ? 'success' : 'default'} />
                <SectionStateRow label="Enrichment sent" value={state.enrichment_sent ? '✓' : '—'} status={state.enrichment_sent ? 'success' : 'default'} />
                <SectionStateRow label="Structured JSON" value={state.structured_json_received ? '✓' : '—'} status={state.structured_json_received ? 'success' : 'default'} />
                <SectionStateRow label="Fields populated" value={state.fields_populated ? '✓' : '—'} status={state.fields_populated ? 'success' : 'default'} />
              </div>
            </div>
            <div className="rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 text-sm text-[var(--pp-muted)]">
              <p className="font-semibold text-[var(--pp-text)]">AI metadata</p>
              <div className="mt-3 grid gap-2">
                <p><span className="font-semibold">Model:</span> {aiMetadata.model || '—'}</p>
                <p><span className="font-semibold">Source:</span> {aiMetadata.generation_source || '—'}</p>
                <p><span className="font-semibold">Schema:</span> {aiMetadata.schema_version || '—'}</p>
                <p><span className="font-semibold">Request ID:</span> {aiMetadata.request_id || '—'}</p>
                <p><span className="font-semibold">Latency:</span> {aiMetadata.latency_ms ? `${aiMetadata.latency_ms} ms` : '—'}</p>
                <p><span className="font-semibold">Validation:</span> {validation.structured_json_valid ? 'validated' : 'fallback'}</p>
              </div>
            </div>
            <div className="rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 text-sm text-[var(--pp-muted)]">
              <div className="flex items-center justify-between">
                <p className="font-semibold text-[var(--pp-text)]">Transcript</p>
                <Sparkles size={16} className="text-[var(--pp-accent)]" />
              </div>
              <p className="mt-3 whitespace-pre-wrap rounded-2xl bg-white px-4 py-3 text-[var(--pp-text)]">{transcript || '—'}</p>
            </div>
          </div>
          <div className="grid gap-4">
            <div className="rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-[var(--pp-text)]">Structured listing JSON</p>
                <FileJson2 size={16} className="text-[var(--pp-muted)]" />
              </div>
              <pre className="mt-3 max-h-[46vh] overflow-auto rounded-[20px] bg-slate-950 px-4 py-4 text-xs leading-6 text-slate-100">{prettyStructured}</pre>
            </div>
          </div>
        </div>
      </div>
    </div></ModalPortal>
  );
}

function SlateSuccessModal({ open, onClose, onNext, onWriteOnBox, onPrint, onRetryUpload, payload }) {
  if (!open) return null;
  const slate = payload?.slate || null;
  const googleStatus = slate?.metadata_json?.google_photos || {};
  const renderedSlateUrl = payload?.rendered_slate_data_url || payload?.rendered_slate_url || slate?.metadata_json?.rendered_slate?.data_url || slate?.metadata_json?.rendered_slate?.storage_path || null;
  const renderedLabelUrl = payload?.rendered_label_data_url || payload?.rendered_label_url || slate?.metadata_json?.rendered_label?.data_url || slate?.metadata_json?.rendered_label?.storage_path || null;
  const renderedSlateDownloadUrl = payload?.rendered_slate_url || slate?.metadata_json?.rendered_slate?.storage_path || null;
  const renderedLabelDownloadUrl = payload?.rendered_label_url || slate?.metadata_json?.rendered_label?.storage_path || null;
  const title = slate?.title || payload?.voice_intelligence?.canonical_listing?.human_readable_name || payload?.voice_intelligence?.title || 'PENDING IDENTIFICATION';
  const googleState = googleStatus.last_upload_status || payload?.bridge_upload?.status || 'pending';
  const googleQueued = String(googleState).toLowerCase().includes('queued') || String(googleState).toLowerCase().includes('submitted');
  const googleOk = String(googleState).toLowerCase().includes('success') || String(googleState).toLowerCase().includes('uploaded') || String(googleState).toLowerCase().includes('completed');
  return (
    <ModalPortal><div className="fixed inset-0 z-[2147483000] overflow-y-auto bg-[#07111f]/95 p-4 backdrop-blur-sm">
      <div className="mx-auto mt-6 w-full max-w-6xl rounded-[30px] border border-slate-200 bg-white shadow-[0_30px_90px_rgba(15,23,42,0.5)]">
        <div className="flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Success</p>
            <h3 className="flex items-center gap-2 text-2xl font-semibold text-slate-950"><CheckCircle2 size={22} className="text-emerald-600" /> Head slate created</h3>
          </div>
          <Button onClick={onClose} variant="outline"><X size={16} /> Close</Button>
        </div>
        <div className="grid gap-6 p-6 xl:grid-cols-[1fr_1.2fr_1fr]">
          <div className="grid gap-4">
            <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
              <p className="text-sm font-semibold text-slate-900">Google Photos</p>
              <p className="mt-2 text-2xl font-semibold text-slate-950">{googleOk ? 'Uploaded ✓' : googleQueued ? 'Upload queued' : 'Pending / Failed'}</p>
              <p className="mt-2 text-sm text-slate-700">Album: PosterPro</p>
              <p className="mt-1 text-sm text-slate-700 break-all">Status: {googleState}</p>
              <p className="mt-1 text-sm text-slate-700">Timestamp: {slate?.metadata_json?.rendered_at || slate?.created_at || '—'}</p>
              {googleStatus.google_album_url || googleStatus.target_album_url ? <p className="mt-1 text-xs text-slate-500 break-all">Target: {googleStatus.google_album_url || googleStatus.target_album_url}</p> : null}
              {googleStatus.last_upload_error || payload?.bridge_upload?.error ? (
                <div className="mt-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                  <p>{googleStatus.last_upload_error || payload?.bridge_upload?.error}</p>
                  {/403|Photos Library API|photoslibrary\.googleapis\.com/i.test(String(googleStatus.last_upload_error || payload?.bridge_upload?.error)) ? (
                    <a className="mt-2 inline-flex font-semibold underline" href="https://console.cloud.google.com/apis/library/photoslibrary.googleapis.com?project=996127842854" target="_blank" rel="noreferrer">Enable Google Photos Library API in project 996127842854</a>
                  ) : null}
                </div>
              ) : null}
              {!googleOk && slate?.id ? (
                <div className="mt-4">
                  <Button onClick={onRetryUpload} variant="secondary">Retry Google upload</Button>
                </div>
              ) : null}
            </div>
            <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
              <p className="text-sm font-semibold text-slate-900">Item identity</p>
              <p className="mt-2 text-4xl font-bold tracking-[-0.05em] text-slate-950">{slate?.metadata_json?.display_item_number || slate?.item_id || '—'}</p>
              <p className="mt-1 text-3xl font-bold tracking-[-0.04em] text-slate-950">{slate?.metadata_json?.display_box_number || slate?.box_id || '—'}</p>
              <p className="mt-3 text-lg text-slate-900">{slate?.location || '—'}</p>
              <p className="mt-1 text-sm text-slate-600">{title}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {renderedSlateUrl ? (
                  <Button href={renderedSlateUrl} variant="outline" target="_blank" rel="noreferrer">View rendered slate</Button>
                ) : (
                  <Button variant="outline" disabled>View rendered slate</Button>
                )}
                {renderedSlateDownloadUrl ? (
                  <Button href={renderedSlateDownloadUrl} download variant="outline"><Download size={16} /> Download slate</Button>
                ) : (
                  <Button variant="outline" disabled><Download size={16} /> Download slate</Button>
                )}
                {renderedSlateUrl ? (
                  <Button
                    onClick={async () => {
                      try {
                        const response = await fetch(renderedSlateUrl, { credentials: 'include' });
                        const blob = await response.blob();
                        const file = new File([blob], `${slate?.item_id || 'posterpro-slate'}.png`, { type: blob.type || 'image/png' });
                        if (navigator.share && (!navigator.canShare || navigator.canShare({ files: [file] }))) {
                          await navigator.share({
                            files: [file],
                            title: 'PosterPro slate',
                            text: 'Save this rendered PosterPro slate to Photos / camera roll.',
                          });
                          return;
                        }
                      } catch {
                        // fall through to anchor download
                      }
                      const anchor = document.createElement('a');
                      anchor.href = renderedSlateUrl;
                      anchor.download = `${slate?.item_id || 'posterpro-slate'}.png`;
                      anchor.rel = 'noreferrer';
                      document.body.appendChild(anchor);
                      anchor.click();
                      anchor.remove();
                    }}
                    variant="secondary"
                  >
                    Save to device
                  </Button>
                ) : null}
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {renderedSlateUrl ? (
                  <Button href={renderedSlateUrl} variant="secondary" target="_blank" rel="noreferrer">Open rendered slate image</Button>
                ) : null}
                {renderedLabelDownloadUrl ? (
                  <Button href={renderedLabelDownloadUrl} download variant="outline"><Download size={16} /> Download label</Button>
                ) : null}
              </div>
            </div>
          </div>
          <div className="grid gap-4">
            <div className="rounded-[28px] border border-slate-200 bg-white p-4 text-slate-950">
              <div className="rounded-[22px] border-4 border-lime-400 bg-lime-50 p-4">
                {renderedSlateUrl ? (
                  <img src={renderedSlateUrl} alt="Generated PosterPro slate" className="h-[440px] w-full rounded-[18px] object-contain bg-white" />
                ) : (
                  <div className="flex h-[440px] items-center justify-center rounded-[18px] border border-dashed border-slate-300 bg-white text-center">
                    <div>
                      <p className="text-2xl font-bold">Slate thumbnail pending</p>
                      <p className="mt-2 text-sm text-slate-700">The generated slate image will appear here once rendered.</p>
                    </div>
                  </div>
                )}
                <div className="mt-3 text-xs text-slate-700">
                  Bright border + large QR are designed to make the slate easy to detect in the camera stream.
                </div>
              </div>
            </div>
            <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
              <p className="text-sm font-semibold text-slate-900">Actions</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button onClick={onWriteOnBox} variant="outline">Write on box</Button>
                <Button onClick={onPrint} variant="secondary">Print 2 copies</Button>
                <Button onClick={onNext} variant="primary">Next item</Button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {renderedSlateUrl ? (
                  <Button href={renderedSlateUrl} variant="outline" target="_blank" rel="noreferrer">View rendered slate</Button>
                ) : (
                  <Button variant="outline" disabled>View rendered slate</Button>
                )}
                {renderedSlateDownloadUrl ? (
                  <Button href={renderedSlateDownloadUrl} download variant="outline"><Download size={16} /> Download slate</Button>
                ) : (
                  <Button variant="outline" disabled><Download size={16} /> Download slate</Button>
                )}
                {slate?.listing_id ? (
                  <Button href={`/listings/${slate.listing_id}`} variant="outline">View item</Button>
                ) : (
                  <Button variant="outline" disabled>View item</Button>
                )}
                <Button onClick={onClose} variant="outline">Keep open</Button>
              </div>
            </div>
          </div>
          <div className="grid gap-4">
            <LabelPreview payload={{
              display_item_number: slate?.metadata_json?.display_item_number,
              display_box_number: slate?.metadata_json?.display_box_number,
              title,
              location: slate?.location,
              quantity: slate?.qr_payload_json?.quantity || '1',
              label_copies: slate?.metadata_json?.label_default_copies || 2,
              boundary_position: slate?.qr_payload_json?.boundary_position || 'start',
              session_id: slate?.session_id,
              item_id: slate?.item_id,
              box_id: slate?.box_id,
            }} renderedDataUrl={renderedLabelUrl} />
            <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
              <p className="font-semibold text-slate-900">Slate and label proof</p>
              <p className="mt-2">This modal shows the actual rendered slate asset and the saved label mockup before moving to the next item.</p>
            </div>
          </div>
        </div>
      </div>
    </div></ModalPortal>
  );
}

function shouldReplaceVoiceField(currentValue, suggestion) {
  const current = String(currentValue || '').trim();
  const next = String(suggestion || '').trim();
  if (!next) return false;
  if (!current) return true;
  if (/^(pending identification|needs review|unknown|n\/a|na)$/i.test(current)) return true;
  return false;
}

function isAiPopulatedValue(currentValue, suggestion) {
  const current = String(currentValue || '').trim();
  const next = String(suggestion || '').trim();
  if (!current || !next) return false;
  return current.toLowerCase() === next.toLowerCase();
}

function toDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Unable to read recorded audio.'));
    reader.onload = () => resolve(String(reader.result || ''));
    reader.readAsDataURL(blob);
  });
}

function spokenNumberToInt(value) {
  const normalized = String(value || '').trim().toLowerCase();
  const mapping = {
    one: 1,
    two: 2,
    three: 3,
    four: 4,
    five: 5,
    six: 6,
    seven: 7,
    eight: 8,
    nine: 9,
    ten: 10,
  };
  if (/^\d+$/.test(normalized)) return Number(normalized);
  return mapping[normalized] || null;
}

function parseVoiceCommands(text) {
  const transcript = String(text || '').trim();
  const result = { fields: {}, action: null, raw: transcript };
  const actions = [
    ['generate_tail', /\b(generate (?:a )?tail slate|tail slate)\b/i],
    ['generate_head', /\b(generate (?:a )?(?:head )?slate|generate head slate|head slate)\b/i],
    ['next_item', /\bnext item\b/i],
    ['new_item', /\bnew item\b/i],
    ['write_on_box', /\bwrite on box\b/i],
    ['print_label', /\bprint label\b/i],
    ['undo_last_change', /\bundo that\b/i],
  ];
  for (const [action, pattern] of actions) {
    if (pattern.test(transcript)) {
      result.action = action;
      break;
    }
  }

  const quantityMatch = transcript.match(/\bquantity\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b/i);
  if (quantityMatch?.[1]) {
    const parsed = spokenNumberToInt(quantityMatch[1]);
    if (parsed) result.fields.quantity = String(parsed);
  }

  const locationMatch = transcript.match(/\blocation\s+(.+?)(?=(?:\bbrand\b|\bcondition\b|\bquantity\b|\bsell\b|\bresearch\b|\bgenerate\b|\bnew item\b|\bnext item\b|\bprint label\b|\bwrite on box\b|\bundo that\b|$))/i);
  if (locationMatch?.[1]) result.fields.location = locationMatch[1].trim().replace(/[.,;]+$/, '');

  const brandMatch = transcript.match(/\bbrand\s+(.+?)(?=(?:\bmodel\b|\bcondition\b|\bquantity\b|\bsell\b|\bresearch\b|\bgenerate\b|$))/i);
  if (brandMatch?.[1]) result.fields.brand = brandMatch[1].trim().replace(/[.,;]+$/, '');

  const modelMatch = transcript.match(/\bmodel\s+(.+?)(?=(?:\bbrand\b|\bcondition\b|\bquantity\b|\bsell\b|\bresearch\b|\bgenerate\b|$))/i);
  if (modelMatch?.[1]) result.fields.model = modelMatch[1].trim().replace(/[.,;]+$/, '');

  const titleMatch = transcript.match(/\b(?:title|item name)\s+(.+?)(?=(?:\bbrand\b|\bmodel\b|\bcondition\b|\bquantity\b|\bprice\b|\bweight\b|\bsell\b|\bresearch\b|\bgenerate\b|$))/i);
  if (titleMatch?.[1]) result.fields.title = titleMatch[1].trim().replace(/[.,;]+$/, '');

  const priceMatch = transcript.match(/\bprice(?:\s+(?:at|of))?\s+(\$?\d+(?:\.\d{1,2})?|\d+\s*dollars?)(?=(?:\bbrand\b|\bmodel\b|\bcondition\b|\bquantity\b|\bweight\b|\bsell\b|\bresearch\b|\bgenerate\b|$))/i);
  if (priceMatch?.[1]) result.fields.price = priceMatch[1].trim().replace(/[.,;]+$/, '');

  const weightMatch = transcript.match(/\bweight\s+(.+?)(?=(?:\btitle\b|\bitem name\b|\bbrand\b|\bmodel\b|\bcondition\b|\bquantity\b|\bprice\b|\bsell\b|\bresearch\b|\bgenerate\b|$))/i);
  if (weightMatch?.[1]) result.fields.weight = weightMatch[1].trim().replace(/[.,;]+$/, '');

  const conditionMatch = transcript.match(/\bcondition\s+(.+?)(?=(?:\bquantity\b|\bsell\b|\bresearch\b|\bgenerate\b|$))/i);
  if (conditionMatch?.[1]) result.fields.condition = conditionMatch[1].trim().replace(/[.,;]+$/, '');

  if (/\bsell individually\b/i.test(transcript)) result.fields.bundle_strategy = 'individual';
  if (/\bsell all together\b/i.test(transcript)) result.fields.bundle_strategy = 'together';
  if (/\bresearch sold comps\b/i.test(transcript)) result.fields.pricing_instruction = 'Research sold comps and price competitively.';

  const marketplaces = [];
  if (/\bebay\b/i.test(transcript)) marketplaces.push('ebay');
  if (/\bfacebook\b/i.test(transcript)) marketplaces.push('facebook');
  if (/\bmercari\b/i.test(transcript)) marketplaces.push('mercari');
  if (/\bposhmark\b/i.test(transcript)) marketplaces.push('poshmark');
  if (/\bvinted\b/i.test(transcript)) marketplaces.push('vinted');
  if (marketplaces.length) result.fields.marketplace_targets = marketplaces;

  return result;
}

export default function IntakeSlatePage() {
  const { user } = useAuth();
  const googlePhotosConnectUrl = getGooglePhotosConnectUrl();
  const googlePhotosRedirectUri =
    typeof window !== 'undefined'
      ? `${window.location.origin}/api/intake/google-photos/callback`
      : 'https://posterpro.sparkleserver.site/api/intake/google-photos/callback';
  const [settings, setSettings] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [googleStatus, setGoogleStatus] = useState(null);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [slateResult, setSlateResult] = useState(null);
  const [slateSuccessOpen, setSlateSuccessOpen] = useState(false);
  const [aiInspectorOpen, setAiInspectorOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [offlineMode, setOfflineMode] = useState(false);
  const [autoDraftMode, setAutoDraftMode] = useState(true);
  const [autoDraftSaving, setAutoDraftSaving] = useState(false);
  const [voiceRecording, setVoiceRecording] = useState(false);
  const [voiceTranscript, setVoiceTranscript] = useState('');
  const [voiceAudioDataUrl, setVoiceAudioDataUrl] = useState('');
  const [voiceAnalysis, setVoiceAnalysis] = useState(null);
  const [voiceError, setVoiceError] = useState('');
  const [voiceWorking, setVoiceWorking] = useState(false);
  const [voiceTranscribing, setVoiceTranscribing] = useState(false);
  const [syncQueued, setSyncQueued] = useState(false);
  const [lastVoiceSnapshot, setLastVoiceSnapshot] = useState(null);
  const [selectedSessionId, setSelectedSessionId] = useState('');
  const [voiceGuideOpen, setVoiceGuideOpen] = useState(false);

  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const recognitionRef = useRef(null);
  const transcriptRef = useRef('');
  const autoSavedSlateRef = useRef(null);
  const lastAnalyzedTranscriptRef = useRef('');

  useEffect(() => {
    if (!user?.id) return;
    (async () => {
      try {
        const [settingsPayload, sessionsPayload, googlePayload] = await Promise.all([
          fetchIntakeSettings(),
          fetchIntakeSessions(),
          fetchGooglePhotosStatus(),
        ]);
        setSettings(settingsPayload || null);
        setSessions(sessionsPayload?.sessions || []);
        setGoogleStatus(googlePayload || null);
        setAutoDraftMode(Boolean(settingsPayload?.auto_draft_listing ?? true));
        const nextSession = sessionsPayload?.sessions?.[0]?.session_id || '';
        setSelectedSessionId(nextSession);
        setForm((current) => ({
          ...current,
          session_id: current.session_id || nextSession,
          item_prefix: settingsPayload?.default_item_prefix || 'SP',
          box_prefix: settingsPayload?.default_box_prefix || 'BX',
          location: current.location || settingsPayload?.default_location || '',
          label_copies: settingsPayload?.default_label_copies || 2,
        }));
        if (typeof window !== 'undefined') {
          window.localStorage.setItem(
            OFFLINE_SLATE_SETTINGS_KEY,
            JSON.stringify({
              session_id: nextSession,
              item_prefix: settingsPayload?.default_item_prefix || 'SP',
              box_prefix: settingsPayload?.default_box_prefix || 'BX',
              location: settingsPayload?.default_location || '',
              label_copies: settingsPayload?.default_label_copies || 2,
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
            label_copies: offlineDefaults.label_copies || current.label_copies,
          }));
          toast.error('Intake defaults could not be loaded from the server. Offline slate mode is available.');
          return;
        }
        toast.error(error.message || 'Failed to load intake slate defaults.');
      }
    })();
  }, [user?.id]);

  const sessionOptions = useMemo(() => sessions.map((session) => session.session_id), [sessions]);
  const activeSession = useMemo(() => sessions.find((session) => session.session_id === (form.session_id || selectedSessionId)) || sessions[0] || null, [form.session_id, selectedSessionId, sessions]);
  const currentSlate = slateResult?.slate || null;
  const renderedSlateUrl = slateResult?.rendered_slate_url || currentSlate?.metadata_json?.rendered_slate?.storage_path || null;
  const renderedLabelUrl = slateResult?.rendered_label_url || currentSlate?.metadata_json?.rendered_label?.storage_path || null;
  const bridgeUpload = slateResult?.bridge_upload || currentSlate?.metadata_json?.bridge_upload || null;
  const aiResultPayload = voiceAnalysis || null;
  const aiStructured = aiResultPayload?.structured_listing_json || aiResultPayload?.voice_intelligence?.structured_listing_json || null;
  const aiMeta = aiResultPayload?.ai_metadata || aiResultPayload?.voice_intelligence?.ai_metadata || {};
  const aiState = aiResultPayload?.intelligence_state || aiResultPayload?.voice_intelligence?.intelligence_state || {};
  const aiFieldSuggestions = {
    title: voiceAnalysis?.suggested_fields?.title || aiStructured?.canonical_listing?.master_title || '',
    brand: voiceAnalysis?.suggested_fields?.item_specifics?.Brand || voiceAnalysis?.suggested_fields?.brand || aiStructured?.identity?.brand || '',
    model: voiceAnalysis?.suggested_fields?.item_specifics?.Model || voiceAnalysis?.suggested_fields?.model || aiStructured?.identity?.model || '',
    price: voiceAnalysis?.suggested_fields?.price || aiStructured?.pricing?.price_hint || aiStructured?.pricing?.listing_price || '',
    condition: voiceAnalysis?.suggested_fields?.condition || aiStructured?.condition?.canonical_condition || '',
    quantity: String(voiceAnalysis?.suggested_fields?.quantity || aiStructured?.inventory?.listing_quantity || aiStructured?.inventory?.quantity_on_hand || ''),
  };
  const currentLabelPayload = useMemo(() => {
    const metadata = currentSlate?.metadata_json || {};
    const qrPayload = currentSlate?.qr_payload_json || {};
    return {
      ...qrPayload,
      display_item_number: metadata.display_item_number || qrPayload.display_item_number || '',
      display_box_number: metadata.display_box_number || qrPayload.display_box_number || '',
      title: currentSlate?.title || form.title || voiceAnalysis?.voice_intelligence?.title || '',
      location: currentSlate?.location || form.location || '',
      quantity: qrPayload.quantity || form.quantity || '1',
      label_copies: metadata.label_default_copies || form.label_copies || 2,
      boundary_position: qrPayload.boundary_position || form.boundary_position || 'start',
      session_id: currentSlate?.session_id || form.session_id || '',
      item_id: currentSlate?.item_id || form.item_id || '',
      box_id: currentSlate?.box_id || form.box_id || '',
    };
  }, [currentSlate, form, voiceAnalysis]);
  const sessionIntake = activeSession?.metadata_json?.intake || {};
  const googleConnected = Boolean(googleStatus?.connected || googleStatus?.connection_state === 'connected');
  const currentGroupState = currentSlate
    ? (currentSlate?.metadata_json?.voice_processing?.structured_json_received || currentSlate?.metadata_json?.voice?.intelligence?.quality?.ready_for_draft
      ? 'ready-to-draft'
      : 'collecting')
    : 'idle';

  const onChange = (key, value) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const updateSelectedSessionDefaults = (sessionId) => {
    const session = sessions.find((item) => item.session_id === sessionId);
    setSelectedSessionId(sessionId);
    setForm((current) => ({
      ...current,
      session_id: sessionId,
      location: current.location || session?.default_location || settings?.default_location || '',
      item_prefix: session?.item_prefix || current.item_prefix || settings?.default_item_prefix || 'SP',
      box_prefix: session?.box_prefix || current.box_prefix || settings?.default_box_prefix || 'BX',
    }));
  };

  const syncGoogleStatus = async () => {
    try {
      const payload = await fetchGooglePhotosStatus();
      setGoogleStatus(payload || null);
      toast.success(payload?.connected ? 'Google Photos connection looks healthy.' : 'Google Photos is not connected.');
    } catch (error) {
      toast.error(error.message || 'Failed to refresh Google Photos status.');
    }
  };

  const startGoogleLogin = async () => {
    try {
      const authPayload = await startGooglePhotosOAuth();
      if (authPayload?.auth_url) {
        window.location.assign(authPayload.auth_url);
        return;
      }
      throw new Error('Google Photos OAuth URL was not returned by the server.');
    } catch (error) {
      if (String(error?.message || '').toLowerCase().includes('missing google photos oauth client settings')) {
        window.location.assign('/settings/intake?google_photos=missing-config#google-photos-oauth');
        return;
      }
      toast.error(error.message || 'Unable to start Google login.');
    }
  };

  const refreshSessions = async () => {
    try {
      const payload = await fetchIntakeSessions();
      setSessions(payload?.sessions || []);
    } catch {
      // non-blocking
    }
  };

  const toggleAutoDraftMode = async () => {
    if (!settings) return;
    const next = !autoDraftMode;
    setAutoDraftSaving(true);
    try {
      const updated = await updateIntakeSettings({ auto_draft_listing: next });
      setSettings(updated || null);
      setAutoDraftMode(Boolean(updated?.auto_draft_listing ?? next));
      toast.success(next ? 'Auto drafting enabled.' : 'Capture only mode enabled.');
    } catch (error) {
      toast.error(error.message || 'Failed to update auto drafting.');
    } finally {
      setAutoDraftSaving(false);
    }
  };

  const syncNow = async () => {
    setSyncQueued(true);
    try {
      const payload = await runIntakeMonitor();
      toast.success(payload?.result?.message || 'Intake sync queued.');
      await syncGoogleStatus();
    } catch (error) {
      toast.error(error.message || 'Failed to queue intake sync.');
    } finally {
      setSyncQueued(false);
    }
  };

  const ensureVoiceEnrichment = async () => {
    const transcript = String(voiceTranscript || '').trim();
    if (!transcript) return null;
    if (lastAnalyzedTranscriptRef.current === transcript && voiceAnalysis) {
      return voiceAnalysis;
    }
    const payload = await analyzeVoice(transcript);
    if (payload) lastAnalyzedTranscriptRef.current = transcript;
    return payload;
  };

  const applyVoiceAnalysis = (analysis) => {
    const suggested = analysis?.suggested_fields || {};
    setLastVoiceSnapshot({
      form,
      voiceTranscript,
      voiceAudioDataUrl,
      voiceAnalysis,
    });
    setVoiceAnalysis(analysis || null);
    setForm((current) => ({
      ...current,
      title: shouldReplaceVoiceField(current.title, suggested.title) ? suggested.title || current.title : current.title,
      brand: shouldReplaceVoiceField(current.brand, suggested.item_specifics?.Brand || suggested.brand) ? (suggested.item_specifics?.Brand || suggested.brand || current.brand) : current.brand,
      model: shouldReplaceVoiceField(current.model, suggested.item_specifics?.Model || suggested.model) ? (suggested.item_specifics?.Model || suggested.model || current.model) : current.model,
      price: shouldReplaceVoiceField(current.price, suggested.price || suggested.suggested_price || analysis?.structured_listing_json?.pricing?.price_hint)
        ? String(suggested.price || suggested.suggested_price || analysis?.structured_listing_json?.pricing?.price_hint || current.price || '')
        : current.price,
      condition: shouldReplaceVoiceField(current.condition, suggested.condition) ? suggested.condition || current.condition : current.condition,
      notes: shouldReplaceVoiceField(current.notes, suggested.notes || suggested.photo_notes?.join('; ')) ? (suggested.notes || suggested.photo_notes?.join('; ') || current.notes) : current.notes,
      quantity: shouldReplaceVoiceField(current.quantity, suggested.item_specifics?.Quantity || suggested.quantity) ? String(suggested.quantity || suggested.item_specifics?.Quantity || current.quantity || '1') : current.quantity,
      internal_notes: suggested.research_queries?.length ? `${current.internal_notes || ''}\n${suggested.research_queries.join(' · ')}`.trim() : current.internal_notes,
    }));
  };

  const applyVoiceCommands = (transcript) => {
    const parsed = parseVoiceCommands(transcript);
    const fields = parsed.fields || {};
    if (Object.keys(fields).length) {
      setForm((current) => ({
        ...current,
        quantity: fields.quantity || current.quantity,
        location: fields.location || current.location,
        brand: fields.brand || current.brand,
        model: fields.model || current.model,
        title: fields.title || current.title,
        price: fields.price || current.price,
        weight: fields.weight || current.weight,
        condition: fields.condition || current.condition,
      }));
    }
    return parsed;
  };

  const analyzeVoice = async (overrideTranscript = null) => {
    const transcript = String(overrideTranscript ?? voiceTranscript ?? '').trim();
    if (!transcript) {
      toast.error('Record or enter a transcript first.');
      return null;
    }
    setVoiceWorking(true);
    try {
      const payload = await analyzeVoiceIntake({
        transcript,
        notes: form.notes || '',
        current_form: form,
        current_session: activeSession || null,
      });
      applyVoiceAnalysis(payload || null);
      lastAnalyzedTranscriptRef.current = transcript;
      toast.success('Voice intelligence applied.');
      return payload;
    } catch (error) {
      toast.error(error.message || 'Voice analysis failed.');
      return null;
    } finally {
      setVoiceWorking(false);
    }
  };

  const transcribeVoice = async (audioDataUrl, fallbackTranscript = '') => {
    if (!audioDataUrl) return String(fallbackTranscript || '').trim();
    setVoiceTranscribing(true);
    try {
      const payload = await transcribeVoiceIntake({
        voice_audio_data_url: audioDataUrl,
        voice_notes: fallbackTranscript || null,
      });
      const transcript = String(payload?.transcript || fallbackTranscript || '').trim();
      if (transcript) {
        setVoiceTranscript(transcript);
        transcriptRef.current = transcript;
        applyVoiceCommands(transcript);
      }
      if (payload?.error) {
        setVoiceError(payload.error);
      }
      return transcript;
    } catch (error) {
      setVoiceError(error.message || 'Voice transcription failed.');
      return String(fallbackTranscript || '').trim();
    } finally {
      setVoiceTranscribing(false);
    }
  };

  const startVoiceNote = async () => {
    if (voiceRecording) return;
    // Open the guidance overlay immediately so the operator can follow the
    // checklist while speaking; transcript updates tick topics live.
    setVoiceGuideOpen(true);
    setVoiceError('');
    setVoiceTranscript('');
    transcriptRef.current = '';
    setVoiceAudioDataUrl('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        const dataUrl = await toDataUrl(blob);
        setVoiceAudioDataUrl(dataUrl);
        setVoiceRecording(false);
        const finalTranscript = await transcribeVoice(dataUrl, String(transcriptRef.current || voiceTranscript || '').trim());
        if (finalTranscript) {
          const parsed = applyVoiceCommands(finalTranscript);
          await analyzeVoice(finalTranscript);
          if (parsed?.action === 'generate_tail') {
            await generateTailSlate();
          } else if (parsed?.action === 'generate_head') {
            await generateHeadSlate();
          } else if (parsed?.action === 'next_item') {
            await nextItem();
          } else if (parsed?.action === 'write_on_box') {
            await writeOnBox();
          } else if (parsed?.action === 'print_label') {
            await printLabel();
          } else if (parsed?.action === 'undo_last_change') {
            undoLastVoiceChange();
          }
        }
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((track) => track.stop());
          streamRef.current = null;
        }
      };
      recorder.start();
      setVoiceRecording(true);
      setVoiceGuideOpen(true);
      const Recognition = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition);
      if (Recognition) {
        const recognition = new Recognition();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = 'en-US';
        recognition.onresult = (event) => {
          let transcript = '';
          for (let index = 0; index < event.results.length; index += 1) {
            const result = event.results[index];
            if (result?.[0]?.transcript) transcript += `${result[0].transcript} `;
          }
          transcriptRef.current = transcript.trim();
          setVoiceTranscript(transcript.trim());
        };
        recognition.onerror = (event) => {
          setVoiceError(event.error || 'speech recognition error');
        };
        recognitionRef.current = recognition;
        recognition.start();
      }
      toast.success('Voice note recording started.');
    } catch (error) {
      setVoiceError(error.message || 'Unable to start microphone.');
      toast.error(error.message || 'Unable to start microphone.');
    }
  };

  const stopVoiceNote = async () => {
    if (!voiceRecording) return;
    try {
      recognitionRef.current?.stop?.();
    } catch {
      // ignore
    }
    try {
      recorderRef.current?.stop?.();
    } catch {
      setVoiceRecording(false);
    }
  };

  const undoLastVoiceChange = () => {
    if (!lastVoiceSnapshot) {
      toast.error('Nothing to undo.');
      return;
    }
    setForm(lastVoiceSnapshot.form || DEFAULT_FORM);
    setVoiceTranscript(lastVoiceSnapshot.voiceTranscript || '');
    setVoiceAudioDataUrl(lastVoiceSnapshot.voiceAudioDataUrl || '');
    setVoiceAnalysis(lastVoiceSnapshot.voiceAnalysis || null);
    lastAnalyzedTranscriptRef.current = String(lastVoiceSnapshot.voiceTranscript || '').trim();
    toast.success('Voice changes restored.');
  };

  const createSlate = async (boundaryPosition = form.boundary_position) => {
    setSaving(true);
    try {
      await ensureVoiceEnrichment();
      const durableNotes = [form.notes, voiceTranscript ? `Voice note: ${voiceTranscript}` : ''].filter(Boolean).join('\n\n');
      const payload = await createIntakeSlate({
        ...form,
        boundary_position: boundaryPosition,
        voice_transcript: voiceTranscript || null,
        notes: durableNotes,
        voice_notes: voiceAnalysis?.voice_intelligence?.voice_notes || voiceTranscript || null,
        voice_audio_data_url: voiceAudioDataUrl || null,
        voice_intelligence: voiceAnalysis?.voice_intelligence || null,
        quantity: form.quantity || '1',
      });
      setSlateResult(payload || null);
      setSlateSuccessOpen(true);
      setOfflineMode(false);
      autoSavedSlateRef.current = null;
      setVoiceTranscript('');
      setVoiceAudioDataUrl('');
      setVoiceAnalysis(null);
      setLastVoiceSnapshot(null);
      lastAnalyzedTranscriptRef.current = '';
      toast.success(`${boundaryPosition === 'tail' ? 'Tail' : 'Head'} slate created for ${payload?.slate?.item_id || 'item'}.`);
      if (typeof window !== 'undefined') {
        const renderedSlateAsset = payload?.rendered_slate_url || payload?.rendered_slate_data_url || payload?.slate?.metadata_json?.rendered_slate?.storage_path || payload?.slate?.metadata_json?.rendered_slate?.data_url || null;
        const mobileLikely = window.matchMedia?.('(pointer: coarse)').matches || window.innerWidth < 900 || /iphone|ipad|android|mobile/i.test(navigator.userAgent || '');
        const slateKey = String(payload?.slate?.id || payload?.slate?.item_id || renderedSlateAsset || '');
        if (mobileLikely && renderedSlateAsset && autoSavedSlateRef.current !== slateKey) {
          autoSavedSlateRef.current = slateKey;
          try {
            const response = await fetch(renderedSlateAsset, { credentials: 'include' });
            const blob = await response.blob();
            const fileName = `${payload?.slate?.item_id || 'posterpro-slate'}.png`;
            const file = new File([blob], fileName, { type: blob.type || 'image/png' });
            if (navigator.share && (!navigator.canShare || navigator.canShare({ files: [file] }))) {
              await navigator.share({
                files: [file],
                title: 'PosterPro slate',
                text: 'Save this rendered PosterPro slate to Photos / camera roll.',
              });
            } else {
              const anchor = document.createElement('a');
              anchor.href = renderedSlateAsset;
              anchor.download = fileName;
              anchor.rel = 'noreferrer';
              document.body.appendChild(anchor);
              anchor.click();
              anchor.remove();
            }
          } catch {
            // Best-effort only; the visible download controls remain available.
          }
        }
      }
      await syncGoogleStatus();
      await refreshSessions();
      return payload;
    } catch (error) {
      toast.error(error.message || 'Failed to create slate. Offline slate is available if needed.');
      return null;
    } finally {
      setSaving(false);
    }
  };

  const generateHeadSlate = async () => createSlate('start');
  const generateTailSlate = async () => createSlate('tail');

  const nextItem = async () => {
    const currentUploadState = String(
      slateResult?.bridge_upload?.status ||
        slateResult?.slate?.metadata_json?.bridge_upload?.status ||
        slateResult?.slate?.metadata_json?.google_photos?.last_upload_status ||
        ''
    ).toLowerCase();
    if (slateResult && !currentUploadState.includes('uploaded') && !currentUploadState.includes('success') && !currentUploadState.includes('completed')) {
      try {
        await retryBridgeUpload();
      } catch {
        // continue; intake must not stall
      }
    }
    setSlateSuccessOpen(false);
    setForm((current) => ({
      ...current,
      item_id: '',
      box_id: '',
      title: '',
      brand: '',
      model: '',
      condition: '',
      quantity: '1',
      notes: '',
      flaws: '',
      weight: '',
      length: '',
      width: '',
      height: '',
      packed: false,
      mark_packed: false,
      internal_notes: '',
      boundary_position: 'start',
      same_box: false,
      increment_box: true,
    }));
    await refreshSessions();
  };

  const retryCurrentSlateUpload = async () => {
    await retryBridgeUpload();
  };

  const retryBridgeUpload = async () => {
    const slateId = currentSlate?.id;
    if (!slateId) {
      toast.error('Create or load a slate first.');
      return;
    }
    setSaving(true);
    try {
      const payload = await retryIntakeSlateBridgeUpload(slateId);
      setSlateResult((current) => ({
        ...(current || {}),
        ...(payload || {}),
        bridge_upload: payload?.bridge_upload || current?.bridge_upload || null,
      }));
      toast.success(`Rendered slate re-queued for ${payload?.slate?.item_id || 'upload'}.`);
      await syncGoogleStatus();
    } catch (error) {
      toast.error(error.message || 'Failed to re-upload the rendered slate.');
    } finally {
      setSaving(false);
    }
  };

  const printLabel = async () => {
    if (!currentSlate?.id) {
      toast.error('Save a slate first.');
      return;
    }
    try {
      await printIntakeLabel(currentSlate.id);
      toast.success('Label print request recorded.');
      await syncGoogleStatus();
    } catch (error) {
      toast.error(error.message || 'Failed to queue label print.');
    }
  };

  const writeOnBox = async () => {
    if (!currentSlate?.id) {
      toast.error('Save a slate first.');
      return;
    }
    try {
      await markIntakeLabelWrittenOnBox(currentSlate.id);
      toast.success('Marked as written on box.');
    } catch (error) {
      toast.error(error.message || 'Failed to record box marking.');
    }
  };

  const resetVoice = () => {
    setVoiceTranscript('');
    setVoiceAudioDataUrl('');
    setVoiceAnalysis(null);
    setVoiceError('');
    setLastVoiceSnapshot(null);
    transcriptRef.current = '';
    lastAnalyzedTranscriptRef.current = '';
  };

  const currentSuccessPayload = slateResult || null;
  const currentAIResult = aiResultPayload || null;

  const saveOfflineSlate = () => {
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
    const payload = {
      type: 'posterpro_head_slate',
      version: 1,
      session_id: form.session_id?.trim() || settings?.default_session_naming_pattern || 'offline-session',
      item_id: form.item_id?.trim() || `${(form.item_prefix || 'SP').toUpperCase()}-${dateToken}-${String(nextCounters.item).padStart(4, '0')}`,
      box_id: form.box_id?.trim() || `${(form.box_prefix || 'BX').toUpperCase()}-${String(nextCounters.box).padStart(4, '0')}`,
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
      quantity: form.quantity?.trim() || '1',
      packed: Boolean(form.mark_packed || form.packed),
      boundary_position: form.boundary_position || 'start',
      created_at: now.toISOString(),
      display_item_number: nextCounters.item,
      display_box_number: nextCounters.box,
    };
    setOfflineMode(true);
    setSlateResult({
      slate: {
        item_id: payload.item_id,
        session_id: payload.session_id,
        box_id: payload.box_id,
        location: payload.location,
        title: payload.title,
        condition: payload.condition,
        metadata_json: {
          display_item_number: nextCounters.item,
          display_box_number: nextCounters.box,
          label_default_copies: Number(form.label_copies || 2),
          label_print_status: 'pending',
        },
        qr_payload_json: payload,
      },
      qr_payload: payload,
      qr_data_url: null,
      rendered_slate_url: null,
      rendered_label_url: null,
      local_only: true,
    });
    toast.success(`Offline head slate prepared for ${payload.item_id}.`);
  };

  const slate = currentSlate;
  const qrPayload = slateResult?.qr_payload || slate?.qr_payload_json || null;
  const googleAlbumLabel = googleStatus?.album_url || settings?.album_url || settings?.folder_id || 'Not configured';

  return (
    <AppShell active="/intake" title="Intake Slate" contentWidth="wide">
      <Head>
        <link rel="manifest" href="/manifest.webmanifest" />
        <meta name="theme-color" content="#ffffff" />
      </Head>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Intake Slate"
          title="Voice-first field intake"
          description="Speak the details, capture a boundary slate, upload the marker to Google Photos, and keep the label and transcript attached to the item record."
          actions={(
            <>
              <Button onClick={voiceRecording ? stopVoiceNote : startVoiceNote} variant={voiceRecording ? 'secondary' : 'primary'}>
                {voiceRecording ? <MicOff size={16} /> : <Mic size={16} />}
                {voiceRecording ? 'Stop voice note' : 'Start voice note'}
              </Button>
              <Button onClick={() => setVoiceGuideOpen(true)} variant="outline">
                <Sparkles size={16} />
                Voice tips
              </Button>
              <Button onClick={() => analyzeVoice()} variant="secondary" disabled={voiceWorking || !voiceTranscript.trim()}>
                <PencilLine size={16} />
                {voiceWorking ? 'Analyzing…' : 'Analyze voice'}
              </Button>
              <Button onClick={() => setAiInspectorOpen(true)} variant="outline" disabled={!currentAIResult}>
                <FileJson2 size={16} />
                View AI result
              </Button>
              <Button onClick={toggleAutoDraftMode} variant={autoDraftMode ? 'success' : 'outline'} disabled={autoDraftSaving}>
                <Sparkles size={16} />
                {autoDraftSaving ? 'Updating…' : autoDraftMode ? 'Auto draft on' : 'Capture only'}
              </Button>
              <Button onClick={generateHeadSlate} disabled={saving}><Save size={16} /> {saving ? 'Saving…' : 'Generate head slate'}</Button>
              <Button onClick={generateTailSlate} variant="secondary" disabled={saving}>Generate tail slate</Button>
              <Button onClick={nextItem} variant="secondary" disabled={saving}>Next item</Button>
              <Button onClick={undoLastVoiceChange} variant="outline"><Undo2 size={16} /> Undo last voice change</Button>
            </>
          )}
        />

        {googleConnected ? null : (
          <div className="rounded-[24px] border border-red-200 bg-red-50 p-4 text-red-900">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold uppercase tracking-[0.16em]">Google Photos not connected</p>
                <p className="mt-1 text-sm text-red-800">PosterPro can still generate the slate, but it cannot auto-upload until the Google Photos album is connected.</p>
              </div>
              <Button onClick={startGoogleLogin} variant="secondary"><CloudUpload size={16} /> Connect Google Photos</Button>
            </div>
            <p className="mt-3 text-sm text-red-800">Use this now if you need automatic upload. If you skip it, the slate still downloads locally and can be uploaded manually later.</p>
          </div>
        )}

        <AiInspectorModal
          open={aiInspectorOpen && Boolean(currentAIResult)}
          onClose={() => setAiInspectorOpen(false)}
          payload={currentAIResult}
        />
        <VoicePromptModal
          open={voiceGuideOpen}
          onClose={() => setVoiceGuideOpen(false)}
          transcript={voiceTranscript}
          form={form}
          analysis={voiceAnalysis}
        />
        <SlateSuccessModal
          open={slateSuccessOpen && Boolean(currentSuccessPayload)}
          onClose={() => setSlateSuccessOpen(false)}
          onNext={nextItem}
          onWriteOnBox={writeOnBox}
          onPrint={printLabel}
          onRetryUpload={retryCurrentSlateUpload}
          payload={currentSuccessPayload}
        />

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <div className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--pp-muted)]">Session</p>
            <p className="mt-1 text-2xl font-semibold text-[var(--pp-text)]">{form.session_id || selectedSessionId || '—'}</p>
            <p className="mt-2 text-sm text-[var(--pp-muted)]">Location: {form.location || activeSession?.default_location || settings?.default_location || 'Not set'}</p>
          </div>
          <div className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--pp-muted)]">Google Photos</p>
            <p className="mt-1 text-2xl font-semibold text-[var(--pp-text)]">{googleConnected ? 'Connected' : 'Not connected'}</p>
            <p className="mt-2 text-sm text-[var(--pp-muted)] break-all">{googleAlbumLabel}</p>
          </div>
          <div className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--pp-muted)]">Voice</p>
            <p className="mt-1 text-2xl font-semibold text-[var(--pp-text)]">{voiceRecording ? 'Recording' : voiceTranscript ? 'Captured' : 'Idle'}</p>
            <p className="mt-2 text-sm text-[var(--pp-muted)]">{voiceError || (voiceTranscribing ? 'Transcribing…' : (voiceAnalysis?.voice_intelligence?.generation_source ? `AI: ${voiceAnalysis.voice_intelligence.generation_source}` : 'Ready for a voice note'))}</p>
          </div>
          <div className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--pp-muted)]">AI / Drafting</p>
            <p className="mt-1 text-2xl font-semibold text-[var(--pp-text)]">{settings?.drafting_paused ? 'Drafting paused' : (autoDraftMode ? 'Auto draft' : 'Capture only')}</p>
            <p className="mt-2 text-sm text-[var(--pp-muted)]">
              {aiState.fields_populated ? 'Fields populated' : aiState.enrichment_sent ? 'AI enrichment returned' : 'Awaiting analysis'}
            </p>
            <p className="mt-2 text-xs text-[var(--pp-muted)]">Group: {currentGroupState}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <StatusPill status={settings?.drafting_paused ? 'warning' : 'success'} label={settings?.drafting_paused ? 'Drafting paused' : 'Drafting active'} />
              <StatusPill status={autoDraftMode ? 'success' : 'default'} label={autoDraftMode ? 'Auto draft on' : 'Capture only'} />
            </div>
            <div className="mt-3 grid gap-2">
              <SectionStateRow label="Transcript" value={aiState.transcript_sent ? '✓' : '—'} status={aiState.transcript_sent ? 'success' : 'default'} />
              <SectionStateRow label="JSON" value={aiState.structured_json_received ? '✓' : '—'} status={aiState.structured_json_received ? 'success' : 'default'} />
            </div>
          </div>
          <div className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--pp-muted)]">Captured</p>
            <p className="mt-1 text-2xl font-semibold text-[var(--pp-text)]">{slate?.metadata_json?.display_item_number || slate?.item_id || '—'}</p>
            <p className="mt-2 text-sm text-[var(--pp-muted)]">Box {slate?.metadata_json?.display_box_number || slate?.box_id || '—'}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {renderedSlateUrl ? (
                <Button href={renderedSlateUrl} variant="outline" target="_blank" rel="noreferrer">View rendered slate</Button>
              ) : (
                <Button variant="outline" disabled>View rendered slate</Button>
              )}
              {renderedSlateUrl ? (
                <Button href={renderedSlateUrl} download variant="outline"><Download size={16} /> Download slate</Button>
              ) : (
                <Button variant="outline" disabled><Download size={16} /> Download slate</Button>
              )}
            </div>
          </div>
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(320px,0.92fr)_minmax(0,1.16fr)_minmax(320px,0.92fr)]">
          <SectionPanel title="Voice-first intake" description="Record the item in your own words. PosterPro keeps the audio, the transcript, and the AI interpretation attached to the item.">
            <div className="grid gap-4">
              <div className="flex flex-wrap gap-2">
                <Button onClick={voiceRecording ? stopVoiceNote : startVoiceNote} variant={voiceRecording ? 'secondary' : 'primary'}>
                  {voiceRecording ? <MicOff size={16} /> : <Mic size={16} />}
                  {voiceRecording ? 'Stop voice note' : 'Start voice note'}
                </Button>
                <Button onClick={() => setVoiceGuideOpen(true)} variant="outline">Voice tips</Button>
                <Button onClick={resetVoice} variant="outline">Clear voice note</Button>
                <Button onClick={syncNow} variant="secondary" disabled={syncQueued}><RefreshCcw size={16} /> {syncQueued ? 'Queueing sync…' : 'Sync now'}</Button>
                <Button onClick={syncGoogleStatus} variant="outline"><RefreshCcw size={16} /> Google sync / status</Button>
              </div>
              <Field label="Transcript" hint="Automatic speech recognition if available, or paste the transcript manually.">
                <textarea
                  className="min-h-[160px] rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-sm text-[var(--pp-text)]"
                  value={voiceTranscript}
                  onChange={(event) => {
                    transcriptRef.current = event.target.value;
                    setVoiceTranscript(event.target.value);
                  }}
                  placeholder="Speak naturally: brand, model, condition, quantity, pricing notes, marketplace targets, and anything visible in the photos."
                />
              </Field>
              <Field label="AI intent / research notes" hint="PosterPro keeps the structured interpretation and lets you edit the result before saving the slate.">
                <textarea
                  className="min-h-[120px] rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-sm text-[var(--pp-text)]"
                  value={aiStructured?.canonical_listing?.master_description || voiceAnalysis?.voice_intelligence?.description || voiceAnalysis?.suggested_fields?.notes || ''}
                  readOnly
                  placeholder="AI suggestions will appear here after analysis."
                />
              </Field>
              {voiceAnalysis ? (
                <div className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 text-sm text-[var(--pp-muted)]">
                  <div className="flex items-center justify-between gap-3">
                    <p className="font-semibold text-[var(--pp-text)]">Voice interpretation</p>
                    <Button onClick={() => setAiInspectorOpen(true)} variant="outline" size="sm" disabled={!currentAIResult}><ChevronDown size={16} /> Details</Button>
                  </div>
                  <div className="mt-2 grid gap-2">
                    <p><span className="font-semibold">Title:</span> {voiceAnalysis.suggested_fields?.title || '—'}</p>
                    <p><span className="font-semibold">Category:</span> {voiceAnalysis.suggested_fields?.category_suggestion || '—'}</p>
                    <p><span className="font-semibold">Condition:</span> {voiceAnalysis.suggested_fields?.condition || '—'}</p>
                    <p><span className="font-semibold">Confidence:</span> {voiceAnalysis.confidence ?? '—'}</p>
                    <p><span className="font-semibold">Targets:</span> {(voiceAnalysis.marketplace_targets || []).join(', ') || '—'}</p>
                  </div>
                </div>
              ) : null}
              <div className="grid gap-3 rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-semibold text-[var(--pp-text)]">Google connection</span>
                  <StatusPill status={googleConnected ? 'success' : 'warning'} label={googleConnected ? 'Connected' : 'Not connected'} />
                </div>
                <p className="text-sm text-[var(--pp-muted)]">Album: {googleAlbumLabel}</p>
                <p className="text-sm text-[var(--pp-muted)]">Album ID: {googleStatus?.album_id || '—'}</p>
                <p className="text-sm text-[var(--pp-muted)]">Auth: {googleStatus?.connection_state || 'unknown'}</p>
                <p className="text-sm text-[var(--pp-muted)]">Last sync: {googleStatus?.last_synced_at || '—'}</p>
                <p className="text-sm text-[var(--pp-muted)]">Last upload: {googleStatus?.last_upload_status || '—'}</p>
                <div className="flex flex-wrap gap-2">
                  <Button onClick={startGoogleLogin} variant="secondary"><CloudUpload size={16} /> {googleConnected ? 'Reconnect Google Photos' : 'Connect Google Photos'}</Button>
                  <Button onClick={syncGoogleStatus} variant="outline">Test connection</Button>
                </div>
                {googleStatus?.last_error ? <p className="text-xs text-red-600">Last error: {googleStatus.last_error}</p> : null}
              </div>
              <GooglePhotosConnectionGuide
                connected={googleConnected}
                accountLabel={googleStatus?.account_label}
                albumLabel={googleAlbumLabel}
                albumId={googleStatus?.album_id}
                connectionState={googleStatus?.connection_state}
                redirectUri={googleStatus?.redirect_uri || googlePhotosRedirectUri}
                connectUrl={googlePhotosConnectUrl}
                apiKeysUrl="/settings/intake?google_photos=missing-config#google-photos-oauth"
                slateUrl="/intake/slate"
                onRefresh={syncGoogleStatus}
                onOpenGooglePhotos={() => window.location.assign('/settings/intake?google_photos=missing-config#google-photos-oauth')}
                missingConfig={googleStatus?.connection_state === 'missing_config'}
                compact
              />
              {offlineMode ? <StatusPill status="warning" label="Offline slate mode active" /> : null}
            </div>
          </SectionPanel>

          <SectionPanel
            title="Live slate and label"
            description="Use the generated slate image as the boundary marker, then keep the item and box numbers visible on the laptop while writing on the box."
            action={<Button onClick={() => setFullscreen((current) => !current)} variant="secondary"><Expand size={16} /> {fullscreen ? 'Exit fullscreen' : 'Fullscreen slate'}</Button>}
          >
            <div className={fullscreen ? 'fixed inset-0 z-[100] overflow-auto bg-white p-6 text-black' : 'rounded-[28px] border border-[var(--pp-border)] bg-white p-6 text-black'}>
              <div className="mx-auto grid max-w-[1080px] gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
                <div>
                  <p className="text-[12px] font-semibold uppercase tracking-[0.22em] text-black/70">PosterPro Slate</p>
                  <h2 className="mt-4 text-[2.4rem] font-bold tracking-[-0.05em] text-black">{slate?.metadata_json?.display_item_number || 'Create a slate to preview'}</h2>
                  <div className="mt-4 grid gap-3 text-lg md:grid-cols-2">
                    <p><span className="font-semibold">ITEM:</span> {slate?.metadata_json?.display_item_number || '—'}</p>
                    <p><span className="font-semibold">BOX:</span> {slate?.metadata_json?.display_box_number || '—'}</p>
                    <p><span className="font-semibold">SESSION:</span> {slate?.session_id || form.session_id || '—'}</p>
                    <p><span className="font-semibold">LOCATION:</span> {slate?.location || form.location || '—'}</p>
                    <p className="md:col-span-2"><span className="font-semibold">TITLE:</span> {slate?.title || form.title || 'PENDING IDENTIFICATION'}</p>
                  </div>
                  <div className="mt-5 grid gap-2 text-sm text-black/80">
                    <p>Boundary: {qrPayload?.boundary_position === 'tail' ? 'Tail slate' : 'Head slate'}</p>
                    <p>Date: {(qrPayload?.created_at || '').replace('T', ' ').replace(/:[0-9]{2}(?:\.[0-9]+)?(?:[+-].*)?$/, '') || '—'}</p>
                    <p>Voice note: {voiceTranscript ? 'Captured' : 'None yet'}</p>
                    <p>Label copies: {slate?.metadata_json?.label_default_copies || form.label_copies || 2}</p>
                  </div>
                  <div className="mt-5 space-y-3 text-sm leading-7 text-black/80">
                    <p>This image is internal only. It marks the start of a new intake item and is excluded from public marketplace photos by default.</p>
                    <p>After photographing this slate, take the product photos, flaw photos, label photos, measurements, and packed-box photos. PosterPro will assign every following photo to this item until the next slate appears.</p>
                    {slateResult?.local_only ? <p className="font-semibold">Offline preview only: this slate is cached client-side and will not exist server-side until you create it online.</p> : null}
                    {renderedSlateUrl ? <p><a className="font-semibold text-[var(--pp-link)] underline" href={renderedSlateUrl} target="_blank" rel="noreferrer">Open the rendered slate image</a></p> : null}
                    {bridgeUpload ? (
                      <div className="rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-xs text-[var(--pp-muted)]">
                        <p className="font-semibold text-[var(--pp-text)]">Google Photos bridge upload</p>
                        <p className="mt-1">Status: {bridgeUpload.status || 'unknown'}</p>
                        {bridgeUpload.target_album_url ? <p className="mt-1 break-all">Target album: {bridgeUpload.target_album_url}</p> : null}
                        {bridgeUpload.error ? <p className="mt-1 text-red-600">Error: {bridgeUpload.error}</p> : null}
                      </div>
                    ) : null}
                  </div>
                </div>
                <div className="grid gap-4">
                  <div className="rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 text-sm text-[var(--pp-muted)]">
                    <p className="font-semibold text-[var(--pp-text)]">Label actions</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button onClick={printLabel} variant="secondary" disabled={!slate?.id}><Printer size={16} /> Print label</Button>
                      <Button onClick={retryBridgeUpload} variant="secondary" disabled={saving || !slate?.id}><RefreshCcw size={16} /> Re-upload slate</Button>
                      <Button onClick={writeOnBox} variant="outline" disabled={!slate?.id}><PencilLine size={16} /> Write on box</Button>
                    </div>
                    <div className="mt-4 rounded-[18px] border border-dashed border-[var(--pp-border)] bg-white p-3 text-xs text-[var(--pp-muted)]">
                      <p className="font-semibold text-[var(--pp-text)]">Copies</p>
                      <p className="mt-1">Default label copies: {slate?.metadata_json?.label_default_copies || form.label_copies || 2}</p>
                      <p className="mt-1">Physical printing is deferred until a printer profile is configured. The digital label is always saved.</p>
                    </div>
                  </div>
                  <LabelPreview payload={currentLabelPayload} renderedDataUrl={renderedLabelUrl} />
                </div>
              </div>
            </div>
          </SectionPanel>

          <SectionPanel title="Field form" description="Everything you know now can be entered before the slate is created. Voice and OCR suggestions fill the blanks, but you can edit everything.">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Session ID" hint="Select or type the storage run.">
                <Input list="intake-session-options" value={form.session_id} onChange={(event) => updateSelectedSessionDefaults(event.target.value)} placeholder={settings?.default_session_naming_pattern || '{date}-{location}'} />
                <datalist id="intake-session-options">
                  {sessionOptions.map((value) => <option key={value} value={value} />)}
                </datalist>
              </Field>
              <Field label="Storage location">
                <Input value={form.location} onChange={(event) => onChange('location', event.target.value)} placeholder="Public Storage - Unit 123" />
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
              <Field label="Quantity">
                <Input value={form.quantity} onChange={(event) => onChange('quantity', event.target.value)} placeholder="1" />
              </Field>
              <Field label="Label copies">
                <Input type="number" min="1" value={form.label_copies} onChange={(event) => onChange('label_copies', Number(event.target.value || 2))} />
              </Field>
              <Field label="Optional title / item name" badge={isAiPopulatedValue(form.title, aiFieldSuggestions.title) ? <StatusPill status="success" label="AI populated" /> : null}>
                <Input value={form.title} onChange={(event) => onChange('title', event.target.value)} placeholder="Ryobi 40V charger" />
              </Field>
              <Field label="Price" badge={isAiPopulatedValue(form.price, aiFieldSuggestions.price) ? <StatusPill status="success" label="AI populated" /> : null}>
                <Input value={form.price} onChange={(event) => onChange('price', event.target.value)} placeholder="$29.99" />
              </Field>
              <Field label="Condition" badge={isAiPopulatedValue(form.condition, aiFieldSuggestions.condition) ? <StatusPill status="success" label="AI populated" /> : null}>
                <Input value={form.condition} onChange={(event) => onChange('condition', event.target.value)} placeholder="Used" />
              </Field>
              <Field label="Boundary marker">
                <select className="rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-sm text-[var(--pp-text)]" value={form.boundary_position} onChange={(event) => onChange('boundary_position', event.target.value)}>
                  <option value="start">Start of item</option>
                  <option value="tail">Tail slate taken after photos</option>
                </select>
              </Field>
              <Field label="Brand" badge={isAiPopulatedValue(form.brand, aiFieldSuggestions.brand) ? <StatusPill status="success" label="AI populated" /> : null}>
                <Input value={form.brand} onChange={(event) => onChange('brand', event.target.value)} placeholder="Ryobi" />
              </Field>
              <Field label="Model" badge={isAiPopulatedValue(form.model, aiFieldSuggestions.model) ? <StatusPill status="success" label="AI populated" /> : null}>
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
                Use <strong>Tail slate taken after photos</strong> when you missed the opening slate and need PosterPro to recover the immediately preceding unassigned product photos into this item batch.
              </p>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button onClick={generateHeadSlate} disabled={saving}><Save size={16} /> Generate head slate</Button>
              <Button onClick={generateTailSlate} variant="secondary" disabled={saving}>Generate tail slate</Button>
              <Button onClick={saveOfflineSlate} variant="outline">Offline slate</Button>
              <Button href="/intake/queue" variant="outline"><FolderOpen size={16} /> Open queue</Button>
            </div>
          </SectionPanel>
        </div>
      </div>
    </AppShell>
  );
}
