import { useEffect, useState } from "react";
import { Joyride } from "react-joyride";
import { toast } from "sonner";

import ClusterPreview from "../components/ClusterPreview";
import IntelligencePanel from "../components/IntelligencePanel";
import ListingEditor from "../components/ListingEditor";
import PublishedListings from "../components/PublishedListings";
import SyncPanel from "../components/SyncPanel";
import AppShell from "../components/layout/AppShell";
import Button from "../components/ui/button";
import { Card, CardDescription, CardTitle } from "../components/ui/card";
import { useBatchProgress } from "../hooks/useBatchProgress";
import useDashboardData from "../hooks/useDashboardData";
import { useMarketplacePublish } from "../hooks/useMarketplacePublish";
import {
  applyListingTemplate,
  createListingTemplate,
  generateListing,
  processListingPhoto,
  runAllOvernightBatches,
  runOvernightBatch,
  toggleAutonomousMode,
  updateListing,
} from "../lib/api";

const TOUR_STEPS = [
  {
    target: "body",
    content:
      "Welcome! This is your command center to manage listings effortlessly.",
  },
  {
    target: '[data-tour="upload-photos"]',
    content:
      "Start here: your uploaded photos are grouped and ready for listing.",
  },
  {
    target: '[data-tour="view-inventory"]',
    content: "Review inventory cards, update details, and keep data clean.",
  },
  {
    target: '[data-tour="publish"]',
    content: "Publish to all marketplaces or choose one in a single tap.",
  },
  {
    target: '[data-tour="analytics"]',
    content:
      "Track performance and use insights to make better pricing decisions.",
  },
];

export default function Dashboard({ theme, setTheme }) {
  const [runTour, setRunTour] = useState(false);
  const { publish, publishing, errors, statusByListing, refreshStatus } =
    useMarketplacePublish();
  const { batchStatusById, trackBatch } = useBatchProgress();
  const {
    clusters,
    listings,
    analytics,
    alerts,
    recommendation,
    prediction,
    optimization,
    autonomousConfig,
    storageBatches,
    listingTemplates,
    readyCount,
    recentAutoPublished,
    reload,
  } = useDashboardData();

  useEffect(() => {
    if (!localStorage.getItem("posterpro-tour-done")) setRunTour(true);
  }, []);

  useEffect(() => {
    listings.forEach((listing) =>
      refreshStatus(listing.id).catch(() => undefined),
    );
  }, [listings, refreshStatus]);

  return (
    <>
      <Joyride
        steps={TOUR_STEPS}
        run={runTour}
        continuous
        showSkipButton
        styles={{ options: { primaryColor: "#2563eb", zIndex: 10000 } }}
        callback={(data) => {
          if (data.status === "finished" || data.status === "skipped") {
            localStorage.setItem("posterpro-tour-done", "1");
            setRunTour(false);
          }
        }}
      />
      <AppShell
        active="/"
        autonomousConfig={autonomousConfig}
        onToggleAutonomous={async () => {
          await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
          toast.success("Autonomous mode updated.");
          await reload();
        }}
        theme={theme}
        onToggleTheme={() => {
          const next = theme === "dark" ? "light" : "dark";
          setTheme(next);
          localStorage.setItem("posterpro-theme", next);
          document.documentElement.classList.toggle("dark", next === "dark");
        }}
      >
        <section className="mb-4 grid gap-4 md:grid-cols-3">
          <Card className="bg-grid">
            <CardTitle>Ready to publish</CardTitle>
            <CardDescription>
              {readyCount} listings are ready now.
            </CardDescription>
          </Card>
          <Card>
            <CardTitle>Storage batches</CardTitle>
            <CardDescription>
              {storageBatches.length} batches detected and waiting for actions.
            </CardDescription>
          </Card>
          <Card>
            <CardTitle>Recent automation wins</CardTitle>
            <CardDescription>
              {recentAutoPublished.length} items were auto-published recently.
            </CardDescription>
          </Card>
        </section>

        <ClusterPreview clusters={clusters} />
        <div className="mt-4">
          <IntelligencePanel
            analytics={analytics}
            alerts={alerts}
            recommendation={recommendation}
            prediction={prediction}
            optimization={optimization}
          />
        </div>
        <div className="mt-4">
          <SyncPanel />
        </div>

        <Card className="mt-4">
          <CardTitle>Listing inventory</CardTitle>
          <CardDescription className="mb-4">
            Every card is touch-friendly and written in plain language.
          </CardDescription>
          <div className="grid gap-4 lg:grid-cols-2">
            {listings.map((listing) => (
              <ListingEditor
                key={listing.id}
                listing={listing}
                templates={listingTemplates.filter(
                  (tpl) =>
                    !tpl.category_id || tpl.category_id === listing.category_id,
                )}
                statuses={statusByListing[listing.id] || []}
                publishState={{
                  loading: !!publishing[listing.id],
                  error: errors[listing.id] || "",
                }}
                onSave={async (id, form) => {
                  await updateListing(id, form);
                  await reload();
                }}
                onApplyTemplate={async (id, templateId) => {
                  await applyListingTemplate(id, templateId);
                  await reload();
                }}
                onSaveTemplate={async (payload) => {
                  await createListingTemplate(payload);
                  await reload();
                }}
                onGenerate={async (id) => {
                  await generateListing(id);
                  toast.success(`Listing #${id} improved with AI.`);
                  await reload();
                }}
                onPublish={async (id, targets) => {
                  await publish(id, targets);
                  await reload();
                }}
                onPhotoUpdated={async ({
                  listingId,
                  sourceImage,
                  file,
                  removeBackground,
                  edits,
                }) => {
                  await processListingPhoto({
                    listingId,
                    sourceImage,
                    file,
                    removeBackground,
                    edits,
                  });
                  await reload();
                }}
              />
            ))}
          </div>
        </Card>

        <div className="mt-4">
          <PublishedListings listings={listings} statusMap={statusByListing} />
        </div>
        <div className="mt-4">
          <PublishedListings
            listings={recentAutoPublished}
            statusMap={statusByListing}
            title="Recently Auto-Published"
            emptyMessage="No autonomous publishes yet."
            postedOnly={false}
          />
        </div>

        <Card className="mt-4">
          <CardTitle>Overnight storage runs</CardTitle>
          <CardDescription className="mb-4">
            Run all batches at once or trigger one manually.
          </CardDescription>
          <Button
            onClick={async () => {
              await runAllOvernightBatches();
              await reload();
            }}
            title="Run overnight processing for all storage batches."
          >
            Run Overnight Batch
          </Button>
          <div className="mt-4 space-y-2 text-sm">
            {(storageBatches || []).map((batch) => {
              const liveBatch = batchStatusById[batch.id] || batch;
              return (
                <div
                  key={batch.id}
                  className="rounded-2xl border border-border/70 p-3"
                >
                  #{batch.id} {liveBatch.storage_unit_name || "Unnamed"} —{" "}
                  {liveBatch.status} ({liveBatch.processed_items}/
                  {liveBatch.total_items})
                  {liveBatch.status === "QUEUED" && (
                    <Button
                      size="sm"
                      className="ml-3"
                      onClick={async () => {
                        await runOvernightBatch(batch.id);
                        trackBatch(batch.id);
                        await reload();
                      }}
                    >
                      Run now
                    </Button>
                  )}
                  {liveBatch.status === "PROCESSING" && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="ml-3"
                      onClick={() => trackBatch(batch.id)}
                    >
                      Track
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      </AppShell>
    </>
  );
}
