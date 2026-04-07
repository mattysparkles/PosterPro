import AppShell from "../components/layout/AppShell";
import ListingEditor from "../components/ListingEditor";
import { Card, CardDescription, CardTitle } from "../components/ui/card";
import { useMarketplacePublish } from "../hooks/useMarketplacePublish";
import useDashboardData from "../hooks/useDashboardData";
import {
  applyListingTemplate,
  createListingTemplate,
  generateListing,
  processListingPhoto,
  toggleAutonomousMode,
  updateListing,
} from "../lib/api";

export default function ListingsPage({ theme, setTheme }) {
  const { publish, publishing, errors, statusByListing } =
    useMarketplacePublish();
  const { listings, autonomousConfig, listingTemplates, reload } =
    useDashboardData();

  return (
    <AppShell
      active="/listings"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
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
      <Card>
        <CardTitle>Listings</CardTitle>
        <CardDescription className="mb-4">
          Everything in one place—edit copy, adjust price, then publish
          confidently.
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
    </AppShell>
  );
}
