import { cva } from 'class-variance-authority';
import Link from 'next/link';

import { cn } from '../../lib/utils';

const buttonVariants = cva(
  'pp-button inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border px-4 text-sm font-semibold leading-5 transition-[background-color,border-color,color,box-shadow,transform] duration-150 disabled:pointer-events-none disabled:opacity-55 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--pp-focus-ring)]/25 active:translate-y-px',
  {
    variants: {
      variant: {
        default: 'border-transparent !bg-[var(--pp-primary)] !text-[var(--pp-primary-contrast)] [--pp-button-fg:var(--pp-primary-contrast)] shadow-[var(--pp-shadow-sm)] hover:-translate-y-px hover:!bg-[var(--pp-primary-hover)] active:!bg-[var(--pp-primary-active)]',
        primary: 'border-transparent !bg-[var(--pp-primary)] !text-[var(--pp-primary-contrast)] [--pp-button-fg:var(--pp-primary-contrast)] shadow-[var(--pp-shadow-sm)] hover:-translate-y-px hover:!bg-[var(--pp-primary-hover)] active:!bg-[var(--pp-primary-active)]',
        secondary: 'border-[var(--pp-border-strong)] !bg-white !text-[var(--pp-text-primary)] [--pp-button-fg:var(--pp-text-primary)] shadow-[var(--pp-shadow-sm)] hover:-translate-y-px hover:!bg-slate-50 hover:border-slate-400',
        outline: 'border-[var(--pp-border-strong)] !bg-white !text-[var(--pp-text-primary)] [--pp-button-fg:var(--pp-text-primary)] shadow-[var(--pp-shadow-sm)] hover:-translate-y-px hover:!bg-slate-50 hover:border-slate-400',
        tertiary: 'border-transparent !bg-transparent !text-[var(--pp-primary)] [--pp-button-fg:var(--pp-primary)] hover:!bg-[var(--pp-info-bg)]',
        ghost: 'border-transparent !bg-transparent !text-[var(--pp-shell-copy)] [--pp-button-fg:var(--pp-shell-copy)] hover:!bg-[var(--pp-shell-hover)] hover:!text-[var(--pp-text)] hover:[--pp-button-fg:var(--pp-text)]',
        subtle: 'border-transparent !bg-[var(--pp-primary-soft)] !text-[var(--pp-primary)] [--pp-button-fg:var(--pp-primary)] hover:!bg-[#cfdef3]',
        success: 'border-transparent !bg-[var(--pp-success)] !text-white [--pp-button-fg:#ffffff] hover:brightness-95',
        danger: 'border-transparent !bg-[var(--pp-danger)] !text-white [--pp-button-fg:#ffffff] hover:brightness-95',
      },
      size: {
        default: 'h-11 px-4',
        sm: 'h-10 px-3.5 text-sm',
        lg: 'h-12 px-5 text-base',
        icon: 'h-11 w-11 min-w-11 px-0',
        'icon-sm': 'h-10 w-10 min-h-10 min-w-10 px-0',
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
  style,
  ...props
}) {
  const classes = cn(buttonVariants({ variant, size, className }));
  const resolvedStyle = {
    color: 'var(--pp-button-fg, currentColor)',
    ...(style || {}),
  };
  if (href) {
    const isExternal = external || /^https?:\/\//i.test(href) || href.startsWith('mailto:') || href.startsWith('tel:');
    if (!isExternal) {
      return (
        <Link
          href={href}
          className={classes}
          style={resolvedStyle}
          {...props}
        />
      );
    }
    return (
      <a
        href={href}
        target={target || (isExternal ? '_blank' : undefined)}
        rel={rel || (isExternal ? 'noreferrer' : undefined)}
        className={classes}
        style={resolvedStyle}
        {...props}
      />
    );
  }
  return <button type={type} className={classes} style={resolvedStyle} {...props} />;
}
