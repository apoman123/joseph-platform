/**
 * @fileoverview Editor state for the strategy feature: the working
 * definition, server validation, saved versions and activation actions.
 */
import {
  ApiError,
  type ActivateRequest,
  type StrategyDefinition,
  type StrategyVersion,
  type ValidationIssue,
} from '@joseph/contracts';
import {createStore, type WritableStore} from '@joseph/feature-registry';
import {
  STRATEGY_TEMPLATES,
  ensureIds,
  validateDefinition,
  type ClientValidation,
} from '@joseph/strategy-builder';
import type {WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';

/** Persisted draft settings (schema version 1). */
export interface StrategyDraftSettings {
  /** Working definition. */
  definition: StrategyDefinition;
}

/** Editor state. */
export interface StrategyEditorState {
  /** Working definition (every node has an id). */
  definition: StrategyDefinition;
  /** Client validation of the working definition. */
  validation: ClientValidation;
  /** Issues returned by the server for the last validated/saved draft. */
  serverIssues: ValidationIssue[];
  /** Versions of the strategy known on the server, newest first. */
  versions: StrategyVersion[];
  /** Whether the draft differs from the latest saved version. */
  dirty: boolean;
  /** Request in flight. */
  busy: 'validate' | 'save' | 'activate' | 'pause' | 'load' | null;
  /** Last status message. */
  message: {tone: 'info' | 'warning' | 'danger'; text: string} | null;
}

/** Editor store and actions. */
export interface StrategyEditor {
  /** State store. */
  state: WritableStore<StrategyEditorState>;
  /**
   * Replaces the working definition.
   * @param definition New definition.
   */
  setDefinition(definition: StrategyDefinition): void;
  /**
   * Starts from a template.
   * @param templateId Template id.
   */
  applyTemplate(templateId: string): void;
  /**
   * Validates the draft on the server.
   * @return Promise resolving when done.
   */
  validateOnServer(): Promise<void>;
  /**
   * Saves the draft as a new immutable version.
   * @return Promise resolving when done.
   */
  save(): Promise<void>;
  /**
   * Reloads versions from the server.
   * @return Promise resolving when done.
   */
  loadVersions(): Promise<void>;
  /**
   * Activates a version.
   * @param request Version and mode.
   * @return Promise resolving when done.
   */
  activate(request: ActivateRequest): Promise<void>;
  /**
   * Pauses the active version.
   * @return Promise resolving when done.
   */
  pause(): Promise<void>;
}

/**
 * Canonical JSON used to compare drafts with saved versions.
 * @param value JSON value.
 * @return Text.
 */
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(
          Object.entries(v as Record<string, unknown>).sort(([a], [b]) =>
            a.localeCompare(b)
          )
        )
      : v
  );
}

/**
 * Creates the strategy editor.
 * @param ctx Feature context.
 * @return Editor.
 */
