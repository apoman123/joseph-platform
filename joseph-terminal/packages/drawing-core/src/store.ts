/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

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
   * @param state Current state; not modified.
   * @return Next state.
   */
  apply(state: DrawingState): DrawingState;
  /**
   * Reverts the command.
   * @param state State the command produced; not modified.
   * @return State before the command.
   */
  revert(state: DrawingState): DrawingState;
}

/**
 * Sorts drawings by z index, then creation time (paint order).
 * @param drawings Drawings in any order.
 * @return Sorted copy, ascending.
 */
function ordered(drawings: readonly DrawingDocument[]): DrawingDocument[] {
  return [...drawings].sort(
    (a, b) => a.z_index - b.z_index || a.created_at_ms - b.created_at_ms
  );
}

/**
 * Builds the command that adds a drawing.
 * @param doc Drawing to add.
 * @return Command (never merged).
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
 * Builds the command that deletes a drawing.
 * @param doc Drawing to delete, as currently stored (restored on undo).
 * @return Command (never merged).
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
 * Builds the command that replaces a drawing (move, resize, style, lock,
 * visibility, reorder, text).
 * @param before Current document.
 * @param after Replacement with the same id.
 * @param label History label.
 * @param mergeKey Merge key shared by the commands of one gesture, or null.
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
   * @param initial Initial state; drawings are sorted into z order.
   * @param historyLimit Maximum undo entries; the oldest is dropped first.
   */
  constructor(
    initial: DrawingState = {drawings: [], opaque: []},
    private readonly historyLimit = 200
  ) {
    this.state = {drawings: ordered(initial.drawings), opaque: initial.opaque};
  }

  /**
   * Returns the current state.
   * @return State (immutable; replaced on every change).
   */
  get(): DrawingState {
    return this.state;
  }

  /**
   * Finds a drawing.
   * @param id Drawing id.
   * @return Document, or undefined when no readable drawing has the id.
   */
  find(id: string): DrawingDocument | undefined {
    return this.state.drawings.find(d => d.drawing_id === id);
  }

  /**
   * Subscribes to changes.
   * @param listener Called with the new state after every change.
   * @return Disposer that unsubscribes.
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
   * Executes a command and records it, merging it into the previous entry
   * when both share a non-null merge key. Clears the redo stack.
   * @param command Command to apply.
   */
  execute(command: DrawingCommand): void {
    this.setState(command.apply(this.state));
    this.redoStack.length = 0;
    // Only the latest entry can absorb a command, so a gesture interrupted
    // by any other command starts a new entry.
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
   * Updates a drawing. A locked drawing accepts an update only when it
   * unlocks the drawing or leaves its anchors, style, text and props alone
   * (visibility, z order).
   * @param id Drawing id.
   * @param change Produces the replacement document from the current one.
   * @param label History label.
   * @param mergeKey Merge key shared by the updates of one gesture, or null.
   * @return True when applied; false for an unknown id or a refused edit of
   *     a locked drawing.
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
   * @return True when deleted; false for an unknown id or a locked drawing.
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
    for (const command of entry.commands) {
      state = command.apply(state);
    }
    this.undoStack.push(entry);
    this.setState(state);
    return true;
  }

  /**
   * Returns whether undo is possible.
   * @return True when the undo stack is not empty.
   */
  canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  /**
   * Returns whether redo is possible.
   * @return True when the redo stack is not empty.
   */
  canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  /**
   * Replaces the state and notifies listeners.
   * @param state New state.
   */
  private setState(state: DrawingState): void {
    this.state = state;
    for (const listener of [...this.listeners]) {
      listener(state);
    }
  }
}

/**
 * Returns whether an update leaves a drawing's content alone, so that only
 * flags such as visibility, lock or z order changed (allowed on locked
 * drawings).
 * @param before Document before the update.
 * @param after Document after the update.
 * @return True when anchors, style, text and props are unchanged.
 */
function onlyFlagsChanged(
  before: DrawingDocument,
  after: DrawingDocument
): boolean {
  // Compares the JSON data structurally: edits spread the previous object,
  // so unchanged values keep their key order.
  return (
    JSON.stringify(before.anchors) === JSON.stringify(after.anchors) &&
    JSON.stringify(before.style) === JSON.stringify(after.style) &&
    before.text === after.text &&
    JSON.stringify(before.props ?? {}) === JSON.stringify(after.props ?? {})
  );
}
