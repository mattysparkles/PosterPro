import { X } from 'lucide-react';
import { createPortal } from 'react-dom';

import Button from './button';

export default function Drawer({
  open,
  title,
  description,
  widthClassName = 'xl:w-[480px]',
  onClose,
  children,
  side = 'right',
}) {
  if (!open) return null;

  const sideClassName = side === 'left' ? 'left-0 right-auto border-r border-l-0' : 'right-0 left-auto border-l border-r-0';

  const drawer = (
    <>
      <div data-testid="drawer-backdrop" className="fixed inset-0 z-[9998] bg-[#101828]/35" onClick={onClose} aria-hidden="true" />
      <aside
        data-testid="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`fixed inset-y-0 z-[9999] w-full max-w-[480px] border-[#e5e7eb] bg-white shadow-2xl xl:max-w-none xl:rounded-[12px] xl:border ${sideClassName} ${widthClassName}`}
      >
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
  return typeof document === 'undefined' ? null : createPortal(drawer, document.body);
}
