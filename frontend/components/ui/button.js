import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-2xl border text-sm font-semibold transition disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--pp-focus-ring)]/25',
  {
    variants: {
      variant: {
        default: 'border-transparent !bg-[var(--pp-primary)] !text-white shadow-[0_14px_32px_rgba(23,58,99,0.22)] hover:-translate-y-[1px] hover:!bg-[var(--pp-primary-hover)]',
        secondary: 'border-[var(--pp-border)] bg-[var(--pp-surface)] text-[var(--pp-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] hover:-translate-y-[1px] hover:bg-white',
        outline: 'border-[var(--pp-border)] bg-transparent text-[var(--pp-text)] hover:-translate-y-[1px] hover:border-[#b6a98d] hover:bg-white',
        ghost: 'border-transparent bg-transparent text-[var(--pp-shell-copy)] hover:bg-[var(--pp-shell-hover)] hover:text-[var(--pp-text)]',
        subtle: 'border-transparent bg-[var(--pp-primary-soft)] text-[var(--pp-primary)] hover:bg-[#cfdef3]',
        success: 'border-transparent bg-[var(--pp-success)] text-white hover:brightness-95',
        danger: 'border-transparent bg-[var(--pp-danger)] text-white hover:brightness-95',
      },
      size: {
        default: 'h-11 px-4.5',
        sm: 'h-9 px-3.5 text-xs',
        lg: 'h-12 px-5 text-base',
        icon: 'h-11 w-11',
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
    return <a href={href} target={target || (isExternal ? '_blank' : undefined)} rel={rel || (isExternal ? 'noreferrer' : undefined)} className={classes} {...props} />;
  }
  return <button type={type} className={classes} {...props} />;
}
