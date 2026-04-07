import Badge from './ui/badge';

export default function StatusPill({ status }) {
  const normalized = (status || 'unknown').toLowerCase();
  const tone =
    normalized.includes('post') || normalized.includes('publish')
      ? 'success'
      : normalized.includes('fail') || normalized.includes('reject')
        ? 'danger'
        : 'info';

  return <Badge tone={tone}>{status || 'UNKNOWN'}</Badge>;
}
