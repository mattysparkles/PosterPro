export default function StatusPill({ status }) {
  return <span className={`status ${status}`}>{status}</span>;
}
