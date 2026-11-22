/**
 * @fileoverview Drawing store with a reversible command history.
 *
 * Every mutation is a `DrawingCommand` (Command pattern) so undo/redo is
 * exact. Consecutive commands sharing a `mergeKey` (e.g. one drag gesture)
 * collapse into one history entry. Locked drawings reject edits.
 */
import type {DrawingDocument} from '@joseph/contracts';

/** Store contents. */
export interface DrawingState {
  /** Readable documents, in z order (ascending). */
  readonly drawings: readonly DrawingDocument[];
  /** Unreadable payloads preserved verbatim (newer schema, corrupt). */
  readonly opaque: readonly unknown[];
}

/** Reversible mutation. */
export interface DrawingCommand {
  /** Human-readable label (e.g. for an undo tooltip). */
  readonly label: string;
  /** Commands with equal non-null keys merge into one history entry. */
  readonly mergeKey: string | null;
  /**
   * Applies the command.
   * @param state Current state.
   * @return Next state.
   */
  apply(state: DrawingState): DrawingState;
  /**
   * Reverts the command.
   * @param state Current state.
   * @return Previous state.
   */
  revert(state: DrawingState): DrawingState;
}

/**
 * Sorts drawings by z index, then creation time.
 * @param drawings Drawings.
 * @return Sorted copy.
 */
function ordered(drawings: readonly DrawingDocument[]): DrawingDocument[] {
  return [...drawings].sort(
    (a, b) => a.z_index - b.z_index || a.created_at_ms - b.created_at_ms
  );
}

/**
 * Command that adds a drawing.
 * @param doc Drawing to add.
 * @return Command.
 */
export function createCommand(doc: DrawingDocument): DrawingCommand {
  return {
    label: `Draw ${doc.tool_id}`,
    mergeKey: null,
    apply: state => ({...state, drawings: ordered([...state.drawings, doc])}),
    revert: state => ({
      ...state,
      drawings: state.drawings.filter(d => d.drawing_id !== doc.drawing_id),
    }),
  };
}

/**
 * Command that deletes a drawing.
 * @param doc Drawing to delete (current version).
 * @return Command.
 */
export function deleteCommand(doc: DrawingDocument): DrawingCommand {
  return {
    label: `Delete ${doc.tool_id}`,
    mergeKey: null,
    apply: state => ({
      ...state,
      drawings: state.drawings.filter(d => d.drawing_id !== doc.drawing_id),
    }),
    revert: state => ({...state, drawings: ordered([...state.drawings, doc])}),
  };
}

/**
 * Command that replaces a drawing (move, resize, style, lock, visibility,
 * reorder, text).
 * @param before Current document.
 * @param after Replacement with the same id.
 * @param label History label.
 * @param mergeKey Merge key for gestures, or null.
 * @return Command.
 */
export function updateCommand(
  before: DrawingDocument,
  after: DrawingDocument,
  label: string,
  mergeKey: string | null = null
): DrawingCommand {
  const swap =
    (from: DrawingDocument, to: DrawingDocument) =>
    (state: DrawingState): DrawingState => ({
      ...state,
      drawings: ordered(
        state.drawings.map(d => (d.drawing_id === from.drawing_id ? to : d))
      ),
    });
  return {
    label,
    mergeKey,
    apply: swap(before, after),
    revert: swap(after, before),
  };
}

/** History entry, possibly merged from several commands. */
interface Entry {
  /** Commands in application order. */
  commands: DrawingCommand[];
}

/** Observable drawing store with undo/redo. */
export class DrawingStore {
  private state: DrawingState;
  private readonly undoStack: Entry[] = [];
  private readonly redoStack: Entry[] = [];
  private readonly listeners = new Set<(state: DrawingState) => void>();

  /**
   * Creates the store.
   * @param initial Initial state.
   * @param historyLimit Maximum undo entries.
   */
  constructor(
    initial: DrawingState = {drawings: [], opaque: []},
    private readonly historyLimit = 200
  ) {
    this.state = {drawings: ordered(initial.drawings), opaque: initial.opaque};
  }

