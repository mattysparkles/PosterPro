import Link from 'next/link';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-xl border text-sm font-semibold transition disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--pp-focus-ring)]/25',
  {
    variants: {
      variant: {
        default: 'border-transparent !bg-[var(--pp-primary)] !text-white shadow-[var(--pp-card-shadow)] hover:!bg-[var(--pp-primary-hover)]',
        secondary: 'border-[var(--pp-border)] bg-[var(--pp-surface-strong)] text-[var(--pp-text)] shadow-none hover:bg-white',
        outline: 'border-[var(--pp-border)] bg-transparent text-[var(--pp-text)] hover:border-[#b6c2d9] hover:bg-white',
        ghost: 'border-transparent bg-transparent text-[var(--pp-shell-copy)] hover:bg-[var(--pp-shell-hover)] hover:text-[var(--pp-text)]',
        subtle: 'border-transparent bg-[var(--pp-primary-soft)] text-[var(--pp-primary)] hover:bg-[#d3e1ff]',
        success: 'border-transparent bg-[var(--pp-success)] text-white hover:brightness-95',
        danger: 'border-transparent bg-[var(--pp-danger)] text-white hover:brightness-95',
      },
      size: {
        default: 'h-10 px-4',
        sm: 'h-8 px-3 text-xs',
        lg: 'h-11 px-5 text-base',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export default function Button({
  className,
  variant,
  size,
  type = 'button',
  href,
  external = false,
  target,
  rel,
  ...props
}) {
  const classes = cn(buttonVariants({ variant, size, className }));
  if (href) {
    const isExternal = external || /^https?:\/\//i.test(href) || href.startsWith('mailto:') || href.startsWith('tel:');
    if (isExternal) {
      return <a href={href} target={target || '_blank'} rel={rel || 'noreferrer'} className={classes} {...props} />;
    }
    return <Link href={href} className={classes} target={target} rel={rel} {...props} />;
  }
  return <button type={type} className={classes} {...props} />;
}
