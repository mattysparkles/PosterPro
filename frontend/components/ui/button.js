import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-full border text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[#2563eb]/15 disabled:pointer-events-none disabled:opacity-50 active:scale-[0.99]',
  {
    variants: {
      variant: {
        default: 'pp-button-primary',
        secondary: 'border-[#e5e7eb] bg-[#f8fafc] text-[#111827] hover:bg-white',
        ghost: 'border-transparent bg-transparent text-[#667085] hover:bg-white hover:text-[#111827]',
        outline: 'pp-button-secondary hover:bg-[#f8fafc]',
      },
      size: {
        default: 'h-12 px-5',
        sm: 'h-10 px-3.5 text-xs',
        lg: 'h-12 px-6 text-base',
        icon: 'h-12 w-12',
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