  /**
   * Returns the current state.
   * @return State.
   */
  get(): DrawingState {
    return this.state;
  }

  /**
   * Finds a drawing.
   * @param id Drawing id.
   * @return Document or undefined.
   */
  find(id: string): DrawingDocument | undefined {
    return this.state.drawings.find(d => d.drawing_id === id);
  }

  /**
   * Subscribes to changes.
   * @param listener Listener.
   * @return Disposer.
   */
  subscribe(listener: (state: DrawingState) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Replaces all contents without history (e.g. after loading).
   * @param state New state.
   */
  reset(state: DrawingState): void {
    this.undoStack.length = 0;
    this.redoStack.length = 0;
    this.setState({drawings: ordered(state.drawings), opaque: state.opaque});
  }

  /**
   * Executes a command and records it.
   * @param command Command.
   */
  execute(command: DrawingCommand): void {
    this.setState(command.apply(this.state));
    this.redoStack.length = 0;
    const last = this.undoStack.at(-1);
    const lastKey = last?.commands.at(-1)?.mergeKey ?? null;
    if (last && command.mergeKey !== null && command.mergeKey === lastKey) {
      last.commands.push(command);
    } else {
      this.undoStack.push({commands: [command]});
      if (this.undoStack.length > this.historyLimit) this.undoStack.shift();
    }
  }

  /**
   * Updates a drawing unless it is locked (unlocking is always allowed).
   * @param id Drawing id.
   * @param change Produces the replacement document.
   * @param label History label.
   * @param mergeKey Merge key for gestures.
   * @return True when applied.
   */
  update(
    id: string,
    change: (doc: DrawingDocument) => DrawingDocument,
    label: string,
    mergeKey: string | null = null
  ): boolean {
    const before = this.find(id);
    if (!before) return false;
    const after = change(before);
    const unlocking = before.locked && !after.locked;
    if (before.locked && !unlocking && !onlyFlagsChanged(before, after)) {
      return false;
    }
    this.execute(updateCommand(before, after, label, mergeKey));
    return true;
  }

  /**
   * Deletes a drawing unless it is locked.
   * @param id Drawing id.
   * @return True when deleted.
   */
  remove(id: string): boolean {
    const doc = this.find(id);
    if (!doc || doc.locked) return false;
    this.execute(deleteCommand(doc));
    return true;
  }

  /**
   * Undoes the last entry.
   * @return True when something was undone.
   */
  undo(): boolean {
    const entry = this.undoStack.pop();
    if (!entry) return false;
    let state = this.state;
    for (const command of [...entry.commands].reverse()) {
      state = command.revert(state);
    }
    this.redoStack.push(entry);
    this.setState(state);
    return true;
  }

  /**
   * Redoes the last undone entry.
   * @return True when something was redone.
   */
  redo(): boolean {
    const entry = this.redoStack.pop();
    if (!entry) return false;
    let state = this.state;
    for (const command of entry.commands) state = command.apply(state);
    this.undoStack.push(entry);
    this.setState(state);
    return true;
  }

  /**
   * Whether undo is possible.
   * @return True when the undo stack is not empty.
   */
  canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  /**
   * Whether redo is possible.
   * @return True when the redo stack is not empty.
   */
  canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  /**
   * Publishes a new state.
   * @param state State.
   */
  private setState(state: DrawingState): void {
    this.state = state;
    for (const listener of [...this.listeners]) listener(state);
  }
}

/**
 * Whether only visibility/lock/z-order changed (allowed on locked drawings).
 * @param before Before.
 * @param after After.
 * @return True when geometry, style and text are unchanged.
 */
function onlyFlagsChanged(
  before: DrawingDocument,
  after: DrawingDocument
): boolean {
  return (
    JSON.stringify(before.anchors) === JSON.stringify(after.anchors) &&
    JSON.stringify(before.style) === JSON.stringify(after.style) &&
    before.text === after.text &&
    JSON.stringify(before.props ?? {}) === JSON.stringify(after.props ?? {})
  );
}
