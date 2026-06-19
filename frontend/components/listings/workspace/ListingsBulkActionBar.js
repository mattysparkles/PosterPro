import ActionBar from '../../ui/action-bar';

export default function ListingsBulkActionBar({ selectedCount, left, actions }) {
  if (!selectedCount) {
    return null;
  }

  return <ActionBar left={left} right={<div className="flex flex-wrap gap-2">{actions}</div>} />;
}
