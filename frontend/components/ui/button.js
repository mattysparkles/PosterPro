import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-[10px] border text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[#2563eb]/12 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'pp-button-primary',
        secondary: 'border-[#e5e7eb] bg-[#f9fafb] text-[#101828] hover:bg-white',
        ghost: 'border-transparent bg-transparent text-[#667085] hover:bg-[#f9fafb] hover:text-[#101828]',
        outline: 'pp-button-secondary hover:bg-[#f9fafb]',
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
  }
);

export default function Button({ className, variant, size, ...props }) {
  return <button className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}
