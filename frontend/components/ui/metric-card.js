import Link from 'next/link';

export default function MetricCard({ label, value, detail, href }) {
  const className = `pp-card min-h-[132px] p-4 ${href ? 'transition hover:bg-[#f9fafb]' : ''}`;

  const content = (
    <>
      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{label}</p>
      <p className="mt-3 text-[28px] font-semibold tracking-[-0.03em] text-[#101828]">{value}</p>
      {detail ? <p className="mt-2 text-sm leading-6 text-[#667085]">{detail}</p> : null}
    </>
  );

  if (!href) {
    return <div className={className}>{content}</div>;
  }

  return (
    <Link href={href} className={`${className} block focus:outline-none focus-visible:ring-2 focus-visible:ring-[#2563eb]`}>
      {content}
    </Link>
  );
}
