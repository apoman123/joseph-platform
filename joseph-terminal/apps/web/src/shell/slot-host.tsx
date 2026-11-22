/**
 * @fileoverview Renders feature contributions for UI slots.
 */
'use client';
import type {SlotContribution, UiSlot} from '@joseph/feature-registry';
import {
  ChevronDown,
  ChevronUp,
  PanelRightClose,
  PanelRightOpen,
} from 'lucide-react';
import {
  Component,
  useEffect,
  useRef,
  useState,
  type ErrorInfo,
  type ReactNode,
  type ReactElement,
} from 'react';
import {clampDock} from '@joseph/terminal-shell';
import {Icon} from './icons';
import {Splitter} from './splitter';
import {useStore, type WebFeatureContext, type WebView} from './react-bindings';
import {useRuntime} from './runtime-context';

/**
 * Catches render errors in one contribution so others keep working.
 */
class SlotBoundary extends Component<
  {name: string; children: ReactNode},
  {error: string | null}
> {
  override state = {error: null as string | null};

  /**
   * Captures the error message.
   * @param error Thrown error.
   * @return State update.
   */
  static getDerivedStateFromError(error: unknown) {
    return {error: error instanceof Error ? error.message : 'Unknown error'};
  }

  /**
   * Logs the error for diagnostics.
   * @param error Error.
   * @param info React info.
   */
  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error(
      `Panel ${this.props.name} failed`,
      error,
      info.componentStack
    );
  }

  /**
   * Renders children or a notice.
   * @return Content.
   */
  override render() {
    if (this.state.error) {
      return (
        <div className="j-notice j-notice--danger" role="alert">
          {this.props.name} stopped working: {this.state.error}. Other panels
          are unaffected.
        </div>
      );
    }
    return this.props.children;
  }
}

/**
 * Renders one contribution with its feature context.
 * @param props Contribution.
 * @return View.
 */
function ContributionView({item}: {item: SlotContribution<WebView>}) {
  const runtime = useRuntime();
  const View = item.view;
  const ctx: WebFeatureContext | undefined = item.featureId
    ? runtime.registry.contextOf(item.featureId)
    : undefined;
  if (!ctx) return null;
  return (
    <SlotBoundary name={item.title}>
      <View ctx={ctx} />
    </SlotBoundary>
  );
}

/**
 * Inline contributions (toolbar, status bar).
 * @param props Slot.
 * @return Items.
 */
export function SlotInline({slot}: {slot: UiSlot}): ReactElement {
  const runtime = useRuntime();
  const items = useStore(runtime.slots.observe(slot));
  return (
    <>
      {items.map(item => (
        <ContributionView key={item.id} item={item} />
      ))}
    </>
  );
}

/** Dock size limits in pixels. */
const DOCK_LIMITS = {
  side: {min: 260, max: 720, fallback: 340},
  bottom: {min: 120, max: 560, fallback: 220},
} as const;

/**
 * Reads a saved dock size.
 * @param raw Stored text.
 * @param orientation Dock orientation.
 * @return Size in pixels, or null for the CSS default.
 */
export function savedDockSize(
  raw: string | null,
  orientation: 'side' | 'bottom'
): number | null {
  const value = Number(raw);
  if (!raw || !Number.isFinite(value)) return null;
  const limits = DOCK_LIMITS[orientation];
  return clampDock(value, limits.min, limits.max);
}

/**
 * Tabbed panel host (side panel or bottom panel).
 * @param props Slot, labels and collapse behaviour.
 * @return Panel.
 */
