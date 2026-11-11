const DB_NAME = 'posterpro-intake-offline';
const DB_VERSION = 1;
const EVENTS_STORE = 'events';
const STATE_STORE = 'state';

function isBrowser() {
  return typeof window !== 'undefined';
}

// Older embedded WebViews occasionally expose no IndexedDB. This fallback is
// intentionally process-local (and therefore not advertised as offline-safe),
// but preserves a usable workstation rather than throwing on every scan.
const memoryStores = { [EVENTS_STORE]: new Map(), [STATE_STORE]: new Map() };

function openDatabase() {
  if (!isBrowser()) return Promise.reject(new Error('Offline storage is only available in a browser.'));
  if (!window.indexedDB) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.open(DB_NAME, DB_VERSION);
    request.onerror = () => reject(request.error || new Error('Unable to open local intake storage.'));
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(EVENTS_STORE)) {
        const store = db.createObjectStore(EVENTS_STORE, { keyPath: 'id' });
        store.createIndex('status', 'status', { unique: false });
        store.createIndex('createdAt', 'createdAt', { unique: false });
      }
      if (!db.objectStoreNames.contains(STATE_STORE)) db.createObjectStore(STATE_STORE, { keyPath: 'key' });
    };
    request.onsuccess = () => resolve(request.result);
  });
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('Local storage request failed.'));
  });
}

async function withStore(storeName, mode, callback) {
  const db = await openDatabase();
  if (!db) {
    const store = {
      get: (key) => ({ result: memoryStores[storeName].get(key), onsuccess: null, onerror: null }),
      put: (value) => ({ result: memoryStores[storeName].set(value.id || value.key, value), onsuccess: null, onerror: null }),
      getAll: () => ({ result: [...memoryStores[storeName].values()], onsuccess: null, onerror: null }),
    };
    // The IndexedDB helper waits for request callbacks; use direct values in
    // the small compatibility store instead.
    return callback({
      get: async (key) => memoryStores[storeName].get(key),
      put: async (value) => { memoryStores[storeName].set(value.id || value.key, value); return value; },
      getAll: async () => [...memoryStores[storeName].values()],
    }, true);
  }
  try {
    const transaction = db.transaction(storeName, mode);
    const result = await callback(transaction.objectStore(storeName));
    await new Promise((resolve, reject) => {
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error || new Error('Local storage transaction failed.'));
      transaction.onabort = () => reject(transaction.error || new Error('Local storage transaction was aborted.'));
    });
    return result;
  } finally {
    db.close();
  }
}

function journalId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `offline-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** A durable, idempotent journal for capture actions performed without a network. */
export async function queueIntakeEvent({ type, payload = {}, sessionId = null, id = null }) {
  if (!String(type || '').trim()) throw new Error('An offline event type is required.');
  const event = {
    id: id || journalId(),
    type: String(type).trim(),
    payload,
    sessionId: sessionId || null,
    status: 'pending',
    attempts: 0,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    error: null,
  };
  await withStore(EVENTS_STORE, 'readwrite', (store, memory) => memory ? store.put(event) : requestResult(store.put(event)));
  return event;
}

export async function pendingIntakeEvents() {
  const events = await withStore(EVENTS_STORE, 'readonly', (store, memory) => memory ? store.getAll() : requestResult(store.getAll()));
  return events.filter((event) => event.status === 'pending' || event.status === 'failed').sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export async function updateIntakeEvent(id, patch) {
  return withStore(EVENTS_STORE, 'readwrite', async (store, memory) => {
    const current = memory ? await store.get(id) : await requestResult(store.get(id));
    if (!current) return null;
    const next = { ...current, ...patch, id: current.id, updatedAt: new Date().toISOString() };
    if (memory) await store.put(next); else await requestResult(store.put(next));
    return next;
  });
}

export async function completeIntakeEvent(id) {
  return updateIntakeEvent(id, { status: 'synced', error: null });
}

export async function failIntakeEvent(id, error) {
  return withStore(EVENTS_STORE, 'readwrite', async (store, memory) => {
    const current = memory ? await store.get(id) : await requestResult(store.get(id));
    if (!current) return null;
    const next = {
      ...current,
      status: 'failed',
      attempts: Number(current.attempts || 0) + 1,
      error: String(error || 'Sync failed.').slice(0, 500),
      updatedAt: new Date().toISOString(),
    };
    if (memory) await store.put(next); else await requestResult(store.put(next));
    return next;
  });
}

export async function saveIntakeOfflineState(key, value) {
  if (!String(key || '').trim()) throw new Error('An offline state key is required.');
  await withStore(STATE_STORE, 'readwrite', (store, memory) => memory ? store.put({ key, value, updatedAt: new Date().toISOString() }) : requestResult(store.put({ key, value, updatedAt: new Date().toISOString() })));
  return value;
}

export async function loadIntakeOfflineState(key) {
  const record = await withStore(STATE_STORE, 'readonly', (store, memory) => memory ? store.get(key) : requestResult(store.get(key)));
  return record ? record.value : null;
}

/** A portable, operator-downloadable snapshot for an interrupted field session. */
export async function exportIntakeJournal() {
  const [events, state] = await Promise.all([
    withStore(EVENTS_STORE, 'readonly', (store, memory) => memory ? store.getAll() : requestResult(store.getAll())),
    withStore(STATE_STORE, 'readonly', (store, memory) => memory ? store.getAll() : requestResult(store.getAll())),
  ]);
  return {
    format: 'posterpro-intake-offline-v1',
    exportedAt: new Date().toISOString(),
    events: events.sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
    state: Object.fromEntries(state.map((record) => [record.key, record.value])),
  };
}

/**
 * Replays local operations in capture order. The sender receives the local id
 * as an idempotency key, so a reload after a successful request cannot add
 * stock or duplicate a capture group.
 */
export async function syncIntakeJournal(sendEvent) {
  if (typeof sendEvent !== 'function') throw new Error('A sync sender is required.');
  const events = await pendingIntakeEvents();
  const result = { attempted: 0, synced: 0, failed: 0 };
  for (const event of events) {
    result.attempted += 1;
    try {
      await sendEvent(event, { 'Idempotency-Key': event.id });
      await completeIntakeEvent(event.id);
      result.synced += 1;
    } catch (error) {
      await failIntakeEvent(event.id, error?.message || error);
      result.failed += 1;
      // Events are ordered deliberately: later movement/capture records may
      // depend on an earlier pending operation.
      break;
    }
  }
  return result;
}

export const __offlineJournal = { DB_NAME, DB_VERSION, EVENTS_STORE, STATE_STORE, journalId, memoryStores };
