import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-2xl text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:pointer-events-none disabled:opacity-50 active:scale-[0.98]',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:brightness-110 shadow-soft',
        secondary: 'bg-muted text-foreground hover:bg-muted/80',
        ghost: 'text-muted-foreground hover:bg-muted/70 hover:text-foreground',
        outline: 'border border-border bg-background hover:bg-muted/60',
      },
      size: {
        default: 'h-11 px-5',
        sm: 'h-9 px-3 text-xs',
        lg: 'h-12 px-6 text-base',
        icon: 'h-11 w-11',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

export default function Button({ className, variant, size, ...props }) {
  return <button className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}