export function SlotTabs({
  slot,
  label,
  orientation,
}: {
  slot: 'side-panel' | 'bottom-panel';
  label: string;
  orientation: 'side' | 'bottom';
}): ReactElement {
  const runtime = useRuntime();
  const items = useStore(runtime.slots.observe(slot));
  const key = `joseph.panel.${slot}`;
  const [selected, setSelected] = useState<string | null>(() =>
    runtime.storage.getItem(`${key}.tab`)
  );
  const [collapsed, setCollapsed] = useState(
    () => runtime.storage.getItem(`${key}.collapsed`) === 'yes'
  );
  const [size, setSize] = useState<number | null>(() =>
    savedDockSize(runtime.storage.getItem(`${key}.size`), orientation)
  );
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const limits = DOCK_LIMITS[orientation];
  const viewportMax =
    typeof window === 'undefined'
      ? limits.max
      : Math.min(
          limits.max,
          Math.round(
            (orientation === 'side' ? window.innerWidth : window.innerHeight) *
              0.6
          )
        );
  const currentSize = size ?? limits.fallback;
  useEffect(
    () => runtime.storage.setItem(`${key}.collapsed`, collapsed ? 'yes' : 'no'),
    [collapsed, key, runtime]
  );
  const request = useStore(runtime.panelRequest);
  useEffect(() => {
    if (request?.slot !== slot) return;
    if (!items.some(item => item.id === request.id)) return;
    setSelected(request.id);
    setCollapsed(false);
    runtime.storage.setItem(`${key}.tab`, request.id);
  }, [request, slot, items, key, runtime]);
  const current = items.find(item => item.id === selected) ?? items[0];
  const CollapseIcon =
    orientation === 'side'
      ? collapsed
        ? PanelRightOpen
        : PanelRightClose
      : collapsed
        ? ChevronUp
        : ChevronDown;
  return (
    <section
      className={`panel panel--${orientation} ${collapsed ? 'panel--collapsed' : ''}`}
      aria-label={label}
      style={
        collapsed || size === null
          ? undefined
          : orientation === 'side'
            ? {width: size}
            : {height: size}
      }
    >
      {!collapsed && (
        <Splitter
          orientation={orientation === 'side' ? 'vertical' : 'horizontal'}
          label={`Resize ${label.toLowerCase()}`}
          value={currentSize}
          min={limits.min}
          max={viewportMax}
          testId={`resize-${slot}`}
          className={`panel__resize panel__resize--${orientation}`}
          onDrag={delta => {
            const next = clampDock(
              (sizeRef.current ?? limits.fallback) - delta,
              limits.min,
              viewportMax
            );
            sizeRef.current = next;
            setSize(next);
          }}
          onCommit={() => {
            if (sizeRef.current !== null)
              runtime.storage.setItem(`${key}.size`, String(sizeRef.current));
          }}
        />
      )}
      <div className="panel__head">
        <div className="j-tabs panel__tabs" role="tablist" aria-label={label}>
          {!collapsed &&
            items.map(item => (
              <button
                key={item.id}
                type="button"
                role="tab"
                className="j-tab"
                id={`tab-${item.id}`}
                aria-selected={item.id === current?.id}
                aria-controls={`tabpanel-${item.id}`}
                title={
                  item.shortcut
                    ? `${item.title} (${item.shortcut})`
                    : item.title
                }
                onClick={() => {
                  setSelected(item.id);
                  runtime.storage.setItem(`${key}.tab`, item.id);
                }}
              >
                {item.icon && <Icon name={item.icon} size={14} />}
                {item.title}
              </button>
            ))}
        </div>
        <button
          type="button"
          className="j-btn j-btn--ghost j-btn--icon j-btn--sm panel__collapse"
          aria-label={
            collapsed
              ? `Show ${label.toLowerCase()}`
              : `Hide ${label.toLowerCase()}`
          }
          aria-expanded={!collapsed}
          onClick={() => setCollapsed(value => !value)}
        >
          <CollapseIcon size={15} aria-hidden="true" />
        </button>
      </div>
      {!collapsed && (
        <div
          className="j-tabpanel panel__body"
          role="tabpanel"
          id={current ? `tabpanel-${current.id}` : undefined}
          aria-labelledby={current ? `tab-${current.id}` : undefined}
        >
          {current ? (
            <ContributionView key={current.id} item={current} />
          ) : (
            <div className="j-empty">
              <strong>No panels installed</strong>
              <p>
                Panels appear here when features that provide them are enabled.
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
