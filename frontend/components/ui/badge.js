import { cn } from '../../lib/utils';

export default function Badge({ className, tone = 'default', ...props }) {
  const tones = {
    default: 'border border-[#e5e7eb] bg-[#f8fafc] text-[#475467]',
    success: 'border border-[#c7f0d8] bg-[#ecfdf3] text-[#027a48]',
    danger: 'border border-[#fecdd3] bg-[#fff1f3] text-[#be123c]',
    info: 'border border-[#bfdbfe] bg-[#eff6ff] text-[#1d4ed8]',
  };

  return <span className={cn('pp-chip px-3 py-1 text-xs font-semibold', tones[tone], className)} {...props} />;
}
