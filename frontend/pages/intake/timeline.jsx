import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/router";
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
  fetchTimelineGooglePhotos,
  runIntakeMonitor,
  insertTimelineGooglePhoto,
  uploadTimelinePhoto,
  createRetroactiveSlate,
  classifyTimelineAssets,
  resetTimelineClassifications,
  deleteTimelineAsset,
  deleteTimelineAssets,
  setTimelinePrimary,
  toThumbnailImageUrl,
} from "../../lib/api";
import { loadTimelineWindow } from "../../lib/timelinePagination.mjs";
import { canonicalGroupTone, groupPalette, slatePalette } from "../../lib/timelineTheme.mjs";
import { clampTimelineZoom, DEFAULT_TIMELINE_ZOOM, TIMELINE_THUMBNAIL_WIDTHS } from "../../lib/timelineLayout.mjs";

const WIDTHS = TIMELINE_THUMBNAIL_WIDTHS;
const PAGE_SIZE = 500;
const INITIAL_PAGE_SIZE = 100;
const EMPTY_SLATE_DRAFT = {
  title: "", item_id: "", box_id: "", location: "", brand: "", model: "", condition: "", notes: "", flaws: "",
  weight: "", length: "", width: "", height: "", voice_notes: "", boundary_position: "head",
};

function classify(photo) {
  const metadata = photo?.metadata_json || {};
  const role = String(
    photo?.timeline_role || metadata.timeline_role || photo?.classification || metadata.classification || photo?.image_type || "PHOTO",
  ).toUpperCase();
  const source = photo?.classification_source || metadata.classification_source || "";
  const slate = Boolean(photo?.is_slate) || (source === "MANUAL_OPERATOR" && role !== "PHOTO") || ["HEAD", "TAIL", "SLATE"].includes(role);
  return {
    slate,
    role: role === "TAIL" ? "TAIL" : role === "UNKNOWN" || role === "UNSPECIFIED" || role === "SLATE" ? "UNKNOWN" : slate ? "HEAD" : "PHOTO",
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

function makeSlateTimelineEntry(slate) {
  const metadata = slate?.metadata_json || {};
  const boundary = metadata.retroactive_boundary || {};
  const artwork = metadata.rendered_slate || {};
  const artworkUrl = artwork.storage_path || metadata.rendered_slate_url || slate?.rendered_slate_url || null;
  const role = String(metadata.timeline_role || "HEAD").toUpperCase() === "TAIL" ? "TAIL" : "HEAD";
  const slateId = slate?.id;
  const id = slate?.pending ? String(slateId) : `slate-${slateId}`;
  return {
    is_slate_marker: true,
    late_arrival: false,
    timeline_key: [String(boundary.effective_boundary_at || ""), "slate", String(slateId || "")],
    photo: {
      id,
      original_filename: `Slate ${slate?.item_id || slateId}`,
      local_path: artworkUrl,
      thumbnail_url: artworkUrl,
      display_url: artworkUrl,
      captured_at: boundary.effective_boundary_at || null,
      image_type: "slate",
      is_slate: true,
      is_internal_only: true,
      item_id: slate?.item_id || null,
      slate_id: slateId,
      classification: "SLATE",
      classification_source: "MODERN_SLATE",
      timeline_role: role,
      canonical_group_id: slate?.item_id || null,
      metadata_json: {
        classification: "SLATE",
        classification_source: "MODERN_SLATE",
        timeline_role: role,
        official_slate_id: slateId,
        modern_artwork_missing: !artworkUrl,
      },
      slate: {
        id: slateId,
        item_id: slate?.item_id,
        box_id: slate?.box_id,
        location: slate?.location,
        title: slate?.title,
        brand: slate?.brand,
        model: slate?.model,
        condition: slate?.condition,
        dimensions: { length: slate?.length, width: slate?.width, height: slate?.height, weight: slate?.weight },
        notes: slate?.notes,
        flaws: slate?.flaws,
        boundary_position: role === "TAIL" ? "tail" : "head",
      },
    },
  };
}

export default function IntakeTimeline() {
  const router = useRouter();
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [savingSlate, setSavingSlate] = useState(false);
  const [pendingSlateBoundary, setPendingSlateBoundary] = useState("");
  const [deletingAssetIds, setDeletingAssetIds] = useState([]);
  const [feedback, setFeedback] = useState("");
  const [zoom, setZoom] = useState(DEFAULT_TIMELINE_ZOOM);
  const [filter, setFilter] = useState("ALL");
  const [separateGroupRows, setSeparateGroupRows] = useState(true);
  const [counts, setCounts] = useState({ total: 0, photo_count: 0, slate_count: 0, duplicates_collapsed: 0 });
  const [photoInserter, setPhotoInserter] = useState(null);
  const [slateBoundary, setSlateBoundary] = useState(null);
  const [slateDraft, setSlateDraft] = useState(EMPTY_SLATE_DRAFT);
  const [googleAlbumUrl, setGoogleAlbumUrl] = useState("");
  const [googlePhotos, setGooglePhotos] = useState([]);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [jumpGroup, setJumpGroup] = useState("");
  const [jumpPhoto, setJumpPhoto] = useState("");
  const uploadInputRef = useRef(null);
  const scrollRef = useRef(null);
  const loadMoreSentinelRef = useRef(null);
  const itemsRef = useRef([]);
  const selectedRef = useRef(null);
  const pendingViewRestoreRef = useRef(null);
  const savingSlateRef = useRef(false);
  const timelineRefreshBusyRef = useRef(false);
  const loadMoreBusyRef = useRef(false);
  const timelineWindowOffsetRef = useRef(0);
  const lastAddressedJumpRef = useRef("");
  const pendingJumpTargetRef = useRef(null);

  useEffect(() => { itemsRef.current = items; }, [items]);
  useEffect(() => { selectedRef.current = selected; }, [selected]);

  useLayoutEffect(() => {
    if (pendingViewRestoreRef.current) {
      const view = pendingViewRestoreRef.current;
      pendingViewRestoreRef.current = null;
      if (scrollRef.current) {
        scrollRef.current.scrollLeft = view.scrollLeft;
        scrollRef.current.scrollTop = view.scrollTop;
      }
      if (typeof window !== "undefined" && Number.isFinite(view.windowScrollY)) {
        window.scrollTo(0, view.windowScrollY);
      }
    }
    const jumpTarget = pendingJumpTargetRef.current;
    if (jumpTarget) {
      pendingJumpTargetRef.current = null;
      const target = jumpTarget.type === "photo"
        ? [...document.querySelectorAll("[data-timeline-photo-label]")].find((node) => node.dataset.timelinePhotoLabel === jumpTarget.value)
        : [...document.querySelectorAll("[data-timeline-group-number]")].find((node) => node.dataset.timelineGroupNumber === jumpTarget.value);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
        target.focus?.({ preventScroll: true });
        target.classList.add("timeline-jump-highlight");
        window.setTimeout(() => target.classList.remove("timeline-jump-highlight"), 1600);
      }
    }
  }, [items]);

  const refresh = useCallback(async ({ loadedCount } = {}) => {
    const targetCount = Math.max(INITIAL_PAGE_SIZE, Number(loadedCount ?? itemsRef.current.length) || 0);
    const selectedId = selectedRef.current?.photo?.id;
    pendingViewRestoreRef.current = {
      scrollLeft: scrollRef.current?.scrollLeft || 0,
      scrollTop: scrollRef.current?.scrollTop || 0,
      windowScrollY: typeof window !== "undefined" ? window.scrollY : 0,
    };
    const fetchNewestTimeline = (params = {}) => fetchIntakeTimeline({ ...params, order: "newest" });
    const result = await loadTimelineWindow(fetchNewestTimeline, targetCount, PAGE_SIZE);
    timelineWindowOffsetRef.current = 0;
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
      duplicates_collapsed: Number(result.duplicates_collapsed || 0),
    });
  }, []);

  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem("posterpro.timeline.zoom"));
      if (Number.isFinite(saved)) setZoom(clampTimelineZoom(saved));
      const savedGroupRows = window.localStorage.getItem("posterpro.timeline.separate_group_rows");
      setSeparateGroupRows(savedGroupRows == null ? true : savedGroupRows === "true");
    } catch { /* storage may be unavailable in private browsing */ }
    refresh()
      .catch((error) => setFeedback(error?.message || "Could not load the photo timeline."))
      .finally(() => setLoading(false));
    const refreshRecent = async () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (timelineRefreshBusyRef.current) return;
      timelineRefreshBusyRef.current = true;
      try {
        // Poll only the newest 100 entries. Re-fetching every loaded page on
        // each focus/30s tick made a long timeline progressively slower.
        const result = await fetchIntakeTimeline({ limit: INITIAL_PAGE_SIZE, offset: 0, order: "newest" });
        const fresh = Array.isArray(result?.items) ? result.items : [];
        const freshIds = new Set(fresh.map((entry) => String(entry.photo?.id)));
        const merged = [...fresh, ...itemsRef.current.filter((entry) => !freshIds.has(String(entry.photo?.id)))];
        itemsRef.current = merged;
        setItems(merged);
        setCounts({ total: Number(result?.total || 0), photo_count: Number(result?.photo_count || 0), slate_count: Number(result?.slate_count || 0), duplicates_collapsed: Number(result?.duplicates_collapsed || 0) });
      } catch (error) {
        setFeedback(error?.message || "Could not refresh the photo timeline.");
      } finally {
        timelineRefreshBusyRef.current = false;
      }
    };
    const timer = window.setInterval(refreshRecent, 30000);
    window.addEventListener("focus", refreshRecent);
    document.addEventListener("visibilitychange", refreshRecent);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshRecent);
      document.removeEventListener("visibilitychange", refreshRecent);
    };
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

  const changeGroupRowMode = (checked) => {
    setSeparateGroupRows(checked);
    try { window.localStorage.setItem("posterpro.timeline.separate_group_rows", String(checked)); } catch { /* best effort */ }
  };

  const mutate = async (operation, successMessage) => {
    const loadedCount = itemsRef.current.length;
    setBusy(true);
    setFeedback("");
    try {
      await operation();
      setFeedback(successMessage);
    } catch (error) {
      setFeedback(error?.message || "Timeline update failed.");
      setBusy(false);
      return false;
    }
    try {
      await refresh({ loadedCount });
    } catch (refreshError) {
      // The mutation already committed. Never report it as failed or undo the
      // local state merely because the follow-up read timed out.
      setFeedback(`${successMessage} Timeline refresh is pending: ${refreshError?.message || "retry shortly"}`);
    } finally {
      setBusy(false);
    }
    return true;
  };

  const addSlate = (after, before, boundaryPosition) => {
    if (savingSlate) return;
    // The display is newest-first. A Slate at its left/start edge begins a
    // new item; one at the right/end edge closes the preceding item.
    const position = boundaryPosition || (!after && before ? "tail" : "head");
    setSlateDraft({ ...EMPTY_SLATE_DRAFT, boundary_position: position });
    setSlateBoundary({ after, before });
  };

  const insertSlateBetween = async (after, before) => {
    // State updates are asynchronous; keep an immediate ref guard as well so
    // rapid repeated clicks cannot enqueue a second mutation before React has
    // re-rendered the localized pending button.
    if (savingSlateRef.current || (!after?.photo?.id && !before?.photo?.id)) return;
    const boundaryKey = `${after?.photo?.id ?? ""}:${before?.photo?.id ?? ""}`;
    const loadedCount = itemsRef.current.length;
    savingSlateRef.current = true;
    setSavingSlate(true);
    setPendingSlateBoundary(boundaryKey);
    setFeedback("");
    try {
      await createRetroactiveSlate({
        retroactive: true,
        after_photo_id: after?.photo?.id ?? null,
        before_photo_id: before?.photo?.id ?? null,
        boundary_position: "head",
      });
      await refresh({ loadedCount });
      setFeedback("Slate inserted. The next item group is ready for capture and drafting.");
    } catch (error) {
      setFeedback(error?.message || "Could not insert Slate at this boundary.");
    } finally {
      savingSlateRef.current = false;
      setSavingSlate(false);
      setPendingSlateBoundary("");
    }
  };

  const saveTimelineSlate = async (event) => {
    event.preventDefault();
    if (!slateBoundary || savingSlate) return;
    const currentItems = itemsRef.current;
    const { after, before } = slateBoundary;
    const addingAtNewestEdge = Boolean(after) && !before;
    const provisionalId = `pending-slate-${Date.now()}`;
    const role = slateDraft.boundary_position === "tail" ? "TAIL" : "HEAD";
    const provisional = makeSlateTimelineEntry({
      id: provisionalId,
      pending: true,
      item_id: slateDraft.item_id || "Creating…",
      box_id: slateDraft.box_id,
      location: slateDraft.location,
      title: slateDraft.title,
      brand: slateDraft.brand,
      model: slateDraft.model,
      condition: slateDraft.condition,
      notes: slateDraft.notes,
      flaws: slateDraft.flaws,
      metadata_json: { timeline_role: role, retroactive_boundary: { effective_boundary_at: new Date().toISOString() } },
    });
    provisional.photo.pending_slate = true;
    const beforeIndex = before ? currentItems.findIndex((entry) => String(entry.photo?.id) === String(before.photo?.id)) : -1;
    const insertionIndex = addingAtNewestEdge ? 0 : beforeIndex >= 0 ? beforeIndex + 1 : currentItems.length;
    const optimisticItems = [...currentItems];
    optimisticItems.splice(insertionIndex, 0, provisional);
    itemsRef.current = optimisticItems;
    setItems(optimisticItems);
    setCounts((current) => ({ ...current, total: current.total + 1, slate_count: current.slate_count + 1 }));
    setSavingSlate(true);
    let createdSlate = null;
    try {
      createdSlate = await createRetroactiveSlate({
        retroactive: true,
        after_photo_id: after?.photo?.id ?? null,
        before_photo_id: before?.photo?.id ?? null,
        ...slateDraft,
        boundary_position: slateDraft.boundary_position || (addingAtNewestEdge ? "head" : !after && before ? "tail" : "head"),
      });
    } catch (error) {
      const nextItems = itemsRef.current.filter((entry) => String(entry.photo?.id) !== provisionalId);
      itemsRef.current = nextItems;
      setItems(nextItems);
      setCounts((current) => ({ ...current, total: Math.max(0, current.total - 1), slate_count: Math.max(0, current.slate_count - 1) }));
      setFeedback(error?.message || "Could not add Slate.");
      return;
    } finally {
      setSavingSlate(false);
    }
    // Creation is durable at this point. A refresh is only a view update and
    // must never turn a committed Slate into a reported failure or remove it
    // from the local timeline when the network briefly drops afterward.
    setSlateBoundary(null);
    if (addingAtNewestEdge && scrollRef.current) scrollRef.current.scrollLeft = 0;
    setFeedback(`Slate ${createdSlate?.slate?.item_id || ""} added with item, box, location, and drafting facts.`);
    try {
      await refresh({ loadedCount: Math.max(INITIAL_PAGE_SIZE, currentItems.length) });
    } catch (refreshError) {
      setFeedback(`Slate ${createdSlate?.slate?.item_id || ""} was saved. Timeline refresh is pending: ${refreshError?.message || "retry shortly"}`);
    }
  };

  const syncLatestPhotos = async () => {
    setBusy(true);
    setFeedback("");
    try {
      const result = await runIntakeMonitor();
      setFeedback(result?.result?.message || "Google Photos sync queued. New images will appear at the start of the timeline.");
    } catch (error) {
      setFeedback(error?.message || "Could not start Google Photos sync.");
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

  const loadMore = useCallback(async () => {
    const loadedCount = itemsRef.current.length;
    if (loadMoreBusyRef.current || timelineWindowOffsetRef.current + loadedCount >= counts.total) return;
    loadMoreBusyRef.current = true;
    setLoadingMore(true);
    try {
      const result = await fetchIntakeTimeline({ limit: PAGE_SIZE, offset: timelineWindowOffsetRef.current + loadedCount, order: "newest" });
      setItems((current) => {
        const merged = [...current, ...(result?.items || [])];
        itemsRef.current = merged;
        return merged;
      });
      setCounts({ total: Number(result?.total || 0), photo_count: Number(result?.photo_count || 0), slate_count: Number(result?.slate_count || 0), duplicates_collapsed: Number(result?.duplicates_collapsed || 0) });
    } catch (error) {
      setFeedback(error?.message || "Could not load more timeline items.");
    } finally {
      loadMoreBusyRef.current = false;
      setLoadingMore(false);
    }
  }, [counts.total]);

  const jumpToTimelineTarget = async (event) => {
    event.preventDefault();
    const groupValue = String(event.group ?? jumpGroup).trim();
    const photoValue = String(event.photo ?? jumpPhoto).trim();
    if (!groupValue && !photoValue) {
      setFeedback("Enter an item group number or photo label, such as 85 or 669F.");
      return;
    }
    if (groupValue && photoValue) {
      setFeedback("Enter either a group number or a photo label, not both.");
      return;
    }
    setLoadingMore(true);
    setFeedback("");
    try {
      const result = await fetchIntakeTimeline({
        limit: INITIAL_PAGE_SIZE,
        offset: 0,
        order: "newest",
        ...(photoValue ? { jumpPhoto: photoValue } : { jumpGroup: Number(groupValue) }),
      });
      if (result?.jump_index == null) {
        setFeedback(photoValue ? `Photo ${photoValue} was not found.` : `Item Group ${groupValue} was not found.`);
        return;
      }
      const nextItems = Array.isArray(result.items) ? result.items : [];
      timelineWindowOffsetRef.current = Number(result.offset || 0);
      pendingJumpTargetRef.current = {
        type: photoValue ? "photo" : "group",
        value: photoValue ? photoValue.toUpperCase().replace(/[^A-Z0-9]/g, "") : String(groupValue),
      };
      itemsRef.current = nextItems;
      setItems(nextItems);
      setCounts({ total: Number(result?.total || 0), photo_count: Number(result?.photo_count || 0), slate_count: Number(result?.slate_count || 0), duplicates_collapsed: Number(result?.duplicates_collapsed || 0) });
      const jumpKey = photoValue ? `photo:${photoValue.toUpperCase()}` : `group:${groupValue}`;
      lastAddressedJumpRef.current = jumpKey;
      const query = { ...router.query };
      delete query.group;
      delete query.photo;
      if (photoValue) query.photo = photoValue.toUpperCase();
      else query.group = groupValue;
      const navigate = event.restore ? router.replace : router.push;
      void navigate({ pathname: router.pathname, query }, undefined, { shallow: true, scroll: false });
      setFeedback(photoValue ? `Jumped to photo ${photoValue}.` : `Jumped to Item Group ${groupValue}.`);
    } catch (error) {
      setFeedback(error?.message || "Could not jump to that timeline position.");
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    if (!router.isReady) return;
    const groupValue = Array.isArray(router.query.group) ? router.query.group[0] : router.query.group;
    const photoValue = Array.isArray(router.query.photo) ? router.query.photo[0] : router.query.photo;
    const jumpKey = photoValue ? `photo:${String(photoValue).toUpperCase()}` : groupValue ? `group:${groupValue}` : "";
    if (!jumpKey || jumpKey === lastAddressedJumpRef.current) return;
    if (photoValue) {
      setJumpPhoto(String(photoValue));
      setJumpGroup("");
    } else {
      setJumpGroup(String(groupValue));
      setJumpPhoto("");
    }
    void jumpToTimelineTarget({
      preventDefault() {},
      restore: true,
      group: groupValue ? String(groupValue) : "",
      photo: photoValue ? String(photoValue) : "",
    });
  // Query changes intentionally replay addressable Jump To state on reload and
  // browser back/forward, while lastAddressedJumpRef prevents a replace loop.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, router.query.group, router.query.photo]);

  useEffect(() => {
    // Do not observe the sentinel: an observer fires again as soon as a new
    // batch renders while the sentinel is still in the viewport, causing a
    // long timeline to fetch every 500-row page by itself. A real browser
    // scroll is required before loading the next batch.
    const onPageScroll = () => {
      const sentinel = loadMoreSentinelRef.current;
      if (!sentinel || loadMoreBusyRef.current || timelineWindowOffsetRef.current + itemsRef.current.length >= counts.total) return;
      const sentinelTop = sentinel.getBoundingClientRect().top;
      if (sentinelTop <= window.innerHeight + 64) void loadMore();
    };
    window.addEventListener("scroll", onPageScroll, { passive: true });
    return () => window.removeEventListener("scroll", onPageScroll);
  }, [counts.total, loadMore]);

  const remove = async (entry) => {
    const photo = entry.photo || {};
    const assetId = photo.id;
    const key = String(assetId);
    if (deletingAssetIds.includes(key)) return;
    const priorItems = itemsRef.current;
    const priorCounts = counts;
    const priorIndex = priorItems.findIndex((item) => String(item.photo?.id) === key);
    const nextItems = priorItems.filter((item) => String(item.photo?.id) !== key);
    // Optimistically remove only this tile. Do not lock the entire timeline or
    // wait for a full timeline rebuild before the operator can keep working.
    itemsRef.current = nextItems;
    setItems(nextItems);
    setSelectedIds((current) => current.filter((id) => String(id) !== key));
    if (String(selectedRef.current?.photo?.id) === key) {
      setSelected(null);
      selectedRef.current = null;
    }
    setCounts((current) => ({
      ...current,
      total: Math.max(0, current.total - 1),
      photo_count: photo.is_slate ? current.photo_count : Math.max(0, current.photo_count - 1),
      slate_count: photo.is_slate ? Math.max(0, current.slate_count - 1) : current.slate_count,
    }));
    setDeletingAssetIds((current) => [...current, key]);
    setFeedback("");
    try {
      await deleteTimelineAsset(assetId);
      setFeedback("Timeline asset removed; neighboring chronology was preserved.");
      void refresh({ loadedCount: Math.max(INITIAL_PAGE_SIZE, nextItems.length) })
        .catch((error) => setFeedback(error?.message || "Asset was removed, but the timeline refresh failed."));
    } catch (error) {
      const restored = [...itemsRef.current];
      if (!restored.some((item) => String(item.photo?.id) === key)) {
        restored.splice(Math.max(0, Math.min(priorIndex, restored.length)), 0, entry);
        itemsRef.current = restored;
        setItems(restored);
      }
      setCounts(priorCounts);
      setFeedback(error?.message || "Timeline asset could not be removed; it was restored in place.");
    } finally {
      setDeletingAssetIds((current) => current.filter((id) => id !== key));
    }
  };

  const removeSelected = async () => {
    const assetIds = selectedIds.filter((id) => /^\d+$/.test(String(id)) || /^slate-\d+$/.test(String(id)));
    if (!assetIds.length) {
      setFeedback("Select one or more timeline assets to delete.");
      return;
    }
    if (!window.confirm(`Soft-delete ${assetIds.length} selected timeline asset${assetIds.length === 1 ? "" : "s"}? Their provenance remains recoverable.`)) return;
    await mutate(() => deleteTimelineAssets(assetIds), `${assetIds.length} selected timeline asset${assetIds.length === 1 ? "" : "s"} removed; chronology was preserved.`);
    setSelectedIds([]);
  };

  const openPhotoInserter = (newerEntry, olderEntry) => {
    // UI is newest-first while the persisted capture chronology is oldest-first.
    setPhotoInserter({ after: olderEntry?.photo?.id ?? null, before: newerEntry?.photo?.id ?? null });
    setGooglePhotos([]);
    setGoogleAlbumUrl("");
  };

  const loadGooglePhotos = async () => {
    if (!googleAlbumUrl.trim()) return;
    setGoogleLoading(true);
    setFeedback("");
    try {
      const result = await fetchTimelineGooglePhotos(googleAlbumUrl.trim());
      setGooglePhotos(result?.items || []);
      if (!result?.items?.length) setFeedback("No photos were found in that Google Photos album.");
    } catch (error) {
      setFeedback(error?.message || "Could not read Google Photos.");
    } finally {
      setGoogleLoading(false);
    }
  };

  const insertGooglePhoto = async (photo) => {
    if (!photoInserter || !photo?.url) return;
    await mutate(() => insertTimelineGooglePhoto({ ...photo, album_url: googleAlbumUrl.trim(), after: photoInserter.after, before: photoInserter.before }), "Google Photos image inserted into the timeline.");
    setPhotoInserter(null);
  };

  const chooseUpload = () => uploadInputRef.current?.click();
  const handleTimelineUpload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !photoInserter) return;
    await mutate(() => uploadTimelinePhoto(file, photoInserter), "Uploaded photo inserted into the timeline.");
    setPhotoInserter(null);
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
      state.slate ? Math.max(96, Math.round(width * 0.625)) : width,
    );
    const slateMetadata = photo.metadata_json || {};
    const slateId = photo.slate_id || photo.slate?.id || (String(photo.id || "").startsWith("slate-") ? String(photo.id).slice(6) : null);
    const hasNext = entryIndex < groupEntries.length - 1 || groupIndex < groups.length - 1;
    const nextEntry = entryIndex < groupEntries.length - 1
      ? groupEntries[entryIndex + 1]
      : groups[groupIndex + 1]?.entries?.[0];
    const groupPaletteForEntry = groupPalette(canonicalGroupTone(groupIndex + 1));
    const groupNumber = Number(photo.image_group_index ?? entry.image_group_index ?? groupIndex + 1) || groupIndex + 1;
    const imageLabel = photo.group_photo_label
      ? String(photo.group_photo_label).replace(/[-\s]/g, "")
      : `${photo.photo_number || entryIndex + 1}${String.fromCharCode(65 + (entryIndex % 26))}`;
    return (
      <div key={photo.id || `${entry.image_group_id}-${entryIndex}`} tabIndex={-1} data-timeline-group-number={String(groupNumber)} data-timeline-photo-label={imageLabel.toUpperCase().replace(/[^A-Z0-9]/g, "")} style={{ backgroundColor: groupPaletteForEntry.backgroundColor, color: groupPaletteForEntry.color }} className="flex min-w-0 items-start gap-2 rounded-xl p-2">
        <div className="flex min-w-0 flex-col items-center" style={{ width }}>
          <div className="mb-1 w-full truncate text-center text-[10px] font-bold uppercase tracking-wide text-slate-700">{groupNumber ? `Item Group ${groupNumber}` : "Unassigned"}</div>
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
            className={`!min-h-0 !flex-col !items-stretch h-auto w-full rounded-xl border-2 p-1.5 text-left shadow-sm ${state.slate ? "!border-slate-300 !bg-white !text-slate-900 font-semibold" : "border-slate-200 !bg-white text-slate-900"} ${photo.pending_slate ? "opacity-80" : ""} ${selected?.photo?.id === photo.id ? "ring-2 ring-blue-500" : ""}`}
          >
            <div className={`relative overflow-hidden rounded-lg ring-1 ring-slate-200 ${state.slate ? "aspect-[8/5] bg-[#00FF00]" : "aspect-square bg-slate-100"}`}>
              {thumbnail ? (
                <img src={thumbnail} alt={photo.original_filename || `Timeline asset ${photo.id}`} loading="lazy" decoding="async" className={`h-full w-full ${state.slate ? "object-contain" : "object-cover"}`} />
              ) : (
                state.slate ? (
                  <div className="flex h-full flex-col items-center justify-center border-[6px] border-dashed border-[#087f23] bg-[#00FF00] p-3 text-center text-[#052e16]">
                    <span className="text-[9px] font-black tracking-[0.16em]">CANONICAL SLATE ARTWORK</span>
                    <span className="mt-2 text-xs font-bold">{photo.pending_slate ? "Rendering…" : "Artwork pending"}</span>
                    <span className="mt-1 text-[10px]">The generated Slate image and QR will appear here.</span>
                  </div>
                ) : <span className="flex h-full items-center justify-center p-2 text-center text-[10px] font-bold text-slate-800">Image unavailable</span>
              )}
              {state.primary && <span className="absolute bottom-1 left-1 rounded-md bg-amber-300 px-2 py-1 text-[10px] font-black tracking-wide text-amber-950">PRIMARY</span>}
            </div>
          </Button>
          <div className="mt-2 w-full rounded-md border border-slate-300 bg-white px-1.5 py-1 text-center text-[11px] font-bold leading-tight text-slate-900">
            <div>{state.slate ? (state.role === "UNKNOWN" ? "SLATE" : `${state.role} SLATE`) : imageLabel}</div>
            <div className="mt-0.5 truncate font-medium" title={photo.original_filename || `${state.slate ? "Slate" : "Photo"} ${photo.id}`}>{photo.original_filename || `${state.slate ? "Slate" : "Photo"} ${photo.id}`}</div>
          </div>
          {state.slate && photo.slate && <div className="mt-2 w-full rounded-md border border-[#087f23] bg-[#00FF00] p-2 text-[11px] leading-4 text-[#052e16]">
            <div><b>Item:</b> {photo.slate.item_id || "—"} · <b>Box:</b> {photo.slate.box_id || "—"}</div>
            <div><b>Location:</b> {photo.slate.location || "—"}</div>
            <div className="line-clamp-2"><b>Product:</b> {photo.slate.title || "Add item details"}</div>
          </div>}
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
            {state.slate && <Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => mutate(() => classifyTimelineAssets([photo.id], "PHOTO"), "Slate classification removed.")} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>Remove slate</Button>}
            <Button size="sm" type="button" variant="outline" disabled={busy} style={darkGroupControlStyle} onClick={() => remove(entry)} className={`!h-8 !min-h-8 !px-2 text-[11px] ${groupContrastClass}`}>Delete</Button>
          </div>
        </div>
        {hasNext && nextEntry && <div className="flex shrink-0 self-center flex-col items-center gap-2 px-1">
          {(() => {
            const boundaryKey = `${nextEntry?.photo?.id ?? ""}:${entry?.photo?.id ?? ""}`;
            const pending = pendingSlateBoundary === boundaryKey;
            return <Button type="button" variant="tertiary" size="sm" aria-label="Insert Slate between photos" title="Insert Slate between photos" disabled={pending} onClick={() => void insertSlateBetween(nextEntry, entry)} className="relative z-50 pointer-events-auto cursor-pointer rounded-full border border-dashed border-blue-300 !bg-blue-50 px-2 py-1 text-xs text-blue-800 opacity-100">{pending ? "Inserting…" : "+ Slate"}</Button>;
          })()}
          <Button type="button" variant="tertiary" size="sm" aria-label="Insert Photo between photos" title="Insert Photo between photos" disabled={busy} onClick={() => openPhotoInserter(entry, nextEntry)} className="rounded-full border border-dashed border-emerald-300 !bg-emerald-50 px-2 py-1 text-xs text-emerald-800 opacity-100">+ Photo</Button>
        </div>}
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
              <Checkbox
                label="Separate row for each group"
                checked={separateGroupRows}
                onChange={(event) => changeGroupRowMode(event.target.checked)}
                className="text-sm"
              />
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" aria-label="Timeline totals">
                <span><b className="text-slate-900">{counts.total.toLocaleString()}</b> assets</span>
                <span><b className="text-slate-900">{counts.photo_count.toLocaleString()}</b> photos</span>
                <span><b className="text-slate-900">{counts.slate_count.toLocaleString()}</b> slates</span>
                {counts.duplicates_collapsed > 0 && <span><b className="text-slate-900">{counts.duplicates_collapsed.toLocaleString()}</b> duplicate copies hidden</span>}
                <span>{items.length.toLocaleString()} loaded</span>
              </div>
              <div className="ml-auto flex items-center gap-2">
                <Button type="button" variant="outline" onClick={() => void syncLatestPhotos()} disabled={loading || busy}>Sync Google Photos</Button>
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
            <form onSubmit={jumpToTimelineTarget} className="mt-3 flex flex-wrap items-end gap-2 rounded-lg border border-blue-100 bg-white p-2" aria-label="Jump to timeline position">
              <span className="self-center text-xs font-bold text-slate-700">Jump to</span>
              <label className="text-xs font-medium text-slate-600">Group number<input inputMode="numeric" value={jumpGroup} onChange={(event) => { setJumpGroup(event.target.value); if (event.target.value) setJumpPhoto(""); }} placeholder="85" className="mt-1 block w-24 rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900" /></label>
              <span className="self-center text-xs text-slate-500">or</span>
              <label className="text-xs font-medium text-slate-600">Photo number<input value={jumpPhoto} onChange={(event) => { setJumpPhoto(event.target.value); if (event.target.value) setJumpGroup(""); }} placeholder="669F" className="mt-1 block w-28 rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900" /></label>
              <Button type="submit" size="sm" variant="outline" disabled={loading || loadingMore || (!jumpGroup.trim() && !jumpPhoto.trim())}>Go</Button>
              <span className="self-center text-xs text-slate-500">Photo labels match the timeline number, e.g. 669F.</span>
            </form>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-600"><span className="rounded-full bg-lime-100 px-2 py-1 font-semibold text-lime-900">HEAD SLATE</span><span className="rounded-full bg-fuchsia-100 px-2 py-1 font-semibold text-fuchsia-900">TAIL SLATE</span><span>{loadedCounts.ambiguous.toLocaleString()} possible slate candidates</span>{feedback && <span role="status" className="font-semibold text-emerald-700">{feedback}</span>}</div>
          </div>
          {selectedIds.length > 0 && <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-white p-2 text-xs">
            <span>{selectedIds.length} selected</span>
            <Button type="button" variant="danger" disabled={busy || !selectedIds.some((id) => /^\d+$/.test(String(id)) || /^slate-\d+$/.test(String(id)))} onClick={() => void removeSelected()}>Delete selected assets</Button>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "PHOTO"), "Selected assets marked as photos.").then(() => setSelectedIds([]))}>Mark selected photos</Button>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "HEAD"), "Selected assets marked as Head Slates.").then(() => setSelectedIds([]))}>Mark selected Head Slates</Button>
            <Button type="button" variant="outline" disabled={busy} onClick={() => mutate(() => classifyTimelineAssets(selectedIds, "TAIL"), "Selected assets marked as Tail Slates.").then(() => setSelectedIds([]))}>Mark selected Tail Slates</Button>
            <Button type="button" variant="outline" onClick={() => setSelectedIds([])}>Clear selection</Button>
          </div>}
          {loading ? <p className="text-sm text-slate-500">Loading timeline…</p> : (
            <div ref={scrollRef} className="max-w-full overflow-x-auto pb-3">
              <div className="flex min-w-0 flex-col gap-4">
                {filter === "ALL" && separateGroupRows && items[0] && <div className="flex flex-wrap gap-2 px-1">
                  <Button type="button" variant="tertiary" size="sm" disabled={busy || savingSlate} onClick={() => void addSlate(items[0], null, "head")} className="rounded-full border border-dashed border-blue-300 !bg-blue-50 px-3 py-2 text-xs text-blue-800">+ Add Slate at timeline start</Button>
                  {items.length === counts.total && <Button type="button" variant="tertiary" size="sm" disabled={busy || savingSlate} onClick={() => void addSlate(null, items[items.length - 1], "tail")} className="rounded-full border border-dashed border-blue-300 !bg-blue-50 px-3 py-2 text-xs text-blue-800">+ Add Slate at timeline end</Button>}
                </div>}
                {separateGroupRows ? <>
                  {groups.map((group, groupIndex) => {
                  const tone = canonicalGroupTone(group.index);
              const palette = groupPalette(tone);
              const background = { backgroundColor: palette.backgroundColor, color: palette.color };
              const darkGroup = tone === "dark";
              return <section key={group.id} data-image-group-id={group.id} data-image-group-index={group.index} data-image-group-tone={tone} style={background} className="w-full min-w-0 rounded-2xl border border-slate-300/80 p-4 shadow-sm">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-current/15 pb-2"><span className="text-sm font-bold tracking-tight">{group.index ? `Item Group ${group.index}` : "Unassigned group"}</span><span className="rounded-full bg-white/80 px-2 py-1 text-xs font-semibold text-slate-700">{group.entries.filter((entry) => !classify(entry.photo || {}).slate).length} product photos · {group.entries.filter((entry) => classify(entry.photo || {}).slate).length} Slates</span></div>
                    <div className="max-w-full overflow-x-auto pb-3"><div className="flex min-w-max flex-nowrap items-start gap-4">{group.entries.map((entry, index) => renderEntry(entry, index, group.entries, groupIndex, darkGroup))}</div></div>
                  </section>;
                  })}
                </> : (
                  <section className="w-full min-w-0 rounded-2xl border border-slate-300/80 bg-white p-4 shadow-sm">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-2">
                      <span className="text-sm font-bold tracking-tight">Linear capture timeline</span>
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-700">Groups alternate color</span>
                    </div>
                    <div className="max-w-full overflow-x-auto pb-3"><div className="flex min-w-max flex-nowrap items-start gap-3">
                      {filter === "ALL" && items[0] && <Button type="button" variant="tertiary" size="sm" disabled={busy || savingSlate} onClick={() => void addSlate(items[0], null, "head")} className="mt-8 shrink-0 rounded-full border-dashed border-blue-300 bg-white/80 px-3 py-2 text-xs text-blue-700">+ Add Slate at start</Button>}
                      {groups.flatMap((group, groupIndex) => group.entries.map((entry, index) => renderEntry(entry, index, group.entries, groupIndex, canonicalGroupTone(group.index) === "dark")))}
                      {filter === "ALL" && items.length === counts.total && items.length > 0 && <Button type="button" variant="tertiary" size="sm" disabled={busy || savingSlate} onClick={() => void addSlate(null, items[items.length - 1], "tail")} className="mt-8 shrink-0 rounded-full border-dashed border-blue-300 bg-white/80 px-3 py-2 text-xs text-blue-700">+ Add Slate at end</Button>}
                    </div></div>
                  </section>
                )}
                {!groups.length && <p className="p-4 text-sm text-slate-500">No Timeline assets match this filter.</p>}
                <div ref={loadMoreSentinelRef} className="flex min-h-10 items-center justify-center py-2 text-xs font-medium text-slate-500" aria-live="polite">
                  {loadingMore ? "Loading the next 500 timeline assets…" : timelineWindowOffsetRef.current + items.length < counts.total ? "Scroll for more — loading automatically" : items.length ? "All timeline assets loaded" : ""}
                </div>
              </div>
            </div>
          )}
          {selected && <div role="dialog" aria-label="Photo inspector" className="mt-4 grid gap-4 rounded-2xl border border-blue-200 bg-white p-4 text-sm text-slate-900 shadow-sm md:grid-cols-[minmax(220px,360px)_1fr]">
            <div className="overflow-hidden rounded-xl bg-slate-100"><img src={toThumbnailImageUrl(selected.photo?.display_url || selected.photo?.downloaded_url || selected.photo?.local_path, 900, 900)} alt={selected.photo?.original_filename || `Timeline asset ${selected.photo?.id}`} className="h-full max-h-[360px] w-full object-contain" /></div>
            <div><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Photo inspector</p><h3 className="mt-1 text-lg font-bold">{selected.photo?.original_filename || `Asset ${selected.photo?.id}`}</h3></div><Button type="button" variant="outline" onClick={() => setSelected(null)}>Close</Button></div><dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-xs text-slate-500">Asset</dt><dd className="font-medium">#{selected.photo?.id}</dd></div><div><dt className="text-xs text-slate-500">Role</dt><dd className="font-medium">{classify(selected.photo || {}).role}</dd></div><div><dt className="text-xs text-slate-500">Group</dt><dd className="font-medium">{selected.image_group_id || selected.photo?.image_group_id || "unassigned"}</dd></div><div><dt className="text-xs text-slate-500">Captured</dt><dd className="font-medium">{selected.photo?.captured_at || "time unavailable"}</dd></div></dl></div>
          </div>}
          {slateBoundary && typeof document !== "undefined" ? createPortal(<div role="dialog" aria-modal="true" aria-label="Create full item Slate" style={{ position: "fixed", inset: 0, zIndex: 10000, overflowY: "auto" }} className="bg-slate-950/60 p-4">
            <form onSubmit={saveTimelineSlate} className="mx-auto my-4 h-[calc(100vh-2rem)] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white p-5 text-slate-900 shadow-2xl">
              <div className="flex items-start justify-between gap-3"><div><h2 className="text-xl font-bold">Add item Slate</h2><p className="mt-1 text-sm text-slate-600">This creates the same full Slate used for item identification, warehouse box/location labels, and listing-draft facts.</p></div><Button type="button" variant="outline" onClick={() => setSlateBoundary(null)}>Cancel</Button></div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <fieldset className="sm:col-span-2"><legend className="text-sm font-medium">Slate direction</legend><div className="mt-1 flex flex-wrap gap-4 rounded-lg border border-slate-200 p-3 text-sm"><label className="flex items-center gap-2"><input type="radio" name="boundary_position" value="head" checked={slateDraft.boundary_position === "head"} onChange={(e) => setSlateDraft((v) => ({ ...v, boundary_position: e.target.value }))} />Head Slate — identifies photos after it</label><label className="flex items-center gap-2"><input type="radio" name="boundary_position" value="tail" checked={slateDraft.boundary_position === "tail"} onChange={(e) => setSlateDraft((v) => ({ ...v, boundary_position: e.target.value }))} />Tail Slate — identifies photos before it</label></div></fieldset>
                {[["title","Item / product"],["item_id","Item ID (optional)"],["box_id","Box ID (optional)"],["location","Warehouse location"],["brand","Brand"],["model","Model"],["condition","Condition"],["weight","Weight"],["length","Length"],["width","Width"],["height","Height"]].map(([key,label]) => <label key={key} className="text-sm font-medium">{label}<input value={slateDraft[key]} onChange={(e) => setSlateDraft((v) => ({...v,[key]:e.target.value}))} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>)}
                <label className="text-sm font-medium sm:col-span-2">Notes for identification and listing draft<textarea value={slateDraft.notes} onChange={(e) => setSlateDraft((v) => ({...v,notes:e.target.value}))} rows={3} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
                <label className="text-sm font-medium sm:col-span-2">Flaws / condition details<textarea value={slateDraft.flaws} onChange={(e) => setSlateDraft((v) => ({...v,flaws:e.target.value}))} rows={2} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
                <label className="text-sm font-medium sm:col-span-2">Voice note / extra product facts<textarea value={slateDraft.voice_notes} onChange={(e) => setSlateDraft((v) => ({...v,voice_notes:e.target.value}))} rows={2} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
              </div>
              <div className="sticky bottom-0 z-10 mt-5 flex justify-end gap-2 border-t border-slate-200 bg-white/95 pt-3"><Button type="button" variant="outline" disabled={savingSlate} onClick={() => setSlateBoundary(null)}>Cancel</Button><Button type="submit" disabled={savingSlate}>{savingSlate ? "Saving Slate…" : "Save item Slate"}</Button></div>
            </form>
          </div>, document.body) : null}
          <input ref={uploadInputRef} type="file" accept="image/*" className="hidden" onChange={handleTimelineUpload} />
          {photoInserter && <div role="dialog" aria-label="Insert photo" className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
            <div className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-2xl bg-white p-5 shadow-2xl">
              <div className="flex items-start justify-between gap-4"><div><h2 className="text-lg font-bold text-slate-900">Insert photo between timeline entries</h2><p className="mt-1 text-sm text-slate-600">Choose a Google Photos image or upload one from this device. The photo will be placed at the selected boundary.</p></div><Button type="button" variant="outline" onClick={() => setPhotoInserter(null)}>Close</Button></div>
              <div className="mt-5 grid gap-5 md:grid-cols-2">
                <section className="rounded-xl border border-slate-200 p-4"><h3 className="font-semibold text-slate-900">Google Photos</h3><p className="mt-1 text-xs text-slate-600">Paste any Google Photos album/shared link, then choose an image. Use your Google Photos album or collection link to select from that source.</p><div className="mt-3 flex gap-2"><input value={googleAlbumUrl} onChange={(event) => setGoogleAlbumUrl(event.target.value)} placeholder="https://photos.google.com/..." className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm" /><Button type="button" variant="outline" disabled={googleLoading || !googleAlbumUrl.trim()} onClick={() => void loadGooglePhotos()}>{googleLoading ? "Loading…" : "Load photos"}</Button></div>{googlePhotos.length > 0 && <div className="mt-4 grid max-h-80 grid-cols-3 gap-2 overflow-y-auto">{googlePhotos.map((photo) => <button key={photo.source_photo_id || photo.url} type="button" onClick={() => void insertGooglePhoto(photo)} className="overflow-hidden rounded-lg border border-slate-200 bg-slate-50 text-left hover:ring-2 hover:ring-emerald-400"><img src={photo.url} alt={photo.original_filename || "Google Photos image"} loading="lazy" className="aspect-square w-full object-cover" /><span className="block truncate p-1 text-[10px] text-slate-600">Insert</span></button>)}</div>}</section>
                <section className="rounded-xl border border-slate-200 p-4"><h3 className="font-semibold text-slate-900">Upload from device</h3><p className="mt-1 text-xs text-slate-600">Select an image from your computer or phone. The original is stored in PosterPro and inserted at this boundary.</p><Button type="button" variant="outline" className="mt-4 w-full" onClick={chooseUpload}>Choose image to insert</Button></section>
              </div>
            </div>
          </div>}
        </SectionPanel>
      </div>
    </AppShell>
  );
}
