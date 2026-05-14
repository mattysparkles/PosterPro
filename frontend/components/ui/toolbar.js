import { cn } from '../../lib/utils';

export default function Toolbar({ left, right, className }) {
  return (
    <div className={cn('pp-toolbar', className)}>
      <div className="flex min-w-0 flex-1 flex-col gap-3 lg:flex-row lg:items-center">{left}</div>
      {right ? <div className="flex items-center gap-2 text-sm text-[#667085]">{right}</div> : null}
    </div>
  );
}
