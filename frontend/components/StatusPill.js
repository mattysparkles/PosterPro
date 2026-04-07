export default function StatusPill({ status }) {
  const normalized = (status || 'unknown').toLowerCase();
  return <span className={`status ${normalized}`}>{status}</span>;
}
