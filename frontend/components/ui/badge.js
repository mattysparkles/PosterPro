import { cn } from '../../lib/utils';

export default function Badge({ className, tone = 'default', ...props }) {
  const tones = {
    default: 'border border-[#e5e7eb] bg-[#f9fafb] text-[#475467]',
    success: 'border border-[#ccebd8] bg-[#ecfdf3] text-[#067647]',
    warning: 'border border-[#fde68a] bg-[#fffaeb] text-[#b54708]',
    danger: 'border border-[#f7d4d0] bg-[#fef3f2] text-[#b42318]',
    info: 'border border-[#dbe7ff] bg-[#eef4ff] text-[#2563eb]',
  };

  return <span className={cn('pp-chip px-2.5 py-1 text-xs font-medium', tones[tone], className)} {...props} />;
}
