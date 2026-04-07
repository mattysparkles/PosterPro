import { cn } from '../../lib/utils';

export default function Badge({ className, tone = 'default', ...props }) {
  const tones = {
    default: 'bg-muted text-foreground',
    success: 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-300',
    danger: 'bg-rose-500/20 text-rose-600 dark:text-rose-300',
    info: 'bg-sky-500/20 text-sky-600 dark:text-sky-300',
  };

  return <span className={cn('inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold', tones[tone], className)} {...props} />;
}
