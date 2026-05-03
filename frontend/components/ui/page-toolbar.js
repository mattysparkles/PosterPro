import { cn } from '../../lib/utils';

export default function PageToolbar({ left, right, className }) {
  return (
    <div className={cn('pp-card flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between', className)}>
      <div className="min-w-0">{left}</div>
      <div className="flex flex-wrap items-center gap-2">{right}</div>
    </div>
  );
}
