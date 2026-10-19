import EmptyState from '../../ui/empty-state';

export default function ListingsWorkspaceEmptyState({ activeTabLabel, compact = false }) {
  return (
    <EmptyState
      title={`No ${activeTabLabel || 'listings'} found`}
      description="Adjust the current filters or import new inventory to create drafts."
      className={compact ? 'border-0 p-0 py-6' : undefined}
    />
  );
}
