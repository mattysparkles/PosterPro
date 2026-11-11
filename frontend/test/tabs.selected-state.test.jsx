import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { Tabs } from '../components/ui/tabs';

const analyticsTabs = [
  { value: 'revenue', label: 'Revenue' },
  { value: 'profit', label: 'Profit' },
  { value: 'categories', label: 'Categories' },
  { value: 'pricing', label: 'Pricing' },
  { value: 'performance', label: 'Performance' },
];
const inventoryTabs = [
  { value: 'active', label: 'Active' },
  { value: 'intake', label: 'Intake' },
  { value: 'sold', label: 'Sold' },
  { value: 'stale', label: 'Stale' },
  { value: 'multi', label: 'Multi-quantity' },
  { value: 'batches', label: 'Batches' },
];

function TabHarness({ items }) {
  const [selected, setSelected] = useState(items[0].value);
  return <Tabs items={items} value={selected} onChange={setSelected} />;
}

describe('shared tabs selected state', () => {
  it.each([
    ['Analytics', analyticsTabs],
    ['Inventory', inventoryTabs],
  ])('%s tabs remain labeled and expose one selected tab after each selection', (_name, items) => {
    render(<TabHarness items={items} />);

    for (const item of items) {
      const tab = screen.getByRole('tab', { name: item.label });
      fireEvent.click(tab);
      expect(tab).toHaveAttribute('aria-selected', 'true');
      expect(tab).toHaveClass('is-active');
      expect(tab).toHaveTextContent(item.label);
      expect(screen.getAllByRole('tab', { selected: true })).toHaveLength(1);
    }
  });
});
