import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import PageHeader from "../../components/ui/page-header";
import SectionPanel from "../../components/ui/section-panel";
import Button from "../../components/ui/button";
import StatusPill from "../../components/ui/status-pill";
import {
  fetchIntakeTimeline,
  createRetroactiveSlate,
  classifyTimelineAssets,
  resetTimelineClassifications,
  deleteTimelineAsset,
  setTimelinePrimary,
  toThumbnailImageUrl,
} from "../../lib/api";

const WIDTHS = [48, 64, 88, 120, 160];
const PAGE_SIZE = 500;

function classify(photo) {
  const metadata = photo?.metadata_json || {};
  const role = String(
    photo?.timeline_role || metadata.timeline_role || photo?.classification || metadata.classification || photo?.image_type || "PHOTO",
  ).toUpperCase();
  const source = photo?.classification_source || metadata.classification_source || "";
  const slate = Boolean(photo?.is_slate) || (source === "MANUAL_OPERATOR" && role !== "PHOTO") || ["HEAD", "TAIL", "SLATE"].includes(role);
  return {
    slate,
    role: role === "TAIL" ? "TAIL" : slate ? "HEAD" : "PHOTO",
    primary: Boolean(metadata.timeline_primary),
    source: metadata.timeline_primary_source || "",
  };
}

function timelineGroups(entries) {
  const groups = [];
  for (const entry of entries) {
    const photo = entry.photo || {};
    const id = String(entry.image_group_id || photo.image_group_id || `photo:${photo.id}`);
    let group = groups[groups.length - 1];
    if (!group || group.id !== id) {
      group = {
        id,
        index: Number(entry.image_group_index ?? photo.image_group_index ?? groups.length + 1),
        entries: [],
      };
      groups.push(group);
    }
    group.entries.push(entry);
  }
  return groups;
}

