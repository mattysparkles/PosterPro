export default function MetricCard({ label, value, detail }) {
  return (
    <div className="pp-card min-h-[132px] p-4">
      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{label}</p>
      <p className="mt-3 text-[28px] font-semibold tracking-[-0.03em] text-[#101828]">{value}</p>
      {detail ? <p className="mt-2 text-sm leading-6 text-[#667085]">{detail}</p> : null}
    </div>
  );
}
