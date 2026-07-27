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
  activeTab,
  setActiveTab,
  setSelectedIds,
  filteredListingsLength,
  viewMode,
  setViewMode,
  onBulkRetryFetchImages,
  onRetryMissingVineImages,
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
}) {
  return (
    <Toolbar
      left={
        <>
          <div className="shrink-0 rounded-full border border-[var(--pp-border)] bg-white px-3 py-2 text-xs font-semibold uppercase tracking-[0.14em] text-[var(--pp-shell-soft-copy)]">
            Listings workspace
          </div>
          <div className="relative w-full sm:max-w-[320px]">
            <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" size={16} />
            <Input placeholder="Search this page" className="pl-9" value={search} onChange={(event) => { setSearch(event.target.value); onFiltersChanged?.(); }} />
          </div>
          <div className="relative w-full sm:w-[220px]">
            <select
              value={marketFilter}
              onChange={(event) => { setMarketFilter(event.target.value); onFiltersChanged?.(); }}
              className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
            >
              {filterOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
          </div>
          <div className="relative w-full sm:w-[180px]">
            <select
              value={sourceFilter}
              onChange={(event) => { setSourceFilter(event.target.value); onFiltersChanged?.(); }}
              className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
            >
              {sourceOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
          </div>
          <div className="relative w-full sm:w-[220px]">
            <select
              value={readinessFilter}
              onChange={(event) => { setReadinessFilter(event.target.value); onFiltersChanged?.(); }}
              className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
            >
              {readinessFilterOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
          </div>
          <div className="relative w-full sm:w-[180px]">
            <select
              value={activeTab}
              onChange={(event) => {
                setActiveTab(event.target.value);
                setSelectedIds([]);
              }}
              className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12 md:hidden"
            >
              {listingTabs.map((tab) => (
                <option key={tab.value} value={tab.value}>
                  {tab.label}
                </option>
              ))}
            </select>
            <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3] md:hidden" />
          </div>
        </>
      }
      right={
        <div className="flex items-center gap-2">
          <span>{filteredListingsLength} visible</span>
          <label className="hidden items-center gap-2 text-xs font-medium text-[#475467] lg:flex">
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
          {sourceFilter === 'amazon_vine' ? (
            <>
              <Button size="sm" variant="outline" onClick={onBulkRetryFetchImages}>
                Bulk retry fetch images
              </Button>
              <Button size="sm" variant="secondary" onClick={onRetryMissingVineImages}>
                Retry missing Vine images
              </Button>
            </>
          ) : null}
          <div className="hidden rounded-[10px] border border-[#e5e7eb] bg-white p-1 md:flex">
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