export default function IntakeTimeline() {
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [zoom, setZoom] = useState(2);
  const [filter, setFilter] = useState("ALL");
  const [counts, setCounts] = useState({ total: 0, photo_count: 0, slate_count: 0 });
  const scrollRef = useRef(null);

  const refresh = useCallback(async () => {
    const result = await fetchIntakeTimeline({ limit: PAGE_SIZE, offset: 0 });
    setItems(result?.items || []);
    setCounts({
      total: Number(result?.total || 0),
      photo_count: Number(result?.photo_count || 0),
      slate_count: Number(result?.slate_count || 0),
    });
  }, []);

  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem("posterpro.timeline.zoom"));
      if (Number.isFinite(saved)) setZoom(Math.max(0, Math.min(WIDTHS.length - 1, saved)));
    } catch { /* storage may be unavailable in private browsing */ }
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const changeZoom = (value) => {
    const left = scrollRef.current?.scrollLeft || 0;
    const next = Math.max(0, Math.min(WIDTHS.length - 1, value));
    setZoom(next);
    try { window.localStorage.setItem("posterpro.timeline.zoom", String(next)); } catch { /* best effort */ }
    window.requestAnimationFrame(() => {
      if (scrollRef.current) scrollRef.current.scrollLeft = left;
    });
  };

  const mutate = async (operation, successMessage) => {
    setBusy(true);
    setFeedback("");
    try {
      await operation();
      await refresh();
      setFeedback(successMessage);
    } catch (error) {
      setFeedback(error?.message || "Timeline update failed.");
    } finally {
      setBusy(false);
    }
  };

  const addSlate = async (after, before) => {
    setBusy(true);
    try {
      await createRetroactiveSlate({
        retroactive: true,
        after_photo_id: after?.photo?.id ?? null,
        before_photo_id: before?.photo?.id ?? null,
        title: "",
      });
      await refresh();
      setFeedback("Slate added at the selected timeline boundary.");
    } catch (error) {
      setFeedback(error?.message || "Could not add Slate.");
    } finally {
      setBusy(false);
    }
  };

  const toggleSelected = (id) => setSelectedIds((current) =>
    current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
  );

  const ordered = useMemo(() => items.filter((entry) => {
    const state = classify(entry.photo || {});
    return filter === "ALL"
      || (filter === "PHOTOS" && !state.slate)
      || (filter === "SLATES" && state.slate)
      || (filter === "HEAD" && state.role === "HEAD")
      || (filter === "TAIL" && state.role === "TAIL");
  }), [items, filter]);
  const groups = useMemo(() => timelineGroups(ordered), [ordered]);
  const loadedCounts = useMemo(() => items.reduce((result, entry) => {
    const state = classify(entry.photo || {});
    if (state.slate) result.slates += 1;
    else result.photos += 1;
    if ((entry.photo?.metadata_json || {}).slate_detection_result === "probable_slate_candidate") result.ambiguous += 1;
    return result;
  }, { photos: 0, slates: 0, ambiguous: 0 }), [items]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const result = await fetchIntakeTimeline({ limit: PAGE_SIZE, offset: items.length });
      setItems((current) => [...current, ...(result?.items || [])]);
      setCounts({ total: Number(result?.total || 0), photo_count: Number(result?.photo_count || 0), slate_count: Number(result?.slate_count || 0) });
    } catch (error) {
      setFeedback(error?.message || "Could not load more timeline items.");
    } finally {
      setLoadingMore(false);
    }
  };

  const remove = (entry) => {
    const photo = entry.photo || {};
    if (!window.confirm(`Soft-delete ${classify(photo).slate ? "this Slate marker" : "this image"} from the Timeline and listing media?`)) return;
    void mutate(() => deleteTimelineAsset(photo.id), "Timeline asset removed; neighboring chronology was preserved.");
  };

  const renderEntry = (entry, entryIndex, groupEntries, groupIndex) => {
    const photo = entry.photo || {};
    const state = classify(photo);
    const width = WIDTHS[zoom];
    const thumbnail = toThumbnailImageUrl(
      photo.thumbnail_url || photo.display_url || photo.downloaded_url || photo.local_path,
      width,
      width,
    );
    const slateId = photo.slate_id || photo.slate?.id || (String(photo.id || "").startsWith("slate-") ? String(photo.id).slice(6) : null);
    const hasNext = entryIndex < groupEntries.length - 1 || groupIndex < groups.length - 1;
    const nextEntry = entryIndex < groupEntries.length - 1
      ? groupEntries[entryIndex + 1]
      : groups[groupIndex + 1]?.entries?.[0];
    return (
      <div key={photo.id || `${entry.image_group_id}-${entryIndex}`} className="flex shrink-0 items-start gap-2">
        <div className="flex flex-col items-center">
          <input
            aria-label={`Select timeline asset ${photo.id}`}
            type="checkbox"
            checked={selectedIds.includes(photo.id)}
            onChange={() => toggleSelected(photo.id)}
            className="mb-1"
          />
          <button
            type="button"
            onClick={() => setSelected(entry)}
            style={{ width }}
            className={`rounded-xl border-2 p-1 text-left shadow-sm ${state.role === "TAIL" ? "border-fuchsia-300 bg-fuchsia-500 text-white" : state.slate ? "border-lime-400 bg-lime-300 text-slate-950" : "border-transparent bg-transparent text-inherit"} ${selected?.photo?.id === photo.id ? "ring-2 ring-blue-500" : ""}`}
          >
            <div className="relative aspect-square overflow-hidden rounded-lg bg-slate-100">
              {thumbnail ? (
                <img src={thumbnail} alt={photo.original_filename || `Timeline asset ${photo.id}`} loading="lazy" decoding="async" className="h-full w-full object-cover" />
              ) : (
                <span className="flex h-full items-center justify-center p-2 text-center text-[10px] text-slate-700">Image unavailable</span>
              )}
              {state.slate && <span className="absolute left-1 top-1 rounded bg-black/75 px-1.5 py-0.5 text-[10px] font-black text-white">{state.role} SLATE</span>}
              {state.primary && <span className="absolute bottom-1 left-1 rounded bg-amber-300 px-1.5 py-0.5 text-[10px] font-black text-black">PRIMARY</span>}
            </div>
            <p className="mt-1 max-w-full truncate text-[10px]">{photo.original_filename || `${state.slate ? "Slate" : "Photo"} ${photo.id}`}</p>
            <div className="flex flex-wrap items-center justify-between gap-1">
              <StatusPill status={state.slate ? "warning" : "default"} label={photo.group_photo_label || state.role} />
              {photo.photo_number && <span className="text-[9px]">Image {photo.photo_number}</span>}
            </div>
          </button>
          {state.slate && slateId && (
            <div className="mt-1 flex gap-1">
              <a href={`/intake/slate?slate_id=${slateId}`} className="rounded border border-lime-700 bg-lime-100 px-1 text-[9px] font-semibold text-lime-900">EDIT SLATE</a>
              <a href={`/intake/slate?slate_id=${slateId}#voice`} className="rounded border border-lime-700 bg-lime-100 px-1 text-[9px] text-lime-900">VOICE NOTE</a>
            </div>
          )}
          <div className="mt-1 flex max-w-[190px] flex-wrap justify-center gap-1">
            {!state.slate && (state.primary ? (
              <>
                <Button type="button" variant="outline" disabled className="px-1 py-0 text-[9px]">PRIMARY</Button>
                <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => setTimelinePrimary(photo.id, { clear: true }), "Automatic best-photo selection restored for this group.")} className="px-1 py-0 text-[9px]">CLEAR PRIMARY / USE AUTO</Button>
              </>
            ) : (
              <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => setTimelinePrimary(photo.id), "Manual primary selected and listing media reordered.")} className="px-1 py-0 text-[9px]">SET PRIMARY</Button>
            ))}
            {state.slate && <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets([photo.id], state.role === "TAIL" ? "HEAD" : "TAIL"), `Slate marked ${state.role === "TAIL" ? "HEAD" : "TAIL"}.`)} className="px-1 py-0 text-[9px]">{state.role === "TAIL" ? "HEAD SLATE" : "TAIL SLATE"}</Button>}
            {state.slate && <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets([photo.id], "PHOTO"), "Slate classification removed.")} className="px-1 py-0 text-[9px]">REMOVE SLATE</Button>}
            <Button type="button" variant="outline" disabled={busy} onClick={() => remove(entry)} className="px-1 py-0 text-[9px]">DELETE</Button>
          </div>
          {!state.slate && <div className="mt-1 flex gap-1"><Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets([photo.id], "HEAD"), "Marked Head Slate.")} className="px-1 py-0 text-[9px]">HEAD SLATE</Button><Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets([photo.id], "TAIL"), "Marked Tail Slate; preceding photos stay in this item group.")} className="px-1 py-0 text-[9px]">TAIL SLATE</Button></div>}
        </div>
        {hasNext && nextEntry && <button type="button" disabled={busy} onClick={() => void addSlate(entry, nextEntry)} className="mt-16 shrink-0 rounded-full border border-dashed border-blue-300 bg-white/80 px-2 py-1 text-xs text-blue-700">+ Add Slate</button>}
      </div>
    );
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="Photo Timeline" description="Capture chronology is authoritative. Head Slates identify photos after them; Tail Slates identify photos before them." actions={<Button variant="outline" onClick={() => void refresh()}>Refresh</Button>} />
        <SectionPanel title="Intake filmstrip" description="Canonical image groups are shown as contiguous alternating regions. Green marks a Head Slate; fuchsia marks a Tail Slate.">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <span>Zoom</span>
            <button type="button" aria-label="Zoom out" onClick={() => changeZoom(zoom - 1)}>−</button>
            <input aria-label="Timeline zoom" type="range" min="0" max={WIDTHS.length - 1} value={zoom} onChange={(event) => changeZoom(Number(event.target.value))} />
            <button type="button" aria-label="Zoom in" onClick={() => changeZoom(zoom + 1)}>+</button>
            <span className="text-xs text-slate-500">{WIDTHS[zoom]}px</span>
            <select aria-label="Timeline filter" value={filter} onChange={(event) => setFilter(event.target.value)} className="rounded border px-2 py-1 text-xs">
              <option value="ALL">All</option><option value="PHOTOS">Photos</option><option value="SLATES">Slates</option><option value="HEAD">Head Slates</option><option value="TAIL">Tail Slates</option>
            </select>
            <span className="text-xs text-slate-600">Total {counts.total} · Photos {counts.photo_count} · Slates {counts.slate_count} · Loaded {items.length} ({loadedCounts.ambiguous} possible slate candidates)</span>
            <Button type="button" variant="outline" disabled={busy} onClick={() => { if (window.confirm(`Reset historical classifications for loaded Timeline assets?`)) void mutate(() => resetTimelineClassifications({ scope: "photo_ids", photo_ids: items.map((entry) => entry.photo?.id).filter((id) => id && !String(id).startsWith("slate-")), preserve_modern: true }), "Loaded classifications reset."); }}>Reset classifications</Button>
            {feedback && <span role="status" className="text-xs text-emerald-700">{feedback}</span>}
          </div>
          {selectedIds.length > 0 && <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-white p-2 text-xs">
            <span>{selectedIds.length} selected</span>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "PHOTO"), "Selected assets marked as photos.").then(() => setSelectedIds([]))}>Mark selected photos</Button>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "HEAD"), "Selected assets marked as Head Slates.").then(() => setSelectedIds([]))}>Mark selected Head Slates</Button>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "TAIL"), "Selected assets marked as Tail Slates.").then(() => setSelectedIds([]))}>Mark selected Tail Slates</Button>
            <Button type="button" variant="outline" onClick={() => setSelectedIds([])}>Clear selection</Button>
          </div>}
          {loading ? <p className="text-sm text-slate-500">Loading timeline…</p> : (
            <div ref={scrollRef} className="overflow-x-auto pb-3">
              <div className="flex min-w-max items-stretch gap-3">
                {filter === "ALL" && items[0] && <button type="button" disabled={busy} onClick={() => void addSlate(null, items[0])} className="my-auto shrink-0 rounded-full border border-dashed border-blue-300 bg-white/80 px-3 py-2 text-xs text-blue-700">+ Add Slate at start</button>}
                {groups.map((group, groupIndex) => {
                  const background = group.index === 0 ? "bg-slate-100 text-slate-900" : group.index % 2 ? "bg-slate-800 text-white" : "bg-slate-200 text-slate-950";
                  return <section key={group.id} data-image-group-id={group.id} data-image-group-index={group.index} className={`flex shrink-0 flex-col rounded-2xl border border-slate-400/50 p-3 ${background}`}>
                    <div className="mb-2 flex items-center justify-between gap-3 text-[10px] font-semibold uppercase tracking-wide opacity-80"><span>{group.index ? `Image Group ${group.index}` : "Unassigned Slate"}</span><span>{group.entries.filter((entry) => !classify(entry.photo || {}).slate).length} photos</span></div>
                    <div className="flex min-w-min items-start gap-2">{group.entries.map((entry, index) => renderEntry(entry, index, group.entries, groupIndex))}</div>
                  </section>;
                })}
                {filter === "ALL" && items.length === counts.total && items.length > 0 && <button type="button" disabled={busy} onClick={() => void addSlate(items[items.length - 1], null)} className="my-auto shrink-0 rounded-full border border-dashed border-blue-300 bg-white/80 px-3 py-2 text-xs text-blue-700">+ Add Slate at end</button>}
                {!groups.length && <p className="p-4 text-sm text-slate-500">No Timeline assets match this filter.</p>}
              </div>
            </div>
          )}
          {!loading && items.length < counts.total && <div className="mt-2 flex justify-center"><Button type="button" variant="outline" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "Loading…" : `Load more (${items.length} of ${counts.total})`}</Button></div>}
          {selected && <div className="mt-3 rounded-lg border bg-white p-3 text-sm text-slate-900">
            <b>Selected:</b> {selected.photo?.id} · {classify(selected.photo || {}).role} · Group {selected.image_group_id || selected.photo?.image_group_id || "unassigned"} · Captured {selected.photo?.captured_at || "time unavailable"}
            <Button type="button" variant="outline" className="ml-3" onClick={() => setSelected(null)}>Close</Button>
          </div>}
        </SectionPanel>
      </div>
    </AppShell>
  );
}
