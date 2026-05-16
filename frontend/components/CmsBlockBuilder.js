import Button from './ui/button';
import Input from './ui/input';

function defaultBlock(type) {
  switch (type) {
    case 'steps':
      return { type: 'steps', items: ['First step', 'Second step'] };
    case 'feature_list':
      return {
        type: 'feature_list',
        items: [
          { title: 'Feature one', body: 'Explain the first value point.' },
          { title: 'Feature two', body: 'Explain the second value point.' },
        ],
      };
    case 'cta':
      return {
        type: 'cta',
        title: 'Call to action',
        body: 'Explain why the visitor should continue.',
        button: { label: 'Continue', href: '/' },
      };
    case 'rich_text':
    default:
      return { type: 'rich_text', html: '<p>Add your content here.</p>' };
  }
}

function BlockCard({ title, subtitle, children, actions }) {
  return (
    <div className="rounded-[14px] border border-[#d0d5dd] bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-[#101828]">{title}</p>
          {subtitle ? <p className="mt-1 text-sm text-[#667085]">{subtitle}</p> : null}
        </div>
        <div className="flex flex-wrap gap-2">{actions}</div>
      </div>
      <div className="mt-4 space-y-3">{children}</div>
    </div>
  );
}

export default function CmsBlockBuilder({ blocks = [], onChange }) {
  const updateBlock = (index, nextBlock) => {
    onChange(blocks.map((block, blockIndex) => (blockIndex === index ? nextBlock : block)));
  };

  const removeBlock = (index) => {
    onChange(blocks.filter((_, blockIndex) => blockIndex !== index));
  };

  const moveBlock = (index, direction) => {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= blocks.length) return;
    const next = [...blocks];
    [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
    onChange(next);
  };

  const addBlock = (type) => {
    onChange([...(blocks || []), defaultBlock(type)]);
  };

  return (
    <div className="space-y-4">
      {blocks.length ? (
        <div className="space-y-3">
          {blocks.map((block, index) => {
            const type = String(block?.type || 'rich_text');
            const actions = (
              <>
                <Button type="button" size="sm" variant="ghost" disabled={index === 0} onClick={() => moveBlock(index, -1)}>
                  Up
                </Button>
                <Button type="button" size="sm" variant="ghost" disabled={index === blocks.length - 1} onClick={() => moveBlock(index, 1)}>
                  Down
                </Button>
                <Button type="button" size="sm" variant="outline" onClick={() => removeBlock(index)}>
                  Remove
                </Button>
              </>
            );

            if (type === 'rich_text') {
              return (
                <BlockCard key={`${type}-${index}`} title={`Rich Text ${index + 1}`} subtitle="HTML-enabled content block for policy copy or explanation." actions={actions}>
                  <textarea
                    value={block.html || ''}
                    onChange={(event) => updateBlock(index, { ...block, html: event.target.value })}
                    className="min-h-40 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  />
                </BlockCard>
              );
            }

            if (type === 'steps') {
              const items = Array.isArray(block.items) ? block.items : [];
              return (
                <BlockCard key={`${type}-${index}`} title={`Steps ${index + 1}`} subtitle="Ordered onboarding or workflow sequence." actions={actions}>
                  {items.map((item, itemIndex) => (
                    <div key={`${index}-step-${itemIndex}`} className="flex gap-3">
                      <div className="mt-2 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[#eff6ff] text-xs font-semibold text-[#1d4ed8]">
                        {itemIndex + 1}
                      </div>
                      <div className="flex-1 space-y-2">
                        <textarea
                          value={item || ''}
                          onChange={(event) =>
                            updateBlock(index, {
                              ...block,
                              items: items.map((entry, entryIndex) => (entryIndex === itemIndex ? event.target.value : entry)),
                            })
                          }
                          className="min-h-20 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                        />
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            updateBlock(index, {
                              ...block,
                              items: items.filter((_, entryIndex) => entryIndex !== itemIndex),
                            })
                          }
                        >
                          Remove step
                        </Button>
                      </div>
                    </div>
                  ))}
                  <Button type="button" size="sm" variant="outline" onClick={() => updateBlock(index, { ...block, items: [...items, 'New step'] })}>
                    Add step
                  </Button>
                </BlockCard>
              );
            }

            if (type === 'feature_list') {
              const items = Array.isArray(block.items) ? block.items : [];
              return (
                <BlockCard key={`${type}-${index}`} title={`Feature Cards ${index + 1}`} subtitle="Grid of short value statements or trust highlights." actions={actions}>
                  <div className="space-y-3">
                    {items.map((item, itemIndex) => (
                      <div key={`${index}-feature-${itemIndex}`} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                        <div className="grid gap-3 md:grid-cols-2">
                          <div className="space-y-2">
                            <label className="text-sm font-medium text-[#101828]">Feature title</label>
                            <Input
                              value={item?.title || ''}
                              onChange={(event) =>
                                updateBlock(index, {
                                  ...block,
                                  items: items.map((entry, entryIndex) =>
                                    entryIndex === itemIndex ? { ...entry, title: event.target.value } : entry
                                  ),
                                })
                              }
                            />
                          </div>
                          <div className="space-y-2">
                            <label className="text-sm font-medium text-[#101828]">Feature body</label>
                            <textarea
                              value={item?.body || ''}
                              onChange={(event) =>
                                updateBlock(index, {
                                  ...block,
                                  items: items.map((entry, entryIndex) =>
                                    entryIndex === itemIndex ? { ...entry, body: event.target.value } : entry
                                  ),
                                })
                              }
                              className="min-h-24 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                            />
                          </div>
                        </div>
                        <div className="mt-3">
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              updateBlock(index, {
                                ...block,
                                items: items.filter((_, entryIndex) => entryIndex !== itemIndex),
                              })
                            }
                          >
                            Remove feature
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      updateBlock(index, {
                        ...block,
                        items: [...items, { title: 'New feature', body: 'Explain the next value point.' }],
                      })
                    }
                  >
                    Add feature
                  </Button>
                </BlockCard>
              );
            }

            const button = block.button || {};
            return (
              <BlockCard key={`${type}-${index}`} title={`CTA ${index + 1}`} subtitle="Conversion-oriented banner with one nested button." actions={actions}>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">CTA title</label>
                    <Input value={block.title || ''} onChange={(event) => updateBlock(index, { ...block, title: event.target.value })} />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Button label</label>
                    <Input
                      value={button.label || ''}
                      onChange={(event) => updateBlock(index, { ...block, button: { ...button, label: event.target.value } })}
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">CTA body</label>
                  <textarea
                    value={block.body || ''}
                    onChange={(event) => updateBlock(index, { ...block, body: event.target.value })}
                    className="min-h-24 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Button href</label>
                  <Input
                    value={button.href || ''}
                    onChange={(event) => updateBlock(index, { ...block, button: { ...button, href: event.target.value } })}
                  />
                </div>
              </BlockCard>
            );
          })}
        </div>
      ) : (
        <div className="rounded-[14px] border border-dashed border-[#d0d5dd] bg-white p-4 text-sm text-[#667085]">
          No blocks yet. Add a rich text section, steps, feature cards, or a CTA to start building the page.
        </div>
      )}

      <div className="rounded-[14px] border border-[#d0d5dd] bg-[#fcfcfd] p-4">
        <p className="text-sm font-semibold text-[#101828]">Add block</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => addBlock('rich_text')}>
            Rich text
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => addBlock('steps')}>
            Steps
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => addBlock('feature_list')}>
            Feature cards
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => addBlock('cta')}>
            CTA
          </Button>
        </div>
      </div>
    </div>
  );
}
