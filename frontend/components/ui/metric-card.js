export default function MetricCard({ label, value, detail }) {
  return (
    <div className="pp-card p-5">
      <p className="text-xs font-bold uppercase tracking-[0.18em] text-[#98a2b3]">{label}</p>
      <p className="mt-3 text-3xl font-semibold tracking-[-0.04em] text-[#111827]">{value}</p>
      {detail ? <p className="mt-2 text-sm leading-6 text-[#667085]">{detail}</p> : null}
    </div>
  );
}
