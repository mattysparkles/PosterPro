import { Info } from 'lucide-react';

import { cn } from '../../lib/utils';

export default function HelpTip({ label = 'More info', children, className, align = 'right' }) {
  return (
    <span className={cn('pp-help-tip', className)}>
      <button type="button" className="pp-help-tip__trigger" aria-label={label}>
        <Info size={14} />
      </button>
      <span
        className={cn(
          'pp-help-tip__bubble',
          align === 'left' ? 'pp-help-tip__bubble--left' : 'pp-help-tip__bubble--right',
        )}
        role="tooltip"
      >
        {children}
      </span>
    </span>
  );
}
