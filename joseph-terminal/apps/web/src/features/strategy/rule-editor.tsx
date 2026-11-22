'use client';
/**
 * @fileoverview Visual Rule AST editor: nested group cards (all / any /
 * not), condition cards with plain-language summaries, inline editors per
 * node type and an "add condition" menu by category.
 */
import type {
  AoiKind,
  Direction,
  EventRef,
  Operand,
  RuleNode,
  StructureEventName,
  Timeframe,
  ValidationIssue,
} from '@joseph/contracts';
import {
  AstEditError,
  NODE_CATALOG,
  STRUCTURE_EVENTS,
  TIMEFRAME_ORDER,
  addChild,
  aoiName,
  describeNode,
  eventName,
  isGroup,
  isWrapper,
  removeNode,
  replaceNode,
  setField,
  shiftNode,
  unwrapNode,
  wrapNode,
  type NodeCategory,
} from '@joseph/strategy-builder';
import {ArrowDown, ArrowUp, Pencil, Trash2} from 'lucide-react';
import {useState, type ReactNode} from 'react';
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
  /** Reports edit errors to the user. */
  onError: (message: string) => void;
}

/** Common AOI kinds offered as checkboxes. */
const COMMON_KINDS: readonly AoiKind[] = [
  'FVG',
  'IFVG',
  'OB',
  'BREAKER',
  'BSL',
  'SSL',
  'EQH',
  'EQL',
  'PDH',
  'PDL',
  'VAH',
  'POC',
  'VAL',
];

/** Category order of the add menu. */
const CATEGORIES: readonly NodeCategory[] = [
  'Structure',
  'Zones',
  'Areas of interest',
  'Values',
  'Time',
  'Risk',
  'Logic',
];

/**
 * Runs an edit and reports AST errors instead of throwing.
 * @param props Editor props.
 * @param edit Edit producing a new root.
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
 * Renders one node (group, wrapper or condition) recursively.
 * @param props Card props.
 * @return Card element.
 */
