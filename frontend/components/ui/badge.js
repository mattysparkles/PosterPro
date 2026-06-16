import { cn } from '../../lib/utils';

export default function Badge({ className, tone = 'default', ...props }) {
  const tones = {
    default: 'border border-[#d5dbe5] bg-[#f8fafc] text-[#344054]',
    success: 'border border-[#b8e3d5] bg-[#eefbf6] text-[#0f766e]',
    warning: 'border border-[#fedf9f] bg-[#fff7e6] text-[#b45309]',
    danger: 'border border-[#f7d4d0] bg-[#fef3f2] text-[#b42318]',
    info: 'border border-[#bfd2ff] bg-[#edf4ff] text-[#1d4ed8]',
  };

  return <span className={cn('pp-chip px-2.5 py-1 text-xs font-medium', tones[tone], className)} {...props} />;
}
