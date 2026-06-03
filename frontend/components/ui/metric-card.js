import { cn } from '../../lib/utils';

export default function MetricCard({ label, value, detail, className }) {
  return (
    <div className={cn('pp-card min-h-[132px] p-5', className)}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#667085]">{label}</p>
      <p className="mt-2.5 text-[30px] font-semibold leading-none tracking-[-0.03em] text-[#101828]">{value}</p>
      {detail ? <p className="mt-2 text-sm leading-6 text-[#667085]">{detail}</p> : null}
    </div>
  );
}
