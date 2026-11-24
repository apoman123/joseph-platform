/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Visual editor of the rule AST (abstract syntax tree):
 * nested group cards (all / any / not), condition cards with
 * plain-language summaries, inline editors per node type and an "add
 * condition" menu by category. Every edit goes through the strategy
 * builder's pure tree operations and produces a new root.
 */

'use client';
import type {
  AoiKind,
  Direction,
  EventRef,
  LocalTimeWindow,
  Operand,
  RuleNode,
  StateNode,
  StructureEventName,
  TimeWindowNode,
  Timeframe,
  ValidationIssue,
  ValueMatch,
  ValueSpec,
} from '@joseph/contracts';
import {
  AstEditError,
  NODE_CATALOG,
  TIMEFRAME_ORDER,
  WARNING_CODES,
  addChild,
  describeNode,
  eventLabel,
  isGroup,
  isWrapper,
  kindLabel,
  pluginName,
  removeNode,
  replaceNode,
  setField,
  shiftNode,
  stateValues,
  unwrapNode,
  wrapNode,
  type NodeCategory,
  type Vocabulary,
} from '@joseph/strategy-builder';
import {ArrowDown, ArrowUp, Pencil, Trash2} from 'lucide-react';
import {useMemo, useState, type ReactNode} from 'react';
import styles from './strategy.module.css';

/** Shared props of editor components. */
export interface RuleEditorProps {
  /** Root of the rule tree. */
  root: RuleNode;
  /** Replaces the root after an edit. */
  onChange: (root: RuleNode) => void;
  /** Issues keyed by node id. */
  issues: ReadonlyMap<string, ValidationIssue[]>;
  /** Strategy direction (for defaults). */
  direction: Direction;
  /** Signal timeframe (for defaults). */
  signalTf: Timeframe;
  /** Vocabulary of the installed indicator plugins. */
  vocab: Vocabulary;
  /** Reports edit errors to the user. */
  onError: (message: string) => void;
}

/** Category order of the add menu. */
const CATEGORIES: readonly NodeCategory[] = [
  'Indicator state',
  'Zones and levels',
  'Events',
  'Values',
  'Time',
  'Risk',
  'Logic',
];

/** Weekday names, ISO order (index 0 = Monday). */
const WEEKDAY_NAMES = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];

/**
 * Returns the value a new STATE clause matches by default.
 * @param spec Published value, or undefined when unknown.
 * @return `true` for booleans, the first choice for closed strings,
 *     otherwise an empty string to fill in.
 */
function defaultMatch(spec: ValueSpec | undefined): string | boolean {
  if (spec?.type === 'boolean') return true;
  return spec?.choices?.[0] ?? '';
}

/**
 * Keeps the distinct values of a list in first-seen order.
 * @param items Values.
 * @return Distinct values.
 */
function unique<T>(items: readonly T[]): T[] {
  return [...new Set(items)];
}

/**
 * Runs an edit and reports AST errors instead of throwing.
 * @param props Editor props.
 * @param edit Edit producing a new root; it throws `AstEditError` when the
 *     edit is not allowed, and its message is shown.
 */
function apply(props: RuleEditorProps, edit: () => RuleNode): void {
  try {
    props.onChange(edit());
  } catch (err) {
    props.onError(
      err instanceof AstEditError ? err.message : 'That edit is not possible.'
    );
  }
}

/**
 * Renders the whole rule tree.
 * @param props Editor props.
 * @return Editor element.
 */
export function RuleEditor(props: RuleEditorProps): ReactNode {
  return (
    <div className={styles.tree} data-testid="rule-editor">
      <NodeCard {...props} node={props.root} isRoot />
    </div>
  );
}

/** Props of a node card. */
interface NodeCardProps extends RuleEditorProps {
  /** Node to render. */
  node: RuleNode;
  /** Whether this is the root node. */
  isRoot?: boolean;
}

/**
 * Renders one node (group, wrapper or condition) and its children.
 * @param props Card props.
 * @return Card element.
 */
