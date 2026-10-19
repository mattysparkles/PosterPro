import { cn } from '../../lib/utils';

export default function AppCard({ className, children, padded = true }) {
  return <section className={cn('pp-card', padded ? 'p-5' : '', className)}>{children}</section>;
}
