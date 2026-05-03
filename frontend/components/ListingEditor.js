import { useMemo, useState } from "react";
import { Camera, Sparkles, WandSparkles } from "lucide-react";

import MarketplaceStatusPanel from "./MarketplaceStatusPanel";
import StatusPill from "./StatusPill";
import Button from "./ui/button";
import { Card, CardDescription, CardTitle } from "./ui/card";
import PhotoEditorModal from "./PhotoEditorModal";
import Input from "./ui/input";

const PLATFORM_OPTIONS = [
  "ebay",
  "etsy",
  "poshmark",
  "mercari",
  "depop",
  "whatnot",
];

export default function ListingEditor({
  listing,
  templates = [],
  onApplyTemplate,
  onSaveTemplate,
  onSave,
  onGenerate,
  onPublish,
  onPhotoUpdated,
  publishState,
  statuses,
}) {
  const [openEditor, setOpenEditor] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [selectedPlatforms, setSelectedPlatforms] = useState(["ebay"]);

  const statusMap = useMemo(() => {
    const map = {};
    (statuses || []).forEach((row) => {
      map[row.marketplace] = row.status;
    });
    return map;
  }, [statuses]);

  const togglePlatform = (platform) => {
    setSelectedPlatforms((prev) =>
      prev.includes(platform)
        ? prev.filter((p) => p !== platform)
        : [...prev, platform],
    );
  };

  return (
    <Card className="h-full" data-tour="view-inventory">
      <div className="mb-3 flex items-center justify-between gap-2">
        <CardTitle>Listing #{listing.id}</CardTitle>
        <StatusPill status={listing.ebay_publish_status || listing.status} />
      </div>
      <CardDescription className="mb-4">
        Tighten the draft, set pricing, and publish when it is ready.
      </CardDescription>

      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-[20px] border border-[#e5e7eb] bg-[#f8fafc] p-3">
        <select
          className="h-12 rounded-xl border border-[#e5e7eb] bg-white px-3 text-sm text-[#111827] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
          value={selectedTemplateId}
          onChange={(e) => setSelectedTemplateId(e.target.value)}
        >
          <option value="">Use Template…</option>
          {templates.map((template) => (
            <option key={template.id} value={String(template.id)}>
              {template.name}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          variant="outline"
          disabled={!selectedTemplateId}
          onClick={() =>
            onApplyTemplate(listing.id, Number(selectedTemplateId))
          }
        >
          Apply Template
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() =>
            onSaveTemplate({
              user_id: listing.user_id,
              name: `${listing.category_suggestion || listing.category_id || "General"} Defaults`,
              category_id: listing.category_id || null,
              is_category_default: true,
              fields: {
                title: listing.title,
                description: listing.description,
                condition: listing.condition,
                listing_price: listing.listing_price || listing.suggested_price,
              },
            })
          }
        >
          Save as Default
        </Button>
      </div>

      <div className="space-y-3">
        <Input
          defaultValue={listing.title || ""}
          placeholder="Short clear title (ex: Vintage Canon camera with lens)"
          onBlur={(e) => onSave(listing.id, { title: e.target.value })}
          title="This is the headline buyers see first."
        />
        <textarea
          defaultValue={listing.description || ""}
          placeholder="Describe condition, size, defects, accessories, and what is included."
          className="min-h-28 w-full rounded-[16px] border border-[#e5e7eb] bg-white p-4 text-[15px] text-[#111827] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
          onBlur={(e) => onSave(listing.id, { description: e.target.value })}
          title="Explain the item in plain words so anyone can understand quickly."
        />
        <Input
          type="number"
          defaultValue={listing.suggested_price || ""}
          placeholder="Suggested price"
          onBlur={(e) =>
            onSave(listing.id, { suggested_price: Number(e.target.value) })
          }
          title="Set a clear asking price."
        />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() => setOpenEditor(true)}
          title="Open premium photo editor."
        >
          <Camera size={16} /> Edit photos
        </Button>
        <Button
          variant="secondary"
          onClick={() => onGenerate(listing.id)}
          title="Use AI to improve title and description."
        >
          <WandSparkles size={16} /> AI Enhance
        </Button>
        <Button
          disabled={publishState.loading || !selectedPlatforms.length}
          onClick={() => onPublish(listing.id, selectedPlatforms)}
          data-tour="publish"
          title="Publish this listing to selected marketplaces."
        >
          <Sparkles size={16} />{" "}
          {publishState.loading ? "Publishing..." : "Publish Selected"}
        </Button>
      </div>

      <div className="mt-4">
        <p className="mb-2 text-sm font-semibold text-[#111827]">Choose marketplaces</p>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
          {PLATFORM_OPTIONS.map((market) => {
            const enabled = selectedPlatforms.includes(market);
            const status = statusMap[market] || "Not published";
            return (
              <button
                key={market}
                type="button"
                className={`rounded-[18px] border p-3 text-left transition ${
                  enabled ? 'border-[#2563eb] bg-[#eff6ff] shadow-[0_10px_24px_rgba(37,99,235,0.08)]' : 'border-[#e5e7eb] bg-white'
                }`}
                onClick={() => togglePlatform(market)}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold capitalize text-[#111827]">{market}</span>
                  <span
                    className={`h-5 w-9 rounded-full ${enabled ? 'bg-[#2563eb]' : 'bg-[#cbd5e1]'}`}
                  />
                </div>
                <span className="mt-2 inline-block rounded-full border border-[#e5e7eb] bg-[#f8fafc] px-2 py-0.5 text-xs font-semibold text-[#667085]">
                  {status}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {publishState.error && (
        <p className="mt-3 text-sm text-rose-500">{publishState.error}</p>
      )}
      <MarketplaceStatusPanel statuses={statuses} />

      <PhotoEditorModal
        open={openEditor}
        listing={listing}
        onClose={() => setOpenEditor(false)}
        onApply={onPhotoUpdated}
      />
    </Card>
  );
}
