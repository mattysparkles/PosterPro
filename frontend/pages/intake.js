import { useMemo, useRef, useState } from 'react';
import { Camera, ChevronDown, Upload } from 'lucide-react';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import DataTable from '../components/ui/data-table';
import EmptyState from '../components/ui/empty-state';
import Input from '../components/ui/input';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import { Tabs } from '../components/ui/tabs';
import Toolbar from '../components/ui/toolbar';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import { createStorageUnitBatch, ingestPhotos, toPublicImageUrl, toggleAutonomousMode } from '../lib/api';

const INTAKE_TABS = [
  { value: 'batches', label: 'Batches' },
  { value: 'ungrouped', label: 'Ungrouped' },
  { value: 'grouped', label: 'Grouped' },
];

function formatStatus(value) {
  const label = String(value || 'Pending').replaceAll('_', ' ');
  return label.charAt(0).toUpperCase() + label.slice(1).toLowerCase();
}

export default function IntakePage() {
  const { user } = useAuth();
  const { autonomousConfig, listings, storageBatches, clusters, reload } = useDashboardData(user?.id);
  const [activeTab, setActiveTab] = useState('batches');
  const [storageUnitName, setStorageUnitName] = useState('');
  const [overnightMode, setOvernightMode] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const photoInputRef = useRef(null);
  const zipInputRef = useRef(null);

  const batchPreviewById = useMemo(() => {
    const map = {};
    listings.forEach((listing) => {
      if (listing.batch_id && !map[listing.batch_id] && listing.image_urls?.[0]) {
        map[listing.batch_id] = toPublicImageUrl(listing.image_urls[0]);
      }
    });
    return map;
  }, [listings]);

  const groupedRows = useMemo(() => {
    const listingsByCluster = listings.reduce((accumulator, listing) => {
      if (!listing.cluster_id) return accumulator;
      if (!accumulator[listing.cluster_id]) accumulator[listing.cluster_id] = [];
      accumulator[listing.cluster_id].push(listing);
      return accumulator;
    }, {});

    return clusters.map((cluster) => {
      const clusterListings = listingsByCluster[cluster.id] || [];
      const readyCount = clusterListings.filter((listing) => listing.status === 'ready').length;
      return {
        id: cluster.id,
        thumbnail: clusterListings[0]?.image_urls?.[0] || null,
        group: cluster.title_hint || `Cluster ${cluster.id}`,
        item_count: clusterListings.length || cluster.listing_count || 0,
        status: readyCount ? 'Ready' : 'Grouped',
      };
    });
  }, [clusters, listings]);

  const ungroupedRows = useMemo(
    () =>
      listings
        .filter((listing) => !listing.cluster_id)
        .map((listing) => ({
          id: listing.id,
          thumbnail: listing.image_urls?.[0] || null,
          group: listing.batch_id ? `Batch ${listing.batch_id}` : 'Unassigned',
          item_count: 1,
          status: formatStatus(listing.status),
        })),
    [listings],
  );

  const batchRows = useMemo(
    () =>
      storageBatches.map((batch) => ({
        id: batch.id,
        thumbnail: batchPreviewById[batch.id] || null,
        group: batch.storage_unit_name || `Batch ${batch.id}`,
        item_count: batch.total_items || batch.processed_items || 0,
        photo_count: batch.total_items || 0,
        status: formatStatus(batch.status),
      })),
    [batchPreviewById, storageBatches],
  );

  const processingCounts = useMemo(() => {
    const counts = {
      uploaded: listings.filter((listing) => listing.status === 'INGESTED' || listing.status === 'ingested').length,
      grouped: groupedRows.length,
      ungrouped: ungroupedRows.length,
      queued_batches: batchRows.filter((batch) => !String(batch.status).toLowerCase().includes('complete')).length,
    };
    return counts;
  }, [batchRows, groupedRows.length, listings, ungroupedRows.length]);

  const importPhotos = async (event) => {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    setIsUploading(true);
    try {
      await ingestPhotos({ files, storageUnitName });
      toast.success(`Imported ${files.length} photo${files.length === 1 ? '' : 's'}.`);
      await reload();
    } catch (error) {
      toast.error(error.message);
    } finally {
      event.target.value = '';
      setIsUploading(false);
    }
  };

  const importZip = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setIsUploading(true);
    try {
      await createStorageUnitBatch({
        zipFile: file,
        storageUnitName,
        overnightMode,
      });
      toast.success('Batch import queued.');
      await reload();
      setActiveTab('batches');
    } catch (error) {
      toast.error(error.message);
    } finally {
      event.target.value = '';
      setIsUploading(false);
    }
  };

  const commonColumns = [
    {
      key: 'thumbnail',
      label: 'Thumbnail',
      render: (row) => (
        <div className="h-10 w-10 overflow-hidden rounded-[10px] bg-[#f2f4f7]">
          {row.thumbnail ? <img src={toPublicImageUrl(row.thumbnail)} alt={row.group} className="h-full w-full object-cover" /> : null}
        </div>
      ),
    },
    { key: 'group', label: 'Cluster / Group', cellClassName: 'min-w-[240px]' },
    { key: 'item_count', label: 'Item count' },
    {
      key: 'status',
      label: 'Status',
      render: (row) => <StatusPill status={String(row.status).toLowerCase()} label={row.status} />,
    },
  ];

  return (
    <AppShell
      active="/intake"
      title="Intake"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
    >
      <PageHeader
        title="Intake"
        description="Bring photos in, group related items, and move them into listing work."
        actions={
          <>
            <input ref={photoInputRef} type="file" accept="image/*" multiple className="hidden" onChange={importPhotos} />
            <input ref={zipInputRef} type="file" accept=".zip" className="hidden" onChange={importZip} />
            <Button onClick={() => photoInputRef.current?.click()} disabled={isUploading}>
              <Camera size={16} />
              {isUploading ? 'Importing...' : 'Import photos'}
            </Button>
            <Button variant="outline" onClick={() => zipInputRef.current?.click()} disabled={isUploading}>
              <Upload size={16} />
              Upload batch zip
            </Button>
          </>
        }
      />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Uploaded items" value={processingCounts.uploaded} detail="Fresh photo rows waiting for grouping or enrichment." />
        <MetricCard label="Grouped sets" value={processingCounts.grouped} detail="Photo groups that PosterPro believes belong to the same item." />
        <MetricCard label="Ungrouped rows" value={processingCounts.ungrouped} detail="Single rows that still need grouping or manual review." />
        <MetricCard label="Open batches" value={processingCounts.queued_batches} detail="Batch jobs still progressing through intake." />
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
        <SectionPanel title="Folder import flow" description="How to move from a folder of photos into reviewable drafts.">
          <div className="grid gap-3 md:grid-cols-4">
            {[
              { label: '1. Upload', note: 'Import loose photos or a zip batch from a storage unit folder.' },
              { label: '2. Group', note: 'PosterPro clusters visually related images into item candidates.' },
              { label: '3. Review', note: 'Operators verify where items start and stop when the grouping is imperfect.' },
              { label: '4. Draft', note: 'Approved groups move downstream into listings for pricing and publish review.' },
            ].map((step) => (
              <div key={step.label} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{step.label}</p>
                <p className="mt-2 text-sm text-[#475467]">{step.note}</p>
              </div>
            ))}
          </div>
        </SectionPanel>
        <SectionPanel title="Current limitation" description="Important reality check for the intake pipeline.">
          <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-sm text-[#475467]">
              PosterPro can already accept a folder-style intake through multi-photo upload or zip batch upload, but it does not yet perform true object boundary detection inside crowded photos. The current grouping logic is still heuristic clustering, so mixed-item scenes may need operator review before draft generation.
            </p>
          </div>
        </SectionPanel>
      </section>

      <Toolbar
        left={
          <>
            <Input
              value={storageUnitName}
              onChange={(event) => setStorageUnitName(event.target.value)}
              placeholder="Storage unit or batch label"
              className="w-full sm:max-w-[280px]"
            />
            <label className="flex h-10 items-center gap-2 rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#475467]">
              <input type="checkbox" checked={overnightMode} onChange={(event) => setOvernightMode(event.target.checked)} />
              Queue as overnight batch
            </label>
            <div className="relative w-full sm:w-[180px] md:hidden">
              <select
                value={activeTab}
                onChange={(event) => setActiveTab(event.target.value)}
                className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              >
                {INTAKE_TABS.map((tab) => (
                  <option key={tab.value} value={tab.value}>
                    {tab.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
            </div>
          </>
        }
        right={
          <span>
            {activeTab === 'batches' ? batchRows.length : activeTab === 'grouped' ? groupedRows.length : ungroupedRows.length} visible
          </span>
        }
      />

      <Tabs
        className="hidden md:flex"
        items={[
          { value: 'batches', label: 'Batches', count: batchRows.length },
          { value: 'ungrouped', label: 'Ungrouped', count: ungroupedRows.length },
          { value: 'grouped', label: 'Grouped', count: groupedRows.length },
        ]}
        value={activeTab}
        onChange={setActiveTab}
      />

      {activeTab === 'batches' ? (
        <DataTable
          columns={[
            ...commonColumns.slice(0, 2),
            { key: 'photo_count', label: 'Photos' },
            commonColumns[2],
            commonColumns[3],
          ]}
          rows={batchRows}
          rowKey={(row) => row.id}
          emptyState={<EmptyState title="No batches yet" description="Import a zip batch or queue an overnight storage-unit intake to start." className="border-0 p-0 py-6" />}
        />
      ) : activeTab === 'grouped' ? (
        <DataTable
          columns={commonColumns}
          rows={groupedRows}
          rowKey={(row) => row.id}
          emptyState={<EmptyState title="No grouped items yet" description="Grouped item clusters will appear here after intake processing finishes." className="border-0 p-0 py-6" />}
        />
      ) : (
        <DataTable
          columns={commonColumns}
          rows={ungroupedRows}
          rowKey={(row) => row.id}
          emptyState={<EmptyState title="No ungrouped items" description="New single-photo intake items will appear here before they are grouped." className="border-0 p-0 py-6" />}
        />
      )}
    </AppShell>
  );
}

IntakePage.requireAuth = true;
