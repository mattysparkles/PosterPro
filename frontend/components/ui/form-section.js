import { cn } from '../../lib/utils';

export default function FormSection({ title, description, children, className }) {
  return (
    <section className={cn('rounded-[16px] border border-[#e5e7eb] bg-[#fcfcfd] p-4', className)}>
      {(title || description) ? (
        <div className="mb-4 border-b border-[#eaecf0] pb-4">
          {title ? <h3 className="text-sm font-semibold text-[#101828]">{title}</h3> : null}
          {description ? <p className="mt-1 text-sm leading-6 text-[#667085]">{description}</p> : null}
        </div>
      ) : null}
      <div className="space-y-4">{children}</div>
    </section>
  );
}