export function createStrategyEditor(ctx: WebFeatureContext): StrategyEditor {
  const fallback = STRATEGY_TEMPLATES[0]?.create();
  if (!fallback) throw new Error('Strategy templates are missing.');
  const stored = ctx.settings.read<StrategyDraftSettings>({
    definition: fallback,
  });
  const initial =
    stored.definition?.schema_version === 2 ? stored.definition : fallback;
  const definition = {...initial, when: ensureIds(initial.when)};
  const state = createStore<StrategyEditorState>({
    definition,
    validation: validateDefinition(definition),
    serverIssues: [],
    versions: [],
    dirty: true,
    busy: null,
    message: null,
  });
  const patch = (update: Partial<StrategyEditorState>) =>
    state.update(current => ({...current, ...update}));
  const isDirty = (def: StrategyDefinition, versions: StrategyVersion[]) => {
    const latest = versions[0];
    return !latest || canonical(latest.definition) !== canonical(def);
  };

  const editor: StrategyEditor = {
    state,
    setDefinition(next) {
      const withIds = {...next, when: ensureIds(next.when)};
      ctx.settings.write<StrategyDraftSettings>({definition: withIds});
      patch({
        definition: withIds,
        validation: validateDefinition(withIds),
        serverIssues: [],
        dirty: isDirty(withIds, state.get().versions),
        message: null,
      });
    },
    applyTemplate(templateId) {
      const template = STRATEGY_TEMPLATES.find(t => t.id === templateId);
      if (!template) return;
      editor.setDefinition(template.create());
      void editor.loadVersions();
    },
    async validateOnServer() {
      patch({busy: 'validate', message: null});
      try {
        const result = await ctx.clients.strategy.validate(
          state.get().definition
        );
        patch({
          serverIssues: [...result.errors, ...result.warnings],
          message: result.valid
            ? {
                tone: 'info',
                text: `Valid on the server. Needs ${result.required_plugins.join(', ') || 'no indicators'} on ${result.required_timeframes.join(', ')}.`,
              }
            : {
                tone: 'danger',
                text: `The server found ${result.errors.length} problem${result.errors.length === 1 ? '' : 's'}. They are marked on the conditions below.`,
              },
        });
      } catch (err) {
        patch({message: {tone: 'danger', text: errorMessage(err)}});
      } finally {
        patch({busy: null});
      }
    },
    async save() {
      const {definition: def, validation, versions} = state.get();
      if (!validation.valid) {
        patch({
          message: {
            tone: 'danger',
            text: 'Fix the marked problems before saving.',
          },
        });
        return;
      }
      patch({busy: 'save', message: null});
      try {
        let saved: StrategyVersion;
        if (versions.length === 0) {
          try {
            saved = await ctx.clients.strategy.createStrategy(def);
          } catch (err) {
            if (err instanceof ApiError && err.body.code === 'CONFLICT') {
              saved = await ctx.clients.strategy.createVersion(
                def.strategy_id,
                def
              );
            } else {
              throw err;
            }
          }
        } else {
          saved = await ctx.clients.strategy.createVersion(
            def.strategy_id,
            def
          );
        }
        const list = await ctx.clients.strategy.getStrategy(saved.strategy_id);
        patch({
          versions: list.versions,
          dirty: false,
          message: {
            tone: 'info',
            text: `Saved as version ${saved.version}. Saved versions never change; editing creates a new one.`,
          },
        });
      } catch (err) {
        const issues =
          err instanceof ApiError
            ? err.body.field_errors.map(f => ({
                path: f.field,
                code: 'server',
                message: f.message,
              }))
            : [];
        patch({
          serverIssues: issues,
          message: {tone: 'danger', text: errorMessage(err)},
        });
      } finally {
        patch({busy: null});
      }
    },
    async loadVersions() {
      const id = state.get().definition.strategy_id;
      try {
        const list = await ctx.clients.strategy.getStrategy(id);
        patch({
          versions: list.versions,
          dirty: isDirty(state.get().definition, list.versions),
        });
      } catch {
        patch({versions: [], dirty: true});
      }
    },
    async activate(request) {
      patch({busy: 'activate', message: null});
      try {
        const version = await ctx.clients.strategy.activate(
          state.get().definition.strategy_id,
          request
        );
        await editor.loadVersions();
        patch({
          message: {
            tone: 'info',
            text: `Version ${version.version} is active in ${request.mode.replace('_', ' ').toLowerCase()} mode.`,
          },
        });
      } catch (err) {
        patch({message: {tone: 'danger', text: errorMessage(err)}});
      } finally {
        patch({busy: null});
      }
    },
    async pause() {
      patch({busy: 'pause', message: null});
      try {
        const version = await ctx.clients.strategy.pause(
          state.get().definition.strategy_id
        );
        await editor.loadVersions();
        patch({
          message: {
            tone: 'info',
            text: `Version ${version.version} is paused. No new signals are evaluated.`,
          },
        });
      } catch (err) {
        patch({message: {tone: 'danger', text: errorMessage(err)}});
      } finally {
        patch({busy: null});
      }
    },
  };
  void editor.loadVersions();
  return editor;
}
