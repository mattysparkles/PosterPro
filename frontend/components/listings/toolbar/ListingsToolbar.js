import { ChevronDown, Grid2X2, List, Search } from 'lucide-react';

import Button from '../../ui/button';
import Input from '../../ui/input';
import Toolbar from '../../ui/toolbar';

export default function ListingsToolbar({
  search,
  setSearch,
  marketFilter,
  setMarketFilter,
  sourceFilter,
  setSourceFilter,
  readinessFilter,
  setReadinessFilter,
  sortBy,
  setSortBy,
  sortDir,
  setSortDir,
  activeTab,
  setActiveTab,
  setSelectedIds,
  filteredListingsLength,
  viewMode,
  setViewMode,
  onBulkRetryFetchImages,
  onForceRecentVineImages,
  onRetryMissingVineImages,
  onRefreshVineMetadata,
  onRepairAllVineImages,
  filterOptions,
  sourceOptions,
  readinessFilterOptions,
  listingTabs,
  onClearAllFilters,
  onFiltersChanged,
  catalogTotal,
  catalogPage,
  catalogPageSize,
  onCatalogPageSizeChange,
  workspaceMode,
  autoRefreshEnabled,
  setAutoRefreshEnabled,
}) {
  const activeTabLabel = listingTabs.find((tab) => tab.value === activeTab)?.label || activeTab;
  const activeFilterSummary = [
    activeTab !== 'all' ? `Tab: ${activeTabLabel}` : null,
    marketFilter !== 'all' ? `Market: ${marketFilter}` : null,
    sourceFilter !== 'all' ? `Source: ${sourceFilter}` : null,
    readinessFilter !== 'all' ? `Readiness: ${readinessFilter}` : null,
  ].filter(Boolean);
  const urlStateSummary = [
    activeTab !== 'all' ? `tab=${activeTab}` : null,
    catalogPage > 1 ? `page=${catalogPage}` : null,
    catalogPageSize !== 25 ? `page_size=${catalogPageSize}` : null,
    search ? `q=${search}` : null,
    marketFilter !== 'all' ? `market=${marketFilter}` : null,
    sourceFilter !== 'all' ? `source=${sourceFilter}` : null,
    readinessFilter !== 'all' ? `readiness=${readinessFilter}` : null,
    sortBy !== 'updated' ? `sort=${sortBy}:${sortDir}` : null,
    workspaceMode && workspaceMode !== 'results' ? `workspace=${workspaceMode}` : null,
    viewMode !== 'table' ? `view=${viewMode}` : null,
    autoRefreshEnabled ? 'refresh=1' : null,
  ].filter(Boolean);

  return (
    <Toolbar
      className="items-start"
      left={
        <div className="flex w-full flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="shrink-0 rounded-full border border-[var(--pp-border)] bg-white px-3 py-2 text-xs font-semibold uppercase tracking-[0.14em] text-[var(--pp-shell-soft-copy)]">
              Listings workspace
            </span>
            {activeFilterSummary.length ? (
              <div className="flex flex-wrap gap-1.5">
                {activeFilterSummary.map((label) => (
                  <span key={label} className="rounded-full border border-[#dbe4f0] bg-[#f8fbff] px-2.5 py-1 text-[11px] font-semibold text-[#475467]">
                    {label}
                  </span>
                ))}
              </div>
            ) : (
              <span className="text-xs font-medium text-[#667085]">Showing the full catalog view.</span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full border border-[#dbe4f0] bg-white px-2.5 py-1 font-semibold text-[#475467]">
              URL state
            </span>
            {urlStateSummary.length ? (
              <div className="flex flex-wrap gap-1.5">
                {urlStateSummary.map((label) => (
                  <span key={label} className="rounded-full bg-[#f8fbff] px-2.5 py-1 font-semibold text-[#2563eb]">
                    {label}
                  </span>
                ))}
              </div>
            ) : (
              <span className="text-[#667085]">default state</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 rounded-[14px] border border-amber-200 bg-amber-50 px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-amber-800">Source filter</span>
            <button type="button" onClick={() => { setSourceFilter('all'); onFiltersChanged?.(); }} className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${sourceFilter === 'all' ? 'border-slate-400 bg-white text-slate-900' : 'border-transparent bg-white/60 text-slate-600'}`}>All sources</button>
            <button type="button" onClick={() => { setSourceFilter('amazon_vine'); onFiltersChanged?.(); }} className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${sourceFilter === 'amazon_vine' ? 'border-amber-500 bg-white text-amber-900' : 'border-transparent bg-white/60 text-amber-800'}`}>Amazon Vine</button>
          </div>

          <div className="flex flex-col gap-3 xl:flex-row xl:flex-wrap xl:items-center">
            <div className="grid w-full gap-2 sm:max-w-[320px]">
              <label className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#667085]">Search</label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" size={16} />
                <Input placeholder="Search this page" className="pl-9" value={search} onChange={(event) => { setSearch(event.target.value); onFiltersChanged?.(); }} />
              </div>
            </div>
            <div className="grid w-full gap-2 sm:w-[220px]">
              <label className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#667085]">Marketplace destination</label>
              <div className="relative">
                <select
                  value={marketFilter}
                  onChange={(event) => { setMarketFilter(event.target.value); onFiltersChanged?.(); }}
                  className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  aria-label="Marketplace destination"
                >
                  {filterOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
              </div>
            </div>
            <div className="grid w-full gap-2 sm:w-[220px]">
              <label className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#667085]">Source</label>
              <div className="relative">
                <select
                  value={sourceFilter}
                  onChange={(event) => { setSourceFilter(event.target.value); onFiltersChanged?.(); }}
                  className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  aria-label="Source"
                >
                  {sourceOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
              </div>
            </div>
            <div className="grid w-full gap-2 sm:w-[220px]">
              <label className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#667085]">Readiness warning</label>
              <div className="relative">
                <select
                  value={readinessFilter}
                  onChange={(event) => { setReadinessFilter(event.target.value); onFiltersChanged?.(); }}
                  className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  aria-label="Readiness warning"
                >
                  {readinessFilterOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
              </div>
            </div>
            <div className="grid w-full gap-2 sm:w-[170px]">
              <label className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#667085]">Sort</label>
              <div className="relative">
                <select
                  value={sortBy}
                  onChange={(event) => { setSortBy(event.target.value); onFiltersChanged?.(); }}
                  className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  aria-label="Sort listings"
                >
                  {[
                    { value: 'updated', label: 'Updated' },
                    { value: 'created', label: 'Created' },
                    { value: 'price', label: 'Price' },
                    { value: 'title', label: 'Title' },
                    { value: 'source', label: 'Source' },
                    { value: 'status', label: 'Status' },
                  ].map((option) => (
                    <option key={option.value} value={option.value}>
                      Sort: {option.label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
              </div>
            </div>
            <Button size="sm" variant="outline" onClick={() => { setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); onFiltersChanged?.(); }}>
              {sortDir === 'asc' ? 'Asc' : 'Desc'}
            </Button>
            <div className="relative w-full sm:w-[180px]">
              <select
                value={activeTab}
                onChange={(event) => {
                  setActiveTab(event.target.value);
                  setSelectedIds([]);
                }}
                className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              >
                {listingTabs.map((tab) => (
                  <option key={tab.value} value={tab.value}>
                    {tab.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
            </div>
          </div>
        </div>
      }
      right={
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span>{filteredListingsLength} visible</span>
          <label className="flex items-center gap-2 text-xs font-medium text-[#475467]">
            Per page
            <select
              value={catalogPageSize}
              onChange={(event) => onCatalogPageSizeChange?.(Number(event.target.value))}
              className="h-8 rounded-[8px] border border-[#e5e7eb] bg-white px-2 text-xs text-[#101828]"
              aria-label="Listings per page"
            >
              {[25, 50, 100, 250].map((size) => <option key={size} value={size}>{size}</option>)}
            </select>
            <span className="text-[#667085]">of {catalogTotal}</span>
          </label>
          <Button size="sm" variant="outline" onClick={onClearAllFilters}>
            Clear all filters
          </Button>
          <Button
            size="sm"
            variant={autoRefreshEnabled ? 'secondary' : 'outline'}
            onClick={() => setAutoRefreshEnabled?.(!autoRefreshEnabled)}
          >
            Auto refresh {autoRefreshEnabled ? 'on' : 'off'}
          </Button>
          {sourceFilter === 'amazon_vine' ? (
            <>
              <Button size="sm" variant="secondary" onClick={onForceRecentVineImages}>
                Force Vine image backfill
              </Button>
              <Button size="sm" variant="outline" onClick={onRefreshVineMetadata}>
                Refresh Vine categories
              </Button>
              <Button size="sm" variant="outline" onClick={onRepairAllVineImages}>
                Repair all Vine images
              </Button>
              <Button size="sm" variant="outline" onClick={onBulkRetryFetchImages}>
                Bulk retry fetch images
              </Button>
              <Button size="sm" variant="secondary" onClick={onRetryMissingVineImages}>
                Retry missing Vine images
              </Button>
            </>
          ) : null}
          <div className="flex rounded-[10px] border border-[#e5e7eb] bg-white p-1">
            <button
              type="button"
              onClick={() => setViewMode('table')}
              className={`inline-flex h-8 items-center gap-2 rounded-[8px] px-3 text-xs font-medium ${
                viewMode === 'table' ? 'bg-[#eef4ff] text-[#2563eb]' : 'text-[#667085]'
              }`}
            >
              <List size={14} />
              Table
            </button>
            <button
              type="button"
              onClick={() => setViewMode('grid')}
              className={`inline-flex h-8 items-center gap-2 rounded-[8px] px-3 text-xs font-medium ${
                viewMode === 'grid' ? 'bg-[#eef4ff] text-[#2563eb]' : 'text-[#667085]'
              }`}
            >
              <Grid2X2 size={14} />
              Grid
            </button>
          </div>
        </div>
      }
    />
  );
}
