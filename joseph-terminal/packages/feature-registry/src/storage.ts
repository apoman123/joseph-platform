/**
 * @fileoverview Key-value storage port with memory and browser adapters.
 *
 * Browser storage can be unavailable (private windows, blocked site data),
 * so every access is wrapped and failures degrade to "nothing stored".
 */

/** Synchronous key-value storage port. */
export interface KeyValueStorage {
  /**
   * Reads a raw string value.
   * @param key Storage key.
   * @return Stored text or null.
   */
  getItem(key: string): string | null;
  /**
   * Writes a raw string value.
   * @param key Storage key.
   * @param value Text to store.
   */
  setItem(key: string, value: string): void;
  /**
   * Removes a key.
   * @param key Storage key.
   */
  removeItem(key: string): void;
  /**
   * Lists keys with a prefix.
   * @param prefix Key prefix.
   * @return Matching keys.
   */
  keys(prefix: string): string[];
}

/** In-memory storage used in tests and server rendering. */
export class MemoryStorage implements KeyValueStorage {
  private readonly data = new Map<string, string>();

  /**
   * Reads a value.
   * @param key Storage key.
   * @return Stored text or null.
   */
  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }

  /**
   * Writes a value.
   * @param key Storage key.
   * @param value Text to store.
   */
  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }

  /**
   * Removes a value.
   * @param key Storage key.
   */
  removeItem(key: string): void {
    this.data.delete(key);
  }

  /**
   * Lists keys with a prefix.
   * @param prefix Key prefix.
   * @return Matching keys.
   */
  keys(prefix: string): string[] {
    return [...this.data.keys()].filter(key => key.startsWith(prefix));
  }
}

/** Minimal subset of the Web Storage API used by `BrowserStorage`. */
export interface WebStorageLike {
  /** Number of keys. */
  readonly length: number;
  /**
   * Returns the key at an index.
   * @param index Index.
   * @return Key or null.
   */
  key(index: number): string | null;
  /**
   * Reads a value.
   * @param key Key.
   * @return Value or null.
   */
  getItem(key: string): string | null;
  /**
   * Writes a value.
   * @param key Key.
   * @param value Value.
   */
  setItem(key: string, value: string): void;
  /**
   * Removes a value.
   * @param key Key.
   */
  removeItem(key: string): void;
}

/** `localStorage` adapter that never throws. */
export class BrowserStorage implements KeyValueStorage {
  /**
   * Creates the adapter.
   * @param resolve Returns the Web Storage object (may throw when blocked).
   */
  constructor(private readonly resolve: () => WebStorageLike | null) {}

  /**
   * Resolves the backing storage safely.
   * @return Storage or null when unavailable.
   */
  private backing(): WebStorageLike | null {
    try {
      return this.resolve();
    } catch {
      return null;
    }
  }

  /**
   * Reads a value.
   * @param key Storage key.
   * @return Stored text or null.
   */
  getItem(key: string): string | null {
    try {
      return this.backing()?.getItem(key) ?? null;
    } catch {
      return null;
    }
  }

  /**
   * Writes a value; quota or permission errors are ignored.
   * @param key Storage key.
   * @param value Text to store.
   */
  setItem(key: string, value: string): void {
    try {
      this.backing()?.setItem(key, value);
    } catch {
      // Storage full or blocked: the terminal keeps working in memory.
    }
  }

  /**
   * Removes a value.
   * @param key Storage key.
   */
  removeItem(key: string): void {
    try {
      this.backing()?.removeItem(key);
    } catch {
      // Ignored: see setItem.
    }
  }

  /**
   * Lists keys with a prefix.
   * @param prefix Key prefix.
   * @return Matching keys.
   */
  keys(prefix: string): string[] {
    const storage = this.backing();
    if (!storage) return [];
    const result: string[] = [];
    try {
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i);
        if (key?.startsWith(prefix)) result.push(key);
      }
    } catch {
      return [];
    }
    return result;
  }
}

/**
 * Reads and parses JSON from storage, returning null on any failure.
 * @param storage Storage port.
 * @param key Storage key.
 * @return Parsed value or null.
 */
export function readJson(storage: KeyValueStorage, key: string): unknown {
  const raw = storage.getItem(key);
  if (raw == null) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

/**
 * Serializes a value to JSON in storage.
 * @param storage Storage port.
 * @param key Storage key.
 * @param value JSON-compatible value.
 */
export function writeJson(
  storage: KeyValueStorage,
  key: string,
  value: unknown
): void {
  storage.setItem(key, JSON.stringify(value));
}
