import { cn } from '../../lib/utils';

export default function Badge({ className, tone = 'default', ...props }) {
  const tones = {
    default: 'border border-[#d7d0c2] bg-[#f7f1e7] text-[#5f5849]',
    success: 'border border-[#b8e3d5] bg-[#eefbf6] text-[#0f766e]',
    warning: 'border border-[#f1d0a1] bg-[#fff4df] text-[#9a5c10]',
    danger: 'border border-[#f0c3bc] bg-[#fef1ef] text-[#b42318]',
    info: 'border border-[#c8d7f2] bg-[#edf4ff] text-[#173a63]',
  };

  return <span className={cn('pp-pill', tones[tone], className)} {...props} />;
}
