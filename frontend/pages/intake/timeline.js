import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import PageHeader from "../../components/ui/page-header";
import SectionPanel from "../../components/ui/section-panel";
import Button from "../../components/ui/button";
import ActionLink from "../../components/ui/action-link";
import StatusPill from "../../components/ui/status-pill";
import Select from "../../components/ui/select";
import Checkbox from "../../components/ui/checkbox";
import {
  fetchIntakeTimeline,
  createRetroactiveSlate,
  classifyTimelineAssets,
  resetTimelineClassifications,
  deleteTimelineAsset,
  setTimelinePrimary,
  toThumbnailImageUrl,
} from "../../lib/api";
import { loadTimelineWindow } from "../../lib/timelinePagination.mjs";
import { canonicalGroupTone, groupPalette, slatePalette } from "../../lib/timelineTheme.mjs";
import { clampTimelineZoom, DEFAULT_TIMELINE_ZOOM, TIMELINE_THUMBNAIL_WIDTHS } from "../../lib/timelineLayout.mjs";

const WIDTHS = TIMELINE_THUMBNAIL_WIDTHS;
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
  const [zoom, setZoom] = useState(DEFAULT_TIMELINE_ZOOM);
  const [filter, setFilter] = useState("ALL");
  const [counts, setCounts] = useState({ total: 0, photo_count: 0, slate_count: 0 });
  const scrollRef = useRef(null);
  const itemsRef = useRef([]);
  const selectedRef = useRef(null);
  const pendingViewRestoreRef = useRef(null);

  useEffect(() => { itemsRef.current = items; }, [items]);
  useEffect(() => { selectedRef.current = selected; }, [selected]);

  useLayoutEffect(() => {
    if (!pendingViewRestoreRef.current) return;
    const view = pendingViewRestoreRef.current;
    pendingViewRestoreRef.current = null;
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = view.scrollLeft;
      scrollRef.current.scrollTop = view.scrollTop;
    }
    if (typeof window !== "undefined" && Number.isFinite(view.windowScrollY)) {
      window.scrollTo(0, view.windowScrollY);
    }
  }, [items]);

  const refresh = useCallback(async ({ loadedCount } = {}) => {
    const targetCount = Math.max(PAGE_SIZE, Number(loadedCount ?? itemsRef.current.length) || 0);
    const selectedId = selectedRef.current?.photo?.id;
    pendingViewRestoreRef.current = {
      scrollLeft: scrollRef.current?.scrollLeft || 0,
      scrollTop: scrollRef.current?.scrollTop || 0,
      windowScrollY: typeof window !== "undefined" ? window.scrollY : 0,
    };
    const result = await loadTimelineWindow(fetchIntakeTimeline, targetCount, PAGE_SIZE);
    itemsRef.current = result.items;
    setItems(result.items);
    const visibleAssetIds = new Set(result.items.map((entry) => String(entry.photo?.id)));
    setSelectedIds((current) => current.filter((id) => visibleAssetIds.has(String(id))));
    if (selectedId != null) {
      const selectedEntry = result.items.find((entry) => String(entry.photo?.id) === String(selectedId));
      setSelected(selectedEntry || null);
      selectedRef.current = selectedEntry || null;
    }
    setCounts({
      total: result.total,
      photo_count: result.photo_count,
      slate_count: result.slate_count,
    });
  }, []);

  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem("posterpro.timeline.zoom"));
      if (Number.isFinite(saved)) setZoom(clampTimelineZoom(saved));
    } catch { /* storage may be unavailable in private browsing */ }
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const changeZoom = (value) => {
    const left = scrollRef.current?.scrollLeft || 0;
    const next = clampTimelineZoom(value);
    setZoom(next);
    try { window.localStorage.setItem("posterpro.timeline.zoom", String(next)); } catch { /* best effort */ }
    window.requestAnimationFrame(() => {
      if (scrollRef.current) scrollRef.current.scrollLeft = left;
    });
  };

  const mutate = async (operation, successMessage) => {
    const loadedCount = itemsRef.current.length;
    setBusy(true);
    setFeedback("");
    try {
      await operation();
      await refresh({ loadedCount });
      setFeedback(successMessage);
    } catch (error) {
      setFeedback(error?.message || "Timeline update failed.");
    } finally {
      setBusy(false);
    }
  };

  const addSlate = async (after, before) => {
    const loadedCount = itemsRef.current.length;
    setBusy(true);
    try {
      await createRetroactiveSlate({
        retroactive: true,
        after_photo_id: after?.photo?.id ?? null,
        before_photo_id: before?.photo?.id ?? null,
        title: "",
      });
      await refresh({ loadedCount });
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
      setItems((current) => {
        const merged = [...current, ...(result?.items || [])];
        itemsRef.current = merged;
        return merged;
      });
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

  const renderEntry = (entry, entryIndex, groupEntries, groupIndex, darkGroup) => {
    const photo = entry.photo || {};
    const state = classify(photo);
    const width = WIDTHS[zoom];
    // Keep controls on a light, high-contrast surface even when the group uses
    // an alternating tone.  The previous dark-group palette made shared
    // buttons unreadable after the global Button migration.
    const controlPalette = groupPalette("light");
    const darkGroupControlStyle = {
      color: controlPalette.controlColor,
      backgroundColor: controlPalette.controlBackground,
      borderColor: controlPalette.controlBorder,
    };
    const groupContrastClass = "hover:!bg-slate-100 focus-visible:!ring-blue-500";
    const thumbnail = toThumbnailImageUrl(
      photo.thumbnail_url || photo.display_url || photo.downloaded_url || photo.local_path,
      width,
      width,
    );
    const slateMetadata = photo.metadata_json || {};
    const slateId = photo.slate_id || photo.slate?.id || (String(photo.id || "").startsWith("slate-") ? String(photo.id).slice(6) : null);
    const hasNext = entryIndex < groupEntries.length - 1 || groupIndex < groups.length - 1;
    const nextEntry = entryIndex < groupEntries.length - 1
      ? groupEntries[entryIndex + 1]
      : groups[groupIndex + 1]?.entries?.[0];
    return (
      <div key={photo.id || `${entry.image_group_id}-${entryIndex}`} className="flex min-w-0 items-start gap-2">
        <div className="flex min-w-0 flex-col items-center" style={{ width }}>
          <Checkbox aria-label={`Select timeline asset ${photo.id}`} checked={selectedIds.includes(photo.id)} onChange={() => toggleSelected(photo.id)} className="mb-2 border-slate-300 bg-white" />
          <Button
            type="button"
            onClick={() => setSelected(entry)}
            data-timeline-role={state.role}
            style={{
              width,
              ...(state.slate ? slatePalette(state.role) : {}),
            }}
            variant="ghost"
            size="sm"
            className={`!min-h-0 h-auto w-full rounded-xl border-2 p-1.5 text-left shadow-sm ${state.slate ? "font-semibold" : "border-slate-200 !bg-white text-slate-900"} ${selected?.photo?.id === photo.id ? "ring-2 ring-blue-500" : ""}`}
          >
            <div className="relative aspect-square overflow-hidden rounded-lg bg-slate-100 ring-1 ring-slate-200">
              {thumbnail ? (
                <img src={thumbnail} alt={photo.original_filename || `Timeline asset ${photo.id}`} loading="lazy" decoding="async" className="h-full w-full object-cover" />
              ) : (
                <span className="flex h-full flex-col items-center justify-center gap-1 p-2 text-center text-[10px] font-bold text-slate-800">
                  {state.slate ? "MODERN SLATE NEEDS REPAIR" : "Image unavailable"}
                </span>
              )}
              {state.slate && <span className="absolute left-1 top-1 rounded-md bg-slate-950/85 px-2 py-1 text-[10px] font-black tracking-wide text-white">{state.role} SLATE</span>}
              {state.primary && <span className="absolute bottom-1 left-1 rounded-md bg-amber-300 px-2 py-1 text-[10px] font-black tracking-wide text-amber-950">PRIMARY</span>}
            </div>
            <p className="mt-2 max-w-full truncate text-xs font-semibold">{photo.original_filename || `${state.slate ? "Slate" : "Photo"} ${photo.id}`}</p>
            <div className="mt-1 flex flex-wrap items-center justify-between gap-1">
              <StatusPill status={state.slate ? "warning" : "default"} label={photo.group_photo_label || state.role} />
              {photo.photo_number && <span className="text-[11px] text-slate-500">Image {photo.photo_number}</span>}
            </div>
          </Button>
          {state.slate && slateId && (
            <div className="mt-2 grid w-full grid-cols-2 gap-1">
              <ActionLink href={`/intake/slate?slate_id=${slateId}`} size="sm" className="!h-8 !min-h-8 !rounded-md !px-2 text-[11px] font-semibold">Edit slate</ActionLink>
              <ActionLink href={`/intake/slate?slate_id=${slateId}#voice`} size="sm" className="!h-8 !min-h-8 !rounded-md !px-2 text-[11px]">Voice note</ActionLink>
              {slateMetadata.legacy_source_image_url && <ActionLink href={slateMetadata.legacy_source_image_url} target="_blank" rel="noreferrer" external size="sm" className="col-span-2 !h-8 !min-h-8 !rounded-md !px-2 text-[11px]">View source</ActionLink>}
            </div>
          )}
          <div className="mt-2 grid w-full grid-cols-1 gap-1">
            {!state.slate && (state.primary ? (
              <>
                <Button size="sm" type="button" variant="outline" disabled style={darkGroupControlStyle} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>PRIMARY</Button>
                <Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => mutate(() => setTimelinePrimary(photo.id, { clear: true }), "Automatic best-photo selection restored for this group.")} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>Clear primary</Button>
              </>
            ) : (
              <Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => mutate(() => setTimelinePrimary(photo.id), "Manual primary selected and listing media reordered.")} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>Set primary</Button>
            ))}
            {state.slate && <Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => mutate(() => classifyTimelineAssets([photo.id], state.role === "TAIL" ? "HEAD" : "TAIL"), `Slate marked ${state.role === "TAIL" ? "HEAD" : "TAIL"}.`)} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>{state.role === "TAIL" ? "Mark Head slate" : "Mark Tail slate"}</Button>}
            {state.slate && <Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => mutate(() => classifyTimelineAssets([photo.id], "PHOTO"), "Slate classification removed.")} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>Remove slate</Button>}
            <Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => remove(entry)} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>Delete</Button>
          </div>
          {!state.slate && <div className="mt-1 grid grid-cols-2 gap-1"><Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => mutate(() => classifyTimelineAssets([photo.id], "HEAD"), "Marked Head Slate.")} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>Head slate</Button><Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => mutate(() => classifyTimelineAssets([photo.id], "TAIL"), "Marked Tail Slate; preceding photos stay in this item group.")} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>Tail slate</Button></div>}
        </div>
        {hasNext && nextEntry && <Button type="button" variant="tertiary" size="sm" disabled={busy} onClick={() => void addSlate(entry, nextEntry)} className="mt-24 shrink-0 whitespace-nowrap rounded-full border border-dashed border-blue-300 !bg-blue-50 px-3 py-2 text-xs text-blue-800">+ Add slate</Button>}
      </div>
    );
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="Photo Timeline" description="Capture chronology is authoritative. Head Slates identify photos after them; Tail Slates identify photos before them." />
        <SectionPanel title="Intake timeline" description="Capture chronology is preserved. Head Slates begin a group; Tail Slates close one. Product photos stay large enough to inspect labels and condition.">
          <div className="mb-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="flex flex-wrap items-end gap-4">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-slate-800">Thumbnail size</span>
                <Button size="icon-sm" variant="secondary" type="button" aria-label="Zoom out" onClick={() => changeZoom(zoom - 1)}>−</Button>
                <input className="w-32 accent-blue-700" aria-label="Timeline zoom" type="range" min="1" max={WIDTHS.length - 1} value={zoom} onChange={(event) => changeZoom(Number(event.target.value))} />
                <Button size="icon-sm" variant="secondary" type="button" aria-label="Zoom in" onClick={() => changeZoom(zoom + 1)}>+</Button>
                <span className="min-w-12 text-center text-xs font-medium text-slate-600">{WIDTHS[zoom]} px</span>
              </div>
              <label className="flex items-center gap-2 text-sm font-semibold text-slate-800">View
                <Select aria-label="Timeline filter" value={filter} onChange={(event) => setFilter(event.target.value)} className="h-10 w-auto min-w-32 py-1 text-sm">
                  <option value="ALL">All assets</option><option value="PHOTOS">Photos</option><option value="SLATES">Slates</option><option value="HEAD">Head Slates</option><option value="TAIL">Tail Slates</option>
                </Select>
              </label>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" aria-label="Timeline totals">
                <span><b className="text-slate-900">{counts.total.toLocaleString()}</b> assets</span>
                <span><b className="text-slate-900">{counts.photo_count.toLocaleString()}</b> photos</span>
                <span><b className="text-slate-900">{counts.slate_count.toLocaleString()}</b> slates</span>
                <span>{items.length.toLocaleString()} loaded</span>
              </div>
              <div className="ml-auto flex items-center gap-2">
                <Button type="button" variant="outline" onClick={() => void refresh()} disabled={loading || busy}>Refresh</Button>
                <details className="relative">
                  <summary className="cursor-pointer list-none rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm">Advanced</summary>
                  <div className="absolute right-0 z-20 mt-2 w-72 rounded-xl border border-slate-200 bg-white p-3 shadow-xl">
                    <p className="text-xs leading-5 text-slate-600">Reset is destructive and should only be used when intentionally repairing historical classifications. Manual Slate corrections are preserved by default.</p>
                    <Button type="button" variant="danger" className="mt-3 w-full" disabled={busy} onClick={() => { if (window.confirm(`Reset historical classifications for loaded Timeline assets? This cannot be undone.`)) void mutate(() => resetTimelineClassifications({ scope: "photo_ids", photo_ids: items.map((entry) => entry.photo?.id).filter((id) => id && !String(id).startsWith("slate-")), preserve_modern: true }), "Loaded classifications reset."); }}>Reset classifications</Button>
                  </div>
                </details>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-600"><span className="rounded-full bg-lime-100 px-2 py-1 font-semibold text-lime-900">HEAD SLATE</span><span className="rounded-full bg-fuchsia-100 px-2 py-1 font-semibold text-fuchsia-900">TAIL SLATE</span><span>{loadedCounts.ambiguous.toLocaleString()} possible slate candidates</span>{feedback && <span role="status" className="font-semibold text-emerald-700">{feedback}</span>}</div>
          </div>
          {selectedIds.length > 0 && <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-white p-2 text-xs">
            <span>{selectedIds.length} selected</span>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "PHOTO"), "Selected assets marked as photos.").then(() => setSelectedIds([]))}>Mark selected photos</Button>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "HEAD"), "Selected assets marked as Head Slates.").then(() => setSelectedIds([]))}>Mark selected Head Slates</Button>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "TAIL"), "Selected assets marked as Tail Slates.").then(() => setSelectedIds([]))}>Mark selected Tail Slates</Button>
            <Button type="button" variant="outline" onClick={() => setSelectedIds([])}>Clear selection</Button>
          </div>}
          {loading ? <p className="text-sm text-slate-500">Loading timeline…</p> : (
            <div ref={scrollRef} className="max-w-full overflow-x-auto pb-3">
              <div className="flex min-w-0 flex-col gap-4">
                {filter === "ALL" && items[0] && <Button type="button" variant="tertiary" size="sm" disabled={busy} onClick={() => void addSlate(null, items[0])} className="my-auto shrink-0 rounded-full border-dashed border-blue-300 bg-white/80 px-3 py-2 text-xs text-blue-700">+ Add Slate at start</Button>}
                {groups.map((group, groupIndex) => {
              const tone = canonicalGroupTone(group.index);
              const palette = groupPalette(tone);
              const background = { backgroundColor: palette.backgroundColor, color: palette.color };
              const darkGroup = tone === "dark";
              return <section key={group.id} data-image-group-id={group.id} data-image-group-index={group.index} data-image-group-tone={tone} style={background} className="w-full min-w-0 rounded-2xl border border-slate-300/80 p-4 shadow-sm">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-current/15 pb-2"><span className="text-sm font-bold tracking-tight">{group.index ? `Item Group ${group.index}` : "Unassigned group"}</span><span className="rounded-full bg-white/80 px-2 py-1 text-xs font-semibold text-slate-700">{group.entries.filter((entry) => !classify(entry.photo || {}).slate).length} product photos · {group.entries.filter((entry) => classify(entry.photo || {}).slate).length} Slates</span></div>
                    <div className="flex flex-wrap items-start gap-4">{group.entries.map((entry, index) => renderEntry(entry, index, group.entries, groupIndex, darkGroup))}</div>
                  </section>;
                })}
                {filter === "ALL" && items.length === counts.total && items.length > 0 && <Button type="button" variant="tertiary" size="sm" disabled={busy} onClick={() => void addSlate(items[items.length - 1], null)} className="my-auto shrink-0 rounded-full border-dashed border-blue-300 bg-white/80 px-3 py-2 text-xs text-blue-700">+ Add Slate at end</Button>}
                {!groups.length && <p className="p-4 text-sm text-slate-500">No Timeline assets match this filter.</p>}
              </div>
            </div>
          )}
          {!loading && items.length < counts.total && <div className="mt-4 flex justify-center"><Button type="button" variant="outline" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "Loading…" : `Load more (${items.length.toLocaleString()} of ${counts.total.toLocaleString()})`}</Button></div>}
          {selected && <div role="dialog" aria-label="Photo inspector" className="mt-4 grid gap-4 rounded-2xl border border-blue-200 bg-white p-4 text-sm text-slate-900 shadow-sm md:grid-cols-[minmax(220px,360px)_1fr]">
            <div className="overflow-hidden rounded-xl bg-slate-100"><img src={toThumbnailImageUrl(selected.photo?.display_url || selected.photo?.downloaded_url || selected.photo?.local_path, 900, 900)} alt={selected.photo?.original_filename || `Timeline asset ${selected.photo?.id}`} className="h-full max-h-[360px] w-full object-contain" /></div>
            <div><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Photo inspector</p><h3 className="mt-1 text-lg font-bold">{selected.photo?.original_filename || `Asset ${selected.photo?.id}`}</h3></div><Button type="button" variant="outline" onClick={() => setSelected(null)}>Close</Button></div><dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-xs text-slate-500">Asset</dt><dd className="font-medium">#{selected.photo?.id}</dd></div><div><dt className="text-xs text-slate-500">Role</dt><dd className="font-medium">{classify(selected.photo || {}).role}</dd></div><div><dt className="text-xs text-slate-500">Group</dt><dd className="font-medium">{selected.image_group_id || selected.photo?.image_group_id || "unassigned"}</dd></div><div><dt className="text-xs text-slate-500">Captured</dt><dd className="font-medium">{selected.photo?.captured_at || "time unavailable"}</dd></div></dl></div>
          </div>}
        </SectionPanel>
      </div>
    </AppShell>
  );
}
