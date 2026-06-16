import Badge from './badge';

const STATUS_MAP = {
  draft: 'default',
  drafts: 'default',
  ready: 'success',
  published: 'success',
  posted: 'success',
  active: 'success',
  success: 'success',
  warning: 'warning',
  failed: 'danger',
  error: 'danger',
  sold: 'danger',
  closed: 'warning',
  blocked: 'danger',
  manual_only: 'warning',
  fetched: 'success',
  cached: 'success',
  missing_asin: 'warning',
  intake: 'info',
  queued: 'info',
  grouped: 'info',
  pending: 'info',
};

export default function StatusPill({ status, label }) {
  const normalized = String(status || label || 'default').toLowerCase();
  const tone = STATUS_MAP[normalized] || (normalized.includes('fail') ? 'danger' : normalized.includes('ready') || normalized.includes('post') ? 'success' : 'default');

  return <Badge tone={tone}>{label || status || 'Unknown'}</Badge>;
}