function NodeCard(props: NodeCardProps): ReactNode {
  const {node, isRoot = false} = props;
  const id = node.id ?? '';
  const [editing, setEditing] = useState(false);
  const issues = props.issues.get(id) ?? [];
  const summary = describeNode(node);
  const actions = !isRoot && (
    <div className={styles.cardActions}>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
        aria-label={`Move "${summary}" up`}
        onClick={() => apply(props, () => shiftNode(props.root, id, -1))}
      >
        <ArrowUp size={14} aria-hidden />
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
        aria-label={`Move "${summary}" down`}
        onClick={() => apply(props, () => shiftNode(props.root, id, 1))}
      >
        <ArrowDown size={14} aria-hidden />
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm"
        onClick={() => apply(props, () => wrapNode(props.root, id, 'NOT'))}
      >
        Negate
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm"
        onClick={() => apply(props, () => wrapNode(props.root, id, 'ANY'))}
      >
        Add alternatives
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
        aria-label={`Remove "${summary}"`}
        onClick={() => apply(props, () => removeNode(props.root, id))}
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
          className={
            issue.code === 'auto_live' ||
            issue.code === 'lower_timeframe' ||
            issue.code === 'deep_tree'
              ? 'j-field__hint'
              : 'j-field__error'
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
                apply(props, () =>
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
                apply(props, () =>
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
              onClick={() => apply(props, () => unwrapNode(props.root, id))}
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
                apply(props, () =>
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
            onClick={() => apply(props, () => unwrapNode(props.root, id))}
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
          onClick={() => setEditing(v => !v)}
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
 * "Add condition" button with a categorized menu.
 * @param props Props.
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
        onClick={() => setOpen(v => !v)}
      >
        Add condition
      </button>
      {open && (
        <div
          className={`j-menu ${styles.addMenu}`}
          role="menu"
          aria-label="Condition types"
        >
          {CATEGORIES.map(category => (
            <div key={category} className={styles.menuGroup}>
              <span className={styles.menuCategory}>{category}</span>
              {NODE_CATALOG.filter(entry => entry.category === category).map(
                entry => (
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
                          entry.create(props.direction, props.signalTf)
                        )
                      );
                    }}
                  >
                    {entry.title}
                  </button>
                )
              )}
            </div>
          ))}
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
 * Inline editor for one condition's fields.
 * @param props Props.
 * @return Editor element.
 */
function NodeFields(props: NodeFieldsProps): ReactNode {
  const {node} = props;
  const id = node.id ?? '';
  const set = (field: string, value: unknown) =>
    apply(props, () => setField(props.root, id, field, value));
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
        onChange={e => set(field, e.target.value || undefined)}
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
          set(field, e.target.value === '' ? undefined : Number(e.target.value))
        }
      />
    </label>
  );
  const decimal = (field: string, value: string | undefined, label: string) => (
    <label className="j-field">
      <span className="j-label">{label}</span>
      <input
        className="j-input j-tabular"
        inputMode="decimal"
        value={value ?? ''}
        onChange={e => set(field, e.target.value)}
      />
    </label>
  );
  const sideToggle = (value: 'bullish' | 'bearish') => (
    <div className="j-segmented" role="group" aria-label="Side">
      <button
        type="button"
        aria-pressed={value === 'bullish'}
        onClick={() => set('side', 'bullish')}
      >
        Bullish
      </button>
      <button
        type="button"
        aria-pressed={value === 'bearish'}
        onClick={() => set('side', 'bearish')}
      >
        Bearish
      </button>
    </div>
  );
  const kindChecks = (
    field: string,
    kinds: readonly AoiKind[],
    label: string
  ) => (
    <fieldset className={styles.checks}>
      <legend className="j-label">{label}</legend>
      {COMMON_KINDS.map(kind => (
        <label key={kind} className="j-check">
          <input
            type="checkbox"
            checked={kinds.includes(kind)}
            onChange={e =>
              set(
                field,
                e.target.checked
                  ? [...kinds, kind]
                  : kinds.filter(k => k !== kind)
              )
            }
          />
          {aoiName(kind)}
        </label>
      ))}
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
        onChange={e => onChange(e.target.value as StructureEventName)}
      >
        {STRUCTURE_EVENTS.map(name => (
          <option key={name} value={name}>
            {eventName(name)}
          </option>
        ))}
      </select>
    </label>
  );

  let fields: ReactNode = null;
  switch (node.op) {
    case 'BIAS':
      fields = (
        <>
          <fieldset className={styles.checks}>
            <legend className="j-label">Timeframes</legend>
            {(['1w', '1d', '12h', '4h', '2h', '1h', '30m', '15m'] as const).map(
              tf => (
                <label key={tf} className="j-check">
                  <input
                    type="checkbox"
                    checked={node.timeframes.includes(tf)}
                    onChange={e =>
                      set(
                        'timeframes',
                        e.target.checked
                          ? [...node.timeframes, tf]
                          : node.timeframes.filter(x => x !== tf)
                      )
                    }
                  />
                  {tf}
                </label>
              )
            )}
          </fieldset>
          <div className="j-segmented" role="group" aria-label="Bias">
            <button
              type="button"
              aria-pressed={node.value === 'BULLISH'}
              onClick={() => set('value', 'BULLISH')}
            >
              Bullish
            </button>
            <button
              type="button"
              aria-pressed={node.value === 'BEARISH'}
              onClick={() => set('value', 'BEARISH')}
            >
              Bearish
            </button>
          </div>
          <label className="j-field">
            <span className="j-label">Agreement</span>
            <select
              className="j-select"
              value={node.match ?? 'ALL'}
              onChange={e => set('match', e.target.value)}
            >
              <option value="ALL">All timeframes</option>
              <option value="K_OF_N">At least k of them</option>
            </select>
          </label>
          {node.match === 'K_OF_N' && number('k', node.k, 'k', 1, 6)}
          <label className="j-check">
            <input
              type="checkbox"
              checked={node.require_bos ?? false}
              onChange={e => set('require_bos', e.target.checked || undefined)}
            />
            Latest break must be a BOS
          </label>
        </>
      );
      break;
    case 'PRICE_IN_ZONE':
      fields = (
        <>
          <label className="j-field">
            <span className="j-label">Zone</span>
            <select
              className="j-select"
              value={node.zone}
              onChange={e => set('zone', e.target.value)}
            >
              <option value="DISCOUNT">Discount</option>
              <option value="PREMIUM">Premium</option>
              <option value="EQUILIBRIUM">Equilibrium</option>
              <option value="OTE">OTE (62-79%)</option>
            </select>
          </label>
          {tfSelect('tf', node.tf, 'Dealing range timeframe', false)}
          <label className="j-field">
            <span className="j-label">Price</span>
            <select
              className="j-select"
              value={node.price_source ?? 'close'}
              onChange={e => set('price_source', e.target.value)}
            >
              <option value="close">Close</option>
              <option value="high">High</option>
              <option value="low">Low</option>
              <option value="hl2">Bar midpoint</option>
            </select>
          </label>
          <label className="j-field">
            <span className="j-label">Range group</span>
            <input
              className="j-input"
              value={node.range_group ?? ''}
              placeholder="None"
              onChange={e => set('range_group', e.target.value || undefined)}
            />
            <span className="j-field__hint">
              Zones with the same group must come from one dealing range.
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
              onChange={e => set('touch', e.target.value)}
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
                set('require_active', e.target.checked ? undefined : false)
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
          {eventSelect(node.name, name => set('name', name), 'Event')}
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
                    set(
                      'steps',
                      node.steps.map((s, j) => (j === i ? {...s, name} : s))
                    ),
                  `Step ${i + 1}`
                )}
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--sm"
                  disabled={node.steps.length <= 2}
                  onClick={() =>
                    set(
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
              set('steps', [
                ...node.steps,
                {name: 'SELLER_FAILURE'} satisfies EventRef,
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
            onChange={v => set('left', v)}
          />
          <label className="j-field">
            <span className="j-label">Comparison</span>
            <select
              className="j-select"
              value={node.cmp}
              onChange={e => set('cmp', e.target.value)}
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
            onChange={v => set('right', v)}
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
            onChange={v => set('value', v)}
          />
          <OperandField
            label="Low"
            operand={node.low}
            onChange={v => set('low', v)}
          />
          <OperandField
            label="High"
            operand={node.high}
            onChange={v => set('high', v)}
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
            onChange={v => set('left', v)}
          />
          <div className="j-segmented" role="group" aria-label="Direction">
            <button
              type="button"
              aria-pressed={node.direction === 'ABOVE'}
              onClick={() => set('direction', 'ABOVE')}
            >
              Above
            </button>
            <button
              type="button"
              aria-pressed={node.direction === 'BELOW'}
              onClick={() => set('direction', 'BELOW')}
            >
              Below
            </button>
          </div>
          <OperandField
            label="Crossed value"
            operand={node.right}
            onChange={v => set('right', v)}
          />
        </>
      );
      break;
    case 'SESSION':
      fields = (
        <fieldset className={styles.checks}>
          <legend className="j-label">Sessions (New York time)</legend>
          {(['ASIA', 'LONDON', 'NEW_YORK'] as const).map(s => (
            <label key={s} className="j-check">
              <input
                type="checkbox"
                checked={node.sessions.includes(s)}
                onChange={e =>
                  set(
                    'sessions',
                    e.target.checked
                      ? [...node.sessions, s]
                      : node.sessions.filter(x => x !== s)
                  )
                }
              />
              {s === 'NEW_YORK'
                ? 'New York 08:30-11:00'
                : s === 'LONDON'
                  ? 'London 02:00-05:00'
                  : 'Asia 20:00-00:00'}
            </label>
          ))}
        </fieldset>
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
          onChange={e => set('label', e.target.value || undefined)}
        />
      </label>
    </div>
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
 * Editor for a COMPARE / IN_RANGE / CROSSES operand.
 * @param props Props.
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
    else if (kind === 'indicator')
      onChange({kind, plugin_id: 'auction_value_areas', field: 'poc'});
    else onChange({kind, field: 'funding_8h_pct'});
  };
  return (
    <fieldset className={styles.operand}>
      <legend className="j-label">{label}</legend>
      <select
        className="j-select"
        aria-label={`${label} type`}
        value={operand.kind}
        onChange={e => changeKind(e.target.value as Operand['kind'])}
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
          onChange={e => onChange({...operand, value: e.target.value})}
        />
      )}
      {operand.kind === 'candle' && (
        <select
          className="j-select"
          aria-label={`${label} candle field`}
          value={operand.field}
          onChange={e =>
            onChange({
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
            onChange={e => onChange({...operand, plugin_id: e.target.value})}
          />
          <input
            className="j-input"
            aria-label={`${label} field`}
            value={operand.field}
            onChange={e => onChange({...operand, field: e.target.value})}
          />
        </>
      )}
      {operand.kind === 'market' && (
        <select
          className="j-select"
          aria-label={`${label} market field`}
          value={operand.field}
          onChange={e =>
            onChange({
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
