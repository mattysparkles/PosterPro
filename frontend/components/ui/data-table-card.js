import { cn } from '../../lib/utils';

import DataTable from './data-table';

export default function DataTableCard({ title, description, action, className, tableClassName, ...tableProps }) {
  return (
    <section className={cn('overflow-hidden rounded-[16px] border border-[#e5e7eb] bg-white', className)}>
      {(title || description || action) ? (
        <div className="flex items-start justify-between gap-4 border-b border-[#e5e7eb] px-4 py-4 md:px-5">
          <div>
            {title ? <h2 className="text-base font-semibold text-[#101828]">{title}</h2> : null}
            {description ? <p className="mt-1 text-sm text-[#667085]">{description}</p> : null}
          </div>
          {action ? <div>{action}</div> : null}
        </div>
      ) : null}
      <DataTable {...tableProps} className={cn('rounded-none border-0', tableClassName)} />
    </section>
  );
}
