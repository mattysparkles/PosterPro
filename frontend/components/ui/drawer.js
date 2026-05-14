import { X } from 'lucide-react';

import Button from './button';

export default function Drawer({ open, title, description, widthClassName = 'xl:w-[480px]', onClose, children }) {
  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-40 bg-[#101828]/20 xl:hidden" onClick={onClose} />
      <aside className={`fixed inset-y-0 right-0 z-50 w-full max-w-[480px] border-l border-[#e5e7eb] bg-white xl:static xl:inset-auto xl:z-auto xl:max-w-none xl:rounded-[12px] xl:border ${widthClassName}`}>
        <div className="flex h-full flex-col">
          <div className="flex items-start justify-between gap-4 border-b border-[#e5e7eb] px-5 py-4">
            <div>
              <h2 className="text-base font-semibold text-[#101828]">{title}</h2>
              {description ? <p className="mt-1 text-sm text-[#667085]">{description}</p> : null}
            </div>
            <Button variant="ghost" size="icon" onClick={onClose} title="Close panel">
              <X size={16} />
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
        </div>
      </aside>
    </>
  );
}
