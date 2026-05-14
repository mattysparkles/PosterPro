import { cn } from '../../lib/utils';

export default function Input({ className, ...props }) {
  return (
    <input
      className={cn(
        'pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12',
        className
      )}
      {...props}
    />
  );
}
