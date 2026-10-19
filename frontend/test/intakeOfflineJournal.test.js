import { afterEach, describe, expect, it } from 'vitest';

import {
  __offlineJournal,
  exportIntakeJournal,
  loadIntakeOfflineState,
  pendingIntakeEvents,
  queueIntakeEvent,
  saveIntakeOfflineState,
  syncIntakeJournal,
} from '../lib/intakeOfflineJournal';

afterEach(() => {
  __offlineJournal.memoryStores.events.clear();
  __offlineJournal.memoryStores.state.clear();
});

describe('intake offline journal', () => {
  it('persists active tote state and replays queued events in order with durable ids', async () => {
    await saveIntakeOfflineState('active-tote', { code: 'AO9', containerUuid: 'tote-9' });
    await queueIntakeEvent({ id: 'event-1', type: 'capture_started', sessionId: 'session-a', payload: { tote: 'AO9' } });
    await queueIntakeEvent({ id: 'event-2', type: 'quantity_set', sessionId: 'session-a', payload: { quantity: 2 } });
    const delivered = [];

    const result = await syncIntakeJournal(async (event, headers) => delivered.push([event.id, headers['Idempotency-Key']]));

    expect(await loadIntakeOfflineState('active-tote')).toEqual({ code: 'AO9', containerUuid: 'tote-9' });
    expect(delivered).toEqual([['event-1', 'event-1'], ['event-2', 'event-2']]);
    expect(result).toEqual({ attempted: 2, synced: 2, failed: 0 });
    expect(await pendingIntakeEvents()).toEqual([]);
  });

  it('stops at a failed dependency and leaves it available for the next reconnect', async () => {
    await queueIntakeEvent({ id: 'first', type: 'capture_started' });
    await queueIntakeEvent({ id: 'second', type: 'package_closed' });
    const delivered = [];
    const result = await syncIntakeJournal(async (event) => {
      delivered.push(event.id);
      if (event.id === 'first') throw new Error('offline');
    });

    expect(result).toEqual({ attempted: 1, synced: 0, failed: 1 });
    expect(delivered).toEqual(['first']);
    expect((await pendingIntakeEvents()).map((event) => event.id)).toEqual(['first', 'second']);
  });

  it('exports a portable recovery snapshot without changing queued work', async () => {
    await saveIntakeOfflineState('active-tote', { code: 'AO10' });
    await queueIntakeEvent({ id: 'backup-event', type: 'photo_recorded', payload: { localMediaKey: 'photo-1' } });

    const backup = await exportIntakeJournal();

    expect(backup.format).toBe('posterpro-intake-offline-v1');
    expect(backup.state['active-tote']).toEqual({ code: 'AO10' });
    expect(backup.events).toHaveLength(1);
    expect((await pendingIntakeEvents()).map((event) => event.id)).toEqual(['backup-event']);
  });
});
