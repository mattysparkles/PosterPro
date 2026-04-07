import { Sparkles, WandSparkles } from 'lucide-react';

import MarketplaceStatusPanel from './MarketplaceStatusPanel';
import StatusPill from './StatusPill';
import Button from './ui/button';
import { Card, CardDescription, CardTitle } from './ui/card';
import Input from './ui/input';

const MARKETPLACES = ['ebay', 'etsy', 'mercari', 'facebook'];

export default function ListingEditor({ listing, onSave, onGenerate, onPublish, publishState, statuses }) {
  return (
    <Card className="h-full" data-tour="view-inventory">
      <div className="mb-3 flex items-center justify-between gap-2">
        <CardTitle>Listing #{listing.id}</CardTitle>
        <StatusPill status={listing.ebay_publish_status || listing.status} />
      </div>
      <CardDescription className="mb-4">Update details here, then publish everywhere in one click.</CardDescription>

      <div className="space-y-3">
        <Input
          defaultValue={listing.title || ''}
          placeholder="Short clear title (ex: Vintage Canon camera with lens)"
          onBlur={(e) => onSave(listing.id, { title: e.target.value })}
          title="This is the headline buyers see first."
        />
        <textarea
          defaultValue={listing.description || ''}
          placeholder="Describe condition, size, defects, accessories, and what is included."
          className="min-h-28 w-full rounded-2xl border border-border bg-background p-3 text-sm outline-none focus:border-primary/70 focus:ring-2 focus:ring-primary/20"
          onBlur={(e) => onSave(listing.id, { description: e.target.value })}
          title="Explain the item in plain words so anyone can understand quickly."
        />
        <Input
          type="number"
          defaultValue={listing.suggested_price || ''}
          placeholder="Suggested price"
          onBlur={(e) => onSave(listing.id, { suggested_price: Number(e.target.value) })}
          title="Set a clear asking price."
        />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => onGenerate(listing.id)} title="Use AI to improve title and description.">
          <WandSparkles size={16} /> Generate copy
        </Button>
        <Button disabled={publishState.loading} onClick={() => onPublish(listing.id, MARKETPLACES)} data-tour="publish" title="Publish this listing to all connected marketplaces.">
          <Sparkles size={16} /> {publishState.loading ? 'Publishing...' : 'Publish Everywhere'}
        </Button>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {MARKETPLACES.map((market) => (
          <Button key={market} size="sm" variant="outline" disabled={publishState.loading} onClick={() => onPublish(listing.id, [market])} title={`Publish this item only to ${market}.`}>
            {market}
          </Button>
        ))}
      </div>

      {publishState.error && <p className="mt-3 text-sm text-rose-500">{publishState.error}</p>}
      <MarketplaceStatusPanel statuses={statuses} />
    </Card>
  );
}