function NodeCard(props: NodeCardProps): ReactNode {
  const {node, isRoot = false} = props;
  const id = node.id ?? '';
  const [editing, setEditing] = useState(false);
  const issues = props.issues.get(id) ?? [];
  const summary = describeNode(node, props.vocab);
  const actions = !isRoot && (
    <div className={styles.cardActions}>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
        aria-label={`Move "${summary}" up`}
        onClick={() => void apply(props, () => shiftNode(props.root, id, -1))}
      >
        <ArrowUp size={14} aria-hidden />
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
        aria-label={`Move "${summary}" down`}
        onClick={() => void apply(props, () => shiftNode(props.root, id, 1))}
      >
        <ArrowDown size={14} aria-hidden />
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm"
        onClick={() => void apply(props, () => wrapNode(props.root, id, 'NOT'))}
      >
        Negate
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm"
        onClick={() => void apply(props, () => wrapNode(props.root, id, 'ANY'))}
      >
        Add alternatives
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
        aria-label={`Remove "${summary}"`}
        onClick={() => void apply(props, () => removeNode(props.root, id))}
      >
        <Trash2 size={14} aria-hidden />
      </button>
    </div>
  );
  const issueList = issues.length > 0 && (
    <ul className={styles.issues}>
      {issues.map(issue => (
        <li
          key={`${issue.path}:${issue.code}`}
          // Warnings are advice, shown as hints rather than errors.
          className={
            WARNING_CODES.has(issue.code) ? 'j-field__hint' : 'j-field__error'
          }
        >
          {issue.message}
        </li>
      ))}
    </ul>
  );

  if (isGroup(node)) {
    return (
      <div
        className={`${styles.group} ${isRoot ? styles.root : ''}`}
        role="group"
        aria-label={summary}
        data-node-id={id}
      >
        <div className={styles.groupHeader}>
          <div className="j-segmented" role="group" aria-label="Group logic">
            <button
              type="button"
              aria-pressed={node.op === 'ALL'}
              onClick={() =>
                void apply(props, () =>
                  replaceNode(props.root, id, {...node, op: 'ALL'})
                )
              }
            >
              All of
            </button>
            <button
              type="button"
              aria-pressed={node.op === 'ANY'}
              onClick={() =>
                void apply(props, () =>
                  replaceNode(props.root, id, {...node, op: 'ANY'})
                )
              }
            >
              Any of
            </button>
          </div>
          <span className="j-muted">
            {node.op === 'ALL'
              ? 'every condition must be true'
              : 'at least one must be true'}
          </span>
          <span className={styles.grow} />
          {node.children.length === 1 && !isRoot && (
            <button
              type="button"
              className="j-btn j-btn--ghost j-btn--sm"
              onClick={() =>
                void apply(props, () => unwrapNode(props.root, id))
              }
            >
              Ungroup
            </button>
          )}
          {actions}
        </div>
        {issueList}
        <div className={styles.children}>
          {node.children.map(child => (
            <NodeCard key={child.id} {...props} node={child} isRoot={false} />
          ))}
        </div>
        <AddConditionMenu {...props} parentId={id} />
      </div>
    );
  }

  if (isWrapper(node)) {
    return (
      <div
        className={styles.wrapper}
        role="group"
        aria-label={summary}
        data-node-id={id}
      >
        <div className={styles.groupHeader}>
          <strong>
            {node.op === 'NOT'
              ? 'Not'
              : node.op === 'PERSIST_FOR'
                ? 'Persists for'
                : 'Within'}
          </strong>
          {node.op !== 'NOT' && (
            <input
              className={`j-input j-tabular ${styles.small}`}
              type="number"
              aria-label="Bars"
              min={node.op === 'PERSIST_FOR' ? 1 : 0}
              max={200}
              value={node.bars}
              onChange={e =>
                void apply(props, () =>
                  setField(props.root, id, 'bars', Number(e.target.value))
                )
              }
            />
          )}
          {node.op !== 'NOT' && <span className="j-muted">bars</span>}
          <span className={styles.grow} />
          <button
            type="button"
            className="j-btn j-btn--ghost j-btn--sm"
            onClick={() => void apply(props, () => unwrapNode(props.root, id))}
          >
            Remove {node.op === 'NOT' ? 'negation' : 'wrapper'}
          </button>
          {!isRoot && actions}
        </div>
        {issueList}
        <div className={styles.children}>
          <NodeCard {...props} node={node.child} isRoot={false} />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.card} data-node-id={id} data-testid={`node-${id}`}>
      <div className={styles.cardHeader}>
        <span className={styles.summary}>{summary}</span>
        <button
          type="button"
          className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
          aria-expanded={editing}
          aria-label={`Edit "${summary}"`}
          onClick={() => void setEditing(v => !v)}
        >
          <Pencil size={14} aria-hidden />
        </button>
        {actions}
      </div>
      {issueList}
      {editing && <NodeFields {...props} node={node} />}
    </div>
  );
}

/** Props of the add-condition menu. */
interface AddMenuProps extends RuleEditorProps {
  /** Group receiving the new condition. */
  parentId: string;
}

/**
 * Renders the "Add condition" button with a menu by category; new
 * conditions get defaults for the strategy's direction and timeframe.
 * @param props Editor props and the receiving group.
 * @return Element.
 */
