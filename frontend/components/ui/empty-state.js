import { cn } from '../../lib/utils';

export default function EmptyState({ icon: Icon, title, description, action, className }) {
  return (
    <div className={cn('pp-card flex flex-col items-center gap-3 border-dashed py-10 text-center', className)}>
      {Icon ? <Icon size={28} className="text-[#98a2b3]" /> : null}
      <h3 className="text-base font-semibold text-[#101828]">{title}</h3>
      <p className="max-w-md text-sm leading-6 text-[#667085]">{description}</p>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}
