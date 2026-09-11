import { useEffect, useMemo, useState } from "react";
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
const classify = (p) => {
  const m = p?.metadata_json || {};
  const type = String(
    p?.classification || m.classification || p?.image_type || "",
  ).toUpperCase();
  const source = p?.classification_source || m.classification_source || "";
  const slate =
    source === "MANUAL_OPERATOR"
      ? type !== "PHOTO"
      : (source === "SYSTEM_GENERATED" ||
          source === "MODERN_SLATE" ||
          m.slate_provenance === "generated") &&
        ["SLATE", "HEAD", "TAIL", "RETROACTIVE", "RETROACTIVE_HEAD"].includes(
          type,
        );
  return {
    slate,
    type: slate ? type : "PHOTO",
    tail: type === "TAIL",
    primary: Boolean(m.timeline_primary),
  };
};

export default function IntakeTimeline() {
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [zoom, setZoom] = useState(2);
  const [filter, setFilter] = useState("ALL");
  const refresh = () =>
    fetchIntakeTimeline().then((r) => setItems(r?.items || []));
  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, []);
  const ordered = useMemo(
    () =>
      [...items]
        .sort((a, b) =>
          String(a.timeline_key || "").localeCompare(
            String(b.timeline_key || ""),
          ),
        )
        .filter((e) => {
          const c = classify(e.photo || {});
          return (
            filter === "ALL" ||
            (filter === "PHOTOS" && !c.slate) ||
            (filter === "SLATES" && c.slate) ||
            (filter === "TAIL" && c.tail)
          );
        }),
    [items, filter],
  );
  const mutate = async (fn, message) => {
    setBusy(true);
    try {
      await fn();
      await refresh();
      setFeedback(message);
    } catch (e) {
      setFeedback(e.message || "Timeline update failed");
    } finally {
      setBusy(false);
    }
  };
  const mark = (p, value) =>
    mutate(() => classifyTimelineAssets([p.id], value), `Marked ${value}.`);
  const remove = (p) => {
    if (window.confirm("Remove this image or Slate from the timeline?"))
      mutate(() => deleteTimelineAsset(p.id), "Timeline asset removed.");
  };
  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader
          title="Photo Timeline"
          description="Review capture chronology, remove duplicate Slates, and choose listing photos."
          actions={
            <Button variant="outline" onClick={refresh}>
              Refresh
            </Button>
          }
        />
        <SectionPanel
          title="Intake filmstrip"
          description="Product groups alternate dark/light backgrounds. Head Slates are neon green; Tail Slates are neon purple."
        >
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <span>Zoom</span>
            <button
              type="button"
              onClick={() => setZoom(Math.max(0, zoom - 1))}
            >
              −
            </button>
            <input
              aria-label="Timeline zoom"
              type="range"
              min="0"
              max="4"
              value={zoom}
              onChange={(e) => setZoom(Number(e.target.value))}
            />
            <button
              type="button"
              onClick={() => setZoom(Math.min(4, zoom + 1))}
            >
              +
            </button>
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="rounded border px-2 py-1 text-xs"
            >
              <option>ALL</option>
              <option>PHOTOS</option>
              <option>SLATES</option>
              <option>TAIL</option>
            </select>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                if (window.confirm("Reset historical classifications?"))
                  mutate(
                    () =>
                      resetTimelineClassifications({
                        scope: "all",
                        preserve_modern: true,
                      }),
                    "Classifications reset.",
                  );
              }}
            >
              Reset classifications
            </Button>
            {feedback && (
              <span role="status" className="text-xs text-emerald-700">
                {feedback}
              </span>
            )}
          </div>
          {loading ? (
            <p>Loading timeline…</p>
          ) : (
            <div className="overflow-x-auto pb-3">
              <div className="flex min-w-max items-end gap-2">
                {ordered.map((entry, index) => {
                  const p = entry.photo || {};
                  const c = classify(p);
                  const src = toThumbnailImageUrl(
                    p.thumbnail_url ||
                      p.display_url ||
                      p.downloaded_url ||
                      p.local_path,
                    WIDTHS[zoom],
                    WIDTHS[zoom],
                  );
              const groupIndex = [...ordered.slice(0, index + 1)].reverse().find((candidate) => classify(candidate.photo || {}).slate)?.photo?.slate_number || 0;
              const bg = c.slate
                ? c.tail
                  ? "bg-fuchsia-500 border-fuchsia-300"
                  : "bg-lime-300 border-lime-400"
                : groupIndex % 2
                  ? "bg-slate-200"
                  : "bg-slate-700";
                  return (
                    <div
                      key={p.id || index}
                      className="flex items-center gap-2"
                    >
                      <div className="flex flex-col items-center">
                        <div
                          role="button"
                          tabIndex={0}
                          onClick={() => setSelected(entry)}
                          onKeyDown={(e) =>
                            (e.key === "Enter" || e.key === " ") &&
                            setSelected(entry)
                          }
                          style={{ width: WIDTHS[zoom] }}
                          className={`rounded-xl border-4 p-1 text-left ${bg} ${c.primary ? "ring-4 ring-amber-300" : ""}`}
                        >
                          <div className="relative aspect-square overflow-hidden rounded-lg bg-slate-100">
                            {src ? (
                              <img
                                src={src}
                                alt={p.original_filename || `Photo ${p.id}`}
                                loading="lazy"
                                className="h-full w-full object-cover"
                              />
                            ) : (
                              <span className="flex h-full items-center justify-center p-2 text-center text-[10px]">
                                Image unavailable
                              </span>
                            )}
                            {c.slate && (
                              <span className="absolute left-1 top-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-black text-white">
                                {c.tail ? "TAIL SLATE" : "HEAD SLATE"}
                              </span>
                            )}
                          </div>
                          <p className="mt-1 truncate text-[10px]">
                            {p.original_filename || `Photo ${p.id}`}
                          </p>
                          <StatusPill
                            status={c.slate ? "warning" : "default"}
                            label={c.primary ? "PRIMARY" : c.type}
                          />
                        </div>
                        <div className="mt-1 flex gap-1">
                          <Button
                            variant="outline"
                            disabled={busy || c.slate}
                            onClick={() =>
                              mutate(
                                () => setTimelinePrimary(p.id),
                                "Primary listing photo selected.",
                              )
                            }
                            className="px-1 py-0 text-[9px]"
                          >
                            Primary
                          </Button>
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() => remove(p)}
                            className="px-1 py-0 text-[9px]"
                          >
                            Delete
                          </Button>
                        </div>
                        {c.slate ? (
                          <>
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() => mark(p, "PHOTO")}
                            className="mt-1 px-1 py-0 text-[9px]"
                          >
                            Remove Slate
                          </Button>
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() => mark(p, "TAIL")}
                            className="mt-1 ml-1 px-1 py-0 text-[9px]"
                          >
                            Tail Slate
                          </Button>
                          </>
                        ) : (
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() => mark(p, "SLATE")}
                            className="mt-1 px-1 py-0 text-[9px]"
                          >
                            Mark Slate
                          </Button>
                        )}
                      </div>
                      {index < ordered.length - 1 && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            mutate(
                              () =>
                                createRetroactiveSlate({
                                  retroactive: true,
                                  after_photo_id: entry.photo?.id || null,
                                  before_photo_id:
                                    ordered[index + 1]?.photo?.id || null,
                                  title: "",
                                }),
                              "Slate created.",
                            )
                          }
                          className="rounded-full border border-dashed border-blue-300 px-2 py-1 text-xs text-blue-600"
                        >
                          + Add Slate
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {selected && (
            <div className="rounded-lg border p-3 text-sm">
              <b>Selected:</b> {selected.photo?.id} ·{" "}
              {classify(selected.photo).type}
              <Button
                variant="outline"
                className="ml-3"
                onClick={() => setSelected(null)}
              >
                Close
              </Button>
            </div>
          )}
        </SectionPanel>
      </div>
    </AppShell>
  );
}