function AddConditionMenu(props: AddMenuProps): ReactNode {
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.add}>
      <button
        type="button"
        className="j-btn j-btn--sm"
        aria-expanded={open}
        onClick={() => void setOpen(v => !v)}
      >
        Add condition
      </button>
      {open && (
        <div
          className={`j-menu ${styles.addMenu}`}
          role="menu"
          aria-label="Condition types"
        >
          {CATEGORIES.map(category => {
            const entries = NODE_CATALOG.filter(
              entry =>
                entry.category === category && entry.available(props.vocab)
            );
            if (entries.length === 0) return null;
            return (
              <div key={category} className={styles.menuGroup}>
                <span className={styles.menuCategory}>{category}</span>
                {entries.map(entry => (
                  <button
                    key={entry.op}
                    type="button"
                    role="menuitem"
                    className="j-menu__item"
                    title={entry.description}
                    onClick={() => {
                      setOpen(false);
                      apply(props, () =>
                        addChild(
                          props.root,
                          props.parentId,
                          entry.create(
                            props.direction,
                            props.signalTf,
                            props.vocab
                          )
                        )
                      );
                    }}
                  >
                    {entry.title}
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Props of node field editors. */
interface NodeFieldsProps extends RuleEditorProps {
  /** Leaf node being edited. */
  node: RuleNode;
}

/**
 * Renders the inline editor for one condition's fields.
 * @param props Editor props and the condition.
 * @return Editor element.
 */
function NodeFields(props: NodeFieldsProps): ReactNode {
  const {node} = props;
  const id = node.id ?? '';
  const set = (field: string, value: unknown) =>
    void apply(props, () => setField(props.root, id, field, value));
  const tfSelect = (
    field: string,
    value: Timeframe | undefined,
    label: string,
    optional: boolean
  ) => (
    <label className="j-field">
      <span className="j-label">{label}</span>
      <select
        className="j-select"
        value={value ?? ''}
        onChange={e => void set(field, e.target.value || undefined)}
      >
        {optional && <option value="">Signal timeframe</option>}
        {TIMEFRAME_ORDER.map(tf => (
          <option key={tf} value={tf}>
            {tf}
          </option>
        ))}
      </select>
    </label>
  );
  const number = (
    field: string,
    value: number | undefined,
    label: string,
    min: number,
    max: number
  ) => (
    <label className="j-field">
      <span className="j-label">{label}</span>
      <input
        className="j-input j-tabular"
        type="number"
        min={min}
        max={max}
        value={value ?? ''}
        onChange={e =>
          void set(
            field,
            e.target.value === '' ? undefined : Number(e.target.value)
          )
        }
      />
    </label>
  );
  // Decimal fields keep the typed text; validation checks it, and it never
  // passes through a float.
  const decimal = (field: string, value: string | undefined, label: string) => (
    <label className="j-field">
      <span className="j-label">{label}</span>
      <input
        className="j-input j-tabular"
        inputMode="decimal"
        value={value ?? ''}
        onChange={e => void set(field, e.target.value)}
      />
    </label>
  );
  const sideToggle = (value: 'bullish' | 'bearish') => (
    <div className="j-segmented" role="group" aria-label="Side">
      <button
        type="button"
        aria-pressed={value === 'bullish'}
        onClick={() => void set('side', 'bullish')}
      >
        Bullish
      </button>
      <button
        type="button"
        aria-pressed={value === 'bearish'}
        onClick={() => void set('side', 'bearish')}
      >
        Bearish
      </button>
    </div>
  );
  const {vocab} = props;
  const kindChecks = (
    field: string,
    kinds: readonly AoiKind[],
    label: string
  ) => (
    <fieldset className={styles.checks}>
      <legend className="j-label">{label}</legend>
      {unique([...vocab.kinds.keys(), ...kinds]).map(kind => (
        <label
          key={kind}
          className="j-check"
          title={`${kind} (${pluginName(vocab, vocab.kinds.get(kind)?.pluginId ?? 'not installed')})`}
        >
          <input
            type="checkbox"
            checked={kinds.includes(kind)}
            onChange={e =>
              void set(
                field,
                e.target.checked
                  ? [...kinds, kind]
                  : kinds.filter(k => k !== kind)
              )
            }
          />
          {kindLabel(vocab, kind)}
        </label>
      ))}
      {vocab.kinds.size === 0 && kinds.length === 0 && (
        <span className="j-field__hint">
          No installed indicator publishes zones or levels.
        </span>
      )}
    </fieldset>
  );
  const eventSelect = (
    value: StructureEventName,
    onChange: (name: StructureEventName) => void,
    label: string
  ) => (
    <label className="j-field">
      <span className="j-label">{label}</span>
      <select
        className="j-select"
        value={value}
        onChange={e => void onChange(e.target.value)}
      >
        {unique([...vocab.events.keys(), value]).map(name => (
          <option key={name} value={name}>
            {eventLabel(vocab, name)}
          </option>
        ))}
      </select>
    </label>
  );
  // A source is offered only when several plugins publish the event (or
  // one is already set): with a single publisher it adds nothing.
  const sourceSelect = (
    name: StructureEventName,
    source: string | undefined,
    onChange: (source: string | undefined) => void,
    label: string
  ) => {
    const publishers = vocab.events.get(name)?.pluginIds ?? [];
    if (publishers.length < 2 && !source) return null;
    return (
      <label className="j-field">
        <span className="j-label">{label}</span>
        <select
          className="j-select"
          value={source ?? ''}
          onChange={e => void onChange(e.target.value || undefined)}
        >
          <option value="">Any publisher</option>
          {unique([...publishers, ...(source ? [source] : [])]).map(id => (
            <option key={id} value={id}>
              {pluginName(vocab, id)}
            </option>
          ))}
        </select>
      </label>
    );
  };
  const setEvent = (name: StructureEventName) =>
    void apply(props, () => {
      const renamed = setField(props.root, id, 'name', name);
      // A source that does not publish the new event would be an error.
      const publishers = vocab.events.get(name)?.pluginIds ?? [];
      return node.op === 'EVENT' &&
        node.source &&
        !publishers.includes(node.source)
        ? setField(renamed, id, 'source', undefined)
        : renamed;
    });

  let fields: ReactNode = null;
  switch (node.op) {
    case 'STATE':
      fields = (
        <StateFields
          node={node}
          vocab={vocab}
          onChange={next =>
            void apply(props, () => replaceNode(props.root, id, next))
          }
        />
      );
      break;
    case 'IN_ZONE':
      fields = (
        <>
          {kindChecks('kinds', node.kinds, 'Any of these zones or levels')}
          <label className="j-field">
            <span className="j-label">Side</span>
            <select
              className="j-select"
              value={node.side ?? ''}
              onChange={e => void set('side', e.target.value || undefined)}
            >
              <option value="">Either side</option>
              <option value="bullish">Bullish</option>
              <option value="bearish">Bearish</option>
            </select>
          </label>
          {tfSelect('tf', node.tf, 'Detected on', false)}
          <label className="j-field">
            <span className="j-label">Price</span>
            <select
              className="j-select"
              value={node.price_source ?? 'close'}
              onChange={e => void set('price_source', e.target.value)}
            >
              <option value="close">Close</option>
              <option value="high">High</option>
              <option value="low">Low</option>
              <option value="hl2">Bar midpoint</option>
            </select>
          </label>
          <label className="j-field">
            <span className="j-label">Group</span>
            <input
              className="j-input"
              value={node.group ?? ''}
              placeholder="None"
              onChange={e => void set('group', e.target.value || undefined)}
            />
            <span className="j-field__hint">
              Zone conditions with the same group must match zones of one
              structure (the same group id).
            </span>
          </label>
        </>
      );
      break;
    case 'AOI_TOUCH':
      fields = (
        <>
          {kindChecks('kinds', node.kinds, 'Any of these areas')}
          {sideToggle(node.side)}
          {number(
            'within_bars',
            node.within_bars,
            'Within bars (0 = this bar)',
            0,
            200
          )}
          <label className="j-field">
            <span className="j-label">Touch</span>
            <select
              className="j-select"
              value={node.touch ?? 'WICK'}
              onChange={e => void set('touch', e.target.value)}
            >
              <option value="WICK">Wick</option>
              <option value="BODY">Body</option>
              <option value="CLOSE">Close inside</option>
            </select>
          </label>
          {tfSelect('tf', node.tf, 'Detected on', true)}
          {number('min_quality', node.min_quality, 'Minimum quality', 0, 100)}
          <label className="j-check">
            <input
              type="checkbox"
              checked={node.require_active !== false}
              onChange={e =>
                void set('require_active', e.target.checked ? undefined : false)
              }
            />
            Only areas still active
          </label>
        </>
      );
      break;
    case 'AOI_OVERLAP':
      fields = (
        <>
          {kindChecks('kinds_a', node.kinds_a, 'Area')}
          {kindChecks('kinds_b', node.kinds_b, 'Overlapping')}
          {sideToggle(node.side)}
          {decimal(
            'min_overlap_pct',
            node.min_overlap_pct,
            'Minimum overlap %'
          )}
        </>
      );
      break;
    case 'EVENT':
      fields = (
        <>
          {eventSelect(node.name, setEvent, 'Event')}
          {sourceSelect(
            node.name,
            node.source,
            source => void set('source', source),
            'Source'
          )}
          {number(
            'within_bars',
            node.within_bars,
            'Within bars (0 = this bar)',
            0,
            200
          )}
          {tfSelect('tf', node.tf, 'Timeframe', true)}
        </>
      );
      break;
    case 'SEQUENCE':
      fields = (
        <>
          <ol className={styles.steps}>
            {node.steps.map((step, i) => (
              <li key={`${i}-${step.name}`}>
                {eventSelect(
                  step.name,
                  name =>
                    void set(
                      'steps',
                      node.steps.map((s, j) =>
                        j === i ? withEvent(s, name, vocab) : s
                      )
                    ),
                  `Step ${i + 1}`
                )}
                {sourceSelect(
                  step.name,
                  step.source,
                  source =>
                    void set(
                      'steps',
                      node.steps.map((s, j) =>
                        j === i ? withSource(s, source) : s
                      )
                    ),
                  `Step ${i + 1} source`
                )}
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--sm"
                  disabled={node.steps.length <= 2}
                  onClick={() =>
                    void set(
                      'steps',
                      node.steps.filter((_s, j) => j !== i)
                    )
                  }
                >
                  Remove step
                </button>
              </li>
            ))}
          </ol>
          <button
            type="button"
            className="j-btn j-btn--sm"
            disabled={node.steps.length >= 8}
            onClick={() =>
              void set('steps', [
                ...node.steps,
                {
                  name: [...vocab.events.keys()][0] ?? node.steps[0].name,
                } satisfies EventRef,
              ])
            }
          >
            Add step
          </button>
          {number('within_bars', node.within_bars, 'Within bars', 1, 200)}
        </>
      );
      break;
    case 'COMPARE':
      fields = (
        <>
          <OperandField
            label="Value"
            operand={node.left}
            onChange={v => void set('left', v)}
          />
          <label className="j-field">
            <span className="j-label">Comparison</span>
            <select
              className="j-select"
              value={node.cmp}
              onChange={e => void set('cmp', e.target.value)}
            >
              <option value="gt">greater than</option>
              <option value="gte">at least</option>
              <option value="lt">less than</option>
              <option value="lte">at most</option>
              <option value="eq">equals</option>
              <option value="neq">does not equal</option>
            </select>
          </label>
          <OperandField
            label="Compared with"
            operand={node.right}
            onChange={v => void set('right', v)}
          />
        </>
      );
      break;
    case 'IN_RANGE':
      fields = (
        <>
          <OperandField
            label="Value"
            operand={node.value}
            onChange={v => void set('value', v)}
          />
          <OperandField
            label="Low"
            operand={node.low}
            onChange={v => void set('low', v)}
          />
          <OperandField
            label="High"
            operand={node.high}
            onChange={v => void set('high', v)}
          />
        </>
      );
      break;
    case 'CROSSES':
      fields = (
        <>
          <OperandField
            label="Value"
            operand={node.left}
            onChange={v => void set('left', v)}
          />
          <div className="j-segmented" role="group" aria-label="Direction">
            <button
              type="button"
              aria-pressed={node.direction === 'ABOVE'}
              onClick={() => void set('direction', 'ABOVE')}
            >
              Above
            </button>
            <button
              type="button"
              aria-pressed={node.direction === 'BELOW'}
              onClick={() => void set('direction', 'BELOW')}
            >
              Below
            </button>
          </div>
          <OperandField
            label="Crossed value"
            operand={node.right}
            onChange={v => void set('right', v)}
          />
        </>
      );
      break;
    case 'TIME_WINDOW':
      fields = (
        <TimeWindowFields
          node={node}
          onChange={next =>
            void apply(props, () => replaceNode(props.root, id, next))
          }
        />
      );
      break;
    case 'RISK_REWARD':
      fields = decimal('min_rr', node.min_rr, 'Minimum reward to risk (R)');
      break;
    default:
      fields = (
        <p className="j-muted">This condition has no editable fields.</p>
      );
  }
  return (
    <div className={styles.fields} aria-label="Condition settings" role="group">
      {fields}
      <label className="j-field">
        <span className="j-label">Note</span>
        <input
          className="j-input"
          value={node.label ?? ''}
          placeholder="Optional label shown in alerts"
          onChange={e => void set('label', e.target.value || undefined)}
        />
      </label>
    </div>
  );
}

/**
 * Renames a sequence step, dropping a source that does not publish the new
 * event.
 * @param step Step.
 * @param name New event name.
 * @param vocab Vocabulary.
 * @return New step.
 */
function withEvent(
  step: EventRef,
  name: StructureEventName,
  vocab: Vocabulary
): EventRef {
  const publishers = vocab.events.get(name)?.pluginIds ?? [];
  return step.source && publishers.includes(step.source)
    ? {...step, name}
    : withSource({...step, name}, undefined);
}

/**
 * Sets or clears the source of a sequence step.
 * @param step Step.
 * @param source Publishing plugin, or undefined for any publisher.
 * @return New step, without a `source` key when `source` is undefined.
 */
function withSource(step: EventRef, source: string | undefined): EventRef {
  const rest: EventRef = {name: step.name};
  if (step.tf) rest.tf = step.tf;
  return source ? {...rest, source} : rest;
}

/** Props of the STATE editor. */
interface StateFieldsProps {
  /** STATE node. */
  node: StateNode;
  /** Vocabulary of the installed plugins. */
  vocab: Vocabulary;
  /** Receives the edited node. */
  onChange: (node: StateNode) => void;
}

/**
 * Renders the STATE editor: the indicator, its value clauses (a value and
 * the matches it may take), the timeframes and how many must agree.
 * @param props Node, vocabulary and change handler.
 * @return Element.
 */
function StateFields({node, vocab, onChange}: StateFieldsProps): ReactNode {
  const info = vocab.plugins.get(node.plugin_id);
  const values = info ? stateValues(info) : [];
  const plugins = [...vocab.plugins.values()].filter(
    p => stateValues(p).length > 0
  );
  const specOf = (field: string) =>
    info?.output_values.find(v => v.key === field);
  // Lists may be emptied while editing; validation then asks for an entry,
  // so the contract's non-empty tuple types are not enforced here.
  const setWhere = (where: ValueMatch[]) =>
    void onChange({...node, where: where as StateNode['where']});
  const setClause = (index: number, clause: ValueMatch) =>
    void setWhere(node.where.map((c, i) => (i === index ? clause : c)));
  const setMatches = (
    index: number,
    clause: ValueMatch,
    anyOf: Array<string | boolean>
  ) =>
    void setClause(index, {
      ...clause,
      any_of: anyOf as ValueMatch['any_of'],
    });
  const choose = (pluginId: string) => {
    const next = vocab.plugins.get(pluginId);
    const [spec] = next ? stateValues(next) : [];
    onChange({
      ...node,
      plugin_id: pluginId,
      where: [{field: spec?.key ?? '', any_of: [defaultMatch(spec)]}],
    });
  };
  const setAgreement = (match: string) => {
    const rest: StateNode = {...node};
    delete rest.k;
    onChange(
      match === 'K_OF_N'
        ? {...rest, match: 'K_OF_N', k: 1}
        : {...rest, match: 'ALL'}
    );
  };
  return (
    <>
      <label className="j-field">
        <span className="j-label">Indicator</span>
        <select
          className="j-select"
          value={node.plugin_id}
          onChange={e => void choose(e.target.value)}
        >
          {unique([...plugins.map(p => p.plugin_id), node.plugin_id]).map(
            pluginId => (
              <option key={pluginId} value={pluginId}>
                {pluginName(vocab, pluginId)}
              </option>
            )
          )}
        </select>
      </label>
      <ol className={styles.steps}>
        {node.where.map((clause, i) => {
          const spec = specOf(clause.field);
          const closed =
            spec?.type === 'boolean' || (spec?.choices?.length ?? 0) > 0;
          const options: Array<string | boolean> =
            spec?.type === 'boolean'
              ? [true, false]
              : unique([...(spec?.choices ?? []), ...clause.any_of]);
          return (
            <li key={`${i}-${clause.field}`}>
              <label className="j-field">
                <span className="j-label">Value {i + 1}</span>
                {info ? (
                  <select
                    className="j-select"
                    value={clause.field}
                    onChange={e =>
                      void setClause(i, {
                        field: e.target.value,
                        any_of: [defaultMatch(specOf(e.target.value))],
                      })
                    }
                  >
                    {unique([...values.map(v => v.key), clause.field]).map(
                      key => (
                        <option key={key} value={key}>
                          {key}
                        </option>
                      )
                    )}
                  </select>
                ) : (
                  <input
                    className="j-input"
                    value={clause.field}
                    onChange={e =>
                      void setClause(i, {...clause, field: e.target.value})
                    }
                  />
                )}
              </label>
              {closed ? (
                <fieldset className={styles.checks}>
                  <legend className="j-label">Is any of</legend>
                  {options.map(option => (
                    <label key={String(option)} className="j-check">
                      <input
                        type="checkbox"
                        checked={clause.any_of.includes(option)}
                        onChange={e =>
                          void setMatches(
                            i,
                            clause,
                            e.target.checked
                              ? [...clause.any_of, option]
                              : clause.any_of.filter(v => v !== option)
                          )
                        }
                      />
                      {String(option)}
                    </label>
                  ))}
                </fieldset>
              ) : (
                <label className="j-field">
                  <span className="j-label">Is any of (comma-separated)</span>
                  <input
                    className="j-input"
                    value={clause.any_of.map(String).join(', ')}
                    onChange={e => {
                      const typed = e.target.value
                        .split(',')
                        .map(v => v.trim())
                        .filter(v => v.length > 0);
                      setMatches(i, clause, typed.length > 0 ? typed : ['']);
                    }}
                  />
                </label>
              )}
              <button
                type="button"
                className="j-btn j-btn--ghost j-btn--sm"
                disabled={node.where.length <= 1}
                onClick={() =>
                  void setWhere(node.where.filter((_c, j) => j !== i))
                }
              >
                Remove value
              </button>
            </li>
          );
        })}
      </ol>
      <button
        type="button"
        className="j-btn j-btn--sm"
        disabled={node.where.length >= 8}
        onClick={() => {
          const spec =
            values.find(v => !node.where.some(c => c.field === v.key)) ??
            values[0];
          setWhere([
            ...node.where,
            {field: spec?.key ?? '', any_of: [defaultMatch(spec)]},
          ]);
        }}
      >
        Add value
      </button>
      <fieldset className={styles.checks}>
        <legend className="j-label">Timeframes</legend>
        {TIMEFRAME_ORDER.map(tf => (
          <label key={tf} className="j-check">
            <input
              type="checkbox"
              checked={node.timeframes.includes(tf)}
              onChange={e => {
                const timeframes = e.target.checked
                  ? [...node.timeframes, tf]
                  : node.timeframes.filter(x => x !== tf);
                onChange({
                  ...node,
                  timeframes: timeframes as StateNode['timeframes'],
                });
              }}
            />
            {tf}
          </label>
        ))}
      </fieldset>
      <label className="j-field">
        <span className="j-label">Agreement</span>
        <select
          className="j-select"
          value={node.match ?? 'ALL'}
          onChange={e => void setAgreement(e.target.value)}
        >
          <option value="ALL">All timeframes</option>
          <option value="K_OF_N">At least k of them</option>
        </select>
      </label>
      {node.match === 'K_OF_N' && (
        <label className="j-field">
          <span className="j-label">k</span>
          <input
            className="j-input j-tabular"
            type="number"
            min={1}
            max={6}
            value={node.k ?? ''}
            onChange={e =>
              void onChange({
                ...node,
                k: e.target.value === '' ? undefined : Number(e.target.value),
              })
            }
          />
        </label>
      )}
    </>
  );
}

/** Props of the TIME_WINDOW editor. */
interface TimeWindowFieldsProps {
  /** TIME_WINDOW node. */
  node: TimeWindowNode;
  /** Receives the edited node. */
  onChange: (node: TimeWindowNode) => void;
}

/**
 * Renders the TIME_WINDOW editor: the time zone, the local-time windows and
 * the weekdays.
 * @param props Node and change handler.
 * @return Element.
 */
function TimeWindowFields({node, onChange}: TimeWindowFieldsProps): ReactNode {
  const zones = useMemo(
    () =>
      unique(['UTC', node.time_zone, ...Intl.supportedValuesOf('timeZone')]),
    [node.time_zone]
  );
  // The window list may be emptied while editing; validation reports it.
  const setWindows = (windows: LocalTimeWindow[]) =>
    void onChange({...node, windows: windows as TimeWindowNode['windows']});
  const setWindow = (index: number, window: LocalTimeWindow) =>
    void setWindows(node.windows.map((w, i) => (i === index ? window : w)));
  const days: readonly number[] = node.weekdays ?? [];
  const toggleDay = (day: number, on: boolean) => {
    const next = on
      ? [...days, day].sort((a, b) => a - b)
      : days.filter(d => d !== day);
    const rest: TimeWindowNode = {...node};
    delete rest.weekdays;
    // No day checked means every day, so the field is left out. The list
    // holds distinct days 1-7, which fits the contract tuple.
    onChange(
      next.length > 0
        ? {...rest, weekdays: next as TimeWindowNode['weekdays']}
        : rest
    );
  };
  return (
    <>
      <label className="j-field">
        <span className="j-label">Time zone</span>
        <select
          className="j-select"
          value={node.time_zone}
          onChange={e => void onChange({...node, time_zone: e.target.value})}
        >
          {zones.map(zone => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
      </label>
      <ol className={styles.steps}>
        {node.windows.map((window, i) => (
          <li key={i}>
            <label className="j-field">
              <span className="j-label">Window {i + 1} from</span>
              <input
                className="j-input j-tabular"
                type="time"
                value={window.start}
                onChange={e =>
                  void setWindow(i, {...window, start: e.target.value})
                }
              />
            </label>
            <label className="j-field">
              <span className="j-label">Window {i + 1} until</span>
              <input
                className="j-input j-tabular"
                type="time"
                value={window.end}
                onChange={e =>
                  void setWindow(i, {...window, end: e.target.value})
                }
              />
            </label>
            <button
              type="button"
              className="j-btn j-btn--ghost j-btn--sm"
              disabled={node.windows.length <= 1}
              onClick={() =>
                void setWindows(node.windows.filter((_w, j) => j !== i))
              }
            >
              Remove window
            </button>
          </li>
        ))}
      </ol>
      <button
        type="button"
        className="j-btn j-btn--sm"
        disabled={node.windows.length >= 8}
        onClick={() =>
          void setWindows([...node.windows, {start: '09:00', end: '17:00'}])
        }
      >
        Add window
      </button>
      <fieldset className={styles.checks}>
        <legend className="j-label">Weekdays (none checked: every day)</legend>
        {WEEKDAY_NAMES.map((name, i) => (
          <label key={name} className="j-check" title={name}>
            <input
              type="checkbox"
              checked={days.includes(i + 1)}
              onChange={e => void toggleDay(i + 1, e.target.checked)}
            />
            {name.slice(0, 3)}
          </label>
        ))}
      </fieldset>
      <span className="j-field__hint">
        The bar start time counts; an end at or before the start runs past
        midnight.
      </span>
    </>
  );
}

/** Props of an operand editor. */
interface OperandFieldProps {
  /** Field label. */
  label: string;
  /** Current operand. */
  operand: Operand;
  /** Receives the new operand. */
  onChange: (operand: Operand) => void;
}

/**
 * Renders the editor of a COMPARE / IN_RANGE / CROSSES operand: a constant
 * (kept as decimal text), a candle field, an indicator output or a market
 * value. Switching the kind starts from that kind's default.
 * @param props Label, operand and change handler.
 * @return Element.
 */
function OperandField({
  label,
  operand,
  onChange,
}: OperandFieldProps): ReactNode {
  const changeKind = (kind: Operand['kind']) => {
    if (kind === 'const') onChange({kind, value: '0'});
    else if (kind === 'candle') onChange({kind, field: 'close'});
    else if (kind === 'indicator') {
      onChange({kind, plugin_id: 'ema', field: 'ema'});
    } else onChange({kind, field: 'funding_8h_pct'});
  };
  return (
    <fieldset className={styles.operand}>
      <legend className="j-label">{label}</legend>
      <select
        className="j-select"
        aria-label={`${label} type`}
        value={operand.kind}
        onChange={e => void changeKind(e.target.value as Operand['kind'])}
      >
        <option value="const">Number</option>
        <option value="candle">Candle value</option>
        <option value="indicator">Indicator value</option>
        <option value="market">Market value</option>
      </select>
      {operand.kind === 'const' && (
        <input
          className="j-input j-tabular"
          aria-label={`${label} number`}
          inputMode="decimal"
          value={operand.value}
          onChange={e => void onChange({...operand, value: e.target.value})}
        />
      )}
      {operand.kind === 'candle' && (
        <select
          className="j-select"
          aria-label={`${label} candle field`}
          value={operand.field}
          onChange={e =>
            void onChange({
              ...operand,
              field: e.target.value as typeof operand.field,
            })
          }
        >
          <option value="open">Open</option>
          <option value="high">High</option>
          <option value="low">Low</option>
          <option value="close">Close</option>
          <option value="base_volume">Volume</option>
        </select>
      )}
      {operand.kind === 'indicator' && (
        <>
          <input
            className="j-input"
            aria-label={`${label} plugin`}
            value={operand.plugin_id}
            onChange={e =>
              void onChange({...operand, plugin_id: e.target.value})
            }
          />
          <input
            className="j-input"
            aria-label={`${label} field`}
            value={operand.field}
            onChange={e => void onChange({...operand, field: e.target.value})}
          />
        </>
      )}
      {operand.kind === 'market' && (
        <select
          className="j-select"
          aria-label={`${label} market field`}
          value={operand.field}
          onChange={e =>
            void onChange({
              ...operand,
              field: e.target.value as typeof operand.field,
            })
          }
        >
          <option value="funding_8h_pct">Funding (8h %)</option>
          <option value="open_interest_usd">Open interest (USD)</option>
          <option value="oi_change_pct">OI change %</option>
          <option value="spread_pct">Spread %</option>
          <option value="turnover_24h_quote">24h turnover</option>
        </select>
      )}
    </fieldset>
  );
}
