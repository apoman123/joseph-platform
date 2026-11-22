/**
 * @fileoverview Floating style editor for the selected drawing: color,
 * width, dash, fill, extension, text, tool inputs (for example anchored
 * VWAP bands), lock, visibility, z-order and delete. Every change is an
 * undoable drawing-store command.
 */
'use client';
import type {DrawingDocument, DrawingStyle} from '@joseph/contracts';
import {
  readInput,
  reorderDrawing,
  restyleDrawing,
  retextDrawing,
  setDrawingFlags,
  setDrawingInputs,
  type DrawingStore,
  type ToolInput,
  type ZOrderMove,
} from '@joseph/drawing-core';
import {
  ArrowDownToLine,
  ArrowUpToLine,
  Eye,
  EyeOff,
  Lock,
  LockOpen,
  Trash2,
} from 'lucide-react';
import {useEffect, useState, type ReactElement} from 'react';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/** Preset swatches (course magenta first; user-authored marks). */
export const SWATCHES: readonly string[] = [
  '#e66fd2',
  '#3cc79a',
  '#f0645a',
  '#f2b544',
  '#5ab0f0',
  '#b49cff',
  '#e8eef2',
];

/** Tools whose drawings carry editable text. */
const TEXT_TOOLS: ReadonlySet<string> = new Set(['text', 'zone-box']);

/**
 * Converts `#rrggbb` + alpha byte to a fill color.
 * @param color Base color.
 * @return Translucent fill.
 */
export function fillFor(color: string): string {
  return `${color.slice(0, 7)}26`;
}

/** Style bar props. */
interface StyleBarProps {
  /** Chart id hosting the drawing. */
  chartId: string;
}

/**
 * Floating style editor; renders nothing without a selection on this chart.
 * @param props Props.
 * @return Toolbar or null.
 */
export function DrawingStyleBar({chartId}: StyleBarProps): ReactElement | null {
  const runtime = useRuntime();
  const selection = useStore(runtime.drawing.selection);
  useStore(runtime.drawing.revision);
  const store: DrawingStore | undefined = runtime.drawing.stores.get(chartId);
  const doc: DrawingDocument | undefined =
    selection?.chartId === chartId
      ? store?.find(selection.drawingId)
      : undefined;
  const [text, setText] = useState(doc?.text ?? '');
  useEffect(() => setText(doc?.text ?? ''), [doc?.drawing_id, doc?.text]);
  if (!store || !doc) return null;
  const tool = runtime.tools.get(doc.tool_id);
  const now = () => Date.now();
  const bump = () => runtime.drawing.revision.update(n => n + 1);
  const restyle = (patch: Partial<DrawingStyle>) => {
    if (!restyleDrawing(store, doc.drawing_id, patch, now())) {
      runtime.notifier.notify(
        'Unlock the drawing to change its style.',
        'warning'
      );
    }
    bump();
  };
  const setInput = (input: ToolInput, value: string | boolean) => {
    if (!setDrawingInputs(store, doc.drawing_id, {[input.key]: value}, now())) {
      runtime.notifier.notify(
        'Unlock the drawing to change its inputs.',
        'warning'
      );
    }
    bump();
  };
  const move = (direction: ZOrderMove) => {
    reorderDrawing(store, doc.drawing_id, direction, now());
    bump();
  };
  const style = doc.style;
  // Tools with a fill keep the key (null = fill off).
  const fillable = style.fill_color !== undefined;
  const hasFill = Boolean(style.fill_color);
  const locked = doc.locked;
  return (
    <div
      className="style-bar"
      role="toolbar"
      aria-label={`Style of ${tool?.manifest.displayName ?? doc.tool_id}`}
      data-testid="drawing-style-bar"
      onPointerDown={event => event.stopPropagation()}
    >
      <span className="style-bar__name">
        {tool?.manifest.displayName ?? 'Unsupported drawing'}
      </span>
      <div className="style-bar__swatches" role="group" aria-label="Color">
        {SWATCHES.map(color => (
          <button
            key={color}
            type="button"
            className="style-bar__swatch"
            style={{background: color}}
            aria-label={`Color ${color}`}
            aria-pressed={style.color.toLowerCase() === color}
            disabled={locked}
            onClick={() =>
              restyle(hasFill ? {color, fill_color: fillFor(color)} : {color})
            }
          />
        ))}
        <label className="style-bar__custom" title="Custom color">
          <span className="sr-only">Custom color</span>
          <input
            type="color"
            value={style.color.slice(0, 7)}
            disabled={locked}
            onChange={event => {
              const color = event.target.value;
              restyle(hasFill ? {color, fill_color: fillFor(color)} : {color});
            }}
          />
        </label>
      </div>
      <label className="style-bar__field">
        <span className="sr-only">Line width</span>
        <select
          className="j-select"
          aria-label="Line width"
          value={style.line_width}
          disabled={locked}
          onChange={event => restyle({line_width: Number(event.target.value)})}
        >
          {[1, 2, 3, 4].map(width => (
            <option key={width} value={width}>
              {width}px
            </option>
          ))}
        </select>
      </label>
      <label className="style-bar__field">
        <span className="sr-only">Line style</span>
        <select
          className="j-select"
          aria-label="Line style"
          value={style.line_style}
          disabled={locked}
          onChange={event =>
            restyle({
              line_style: event.target.value as DrawingStyle['line_style'],
            })
          }
        >
          <option value="solid">Solid</option>
          <option value="dashed">Dashed</option>
          <option value="dotted">Dotted</option>
        </select>
      </label>
      {tool?.inputs && tool.inputs.length > 0 && (
        <span
          className="style-bar__inputs"
          role="group"
          aria-label="Inputs"
          data-testid="drawing-inputs"
        >
          {tool.inputs.map(input =>
            input.kind === 'boolean' ? (
              <label key={input.key} className="j-check">
                <input
                  type="checkbox"
                  checked={readInput(doc, input) === true}
                  disabled={locked}
                  onChange={event => setInput(input, event.target.checked)}
                />
                {input.label}
              </label>
            ) : (
              <label key={input.key} className="style-bar__field">
                <span className="style-bar__label">{input.label}</span>
                <select
                  className="j-select"
                  aria-label={input.label}
                  value={String(readInput(doc, input))}
                  disabled={locked}
                  onChange={event => setInput(input, event.target.value)}
                >
                  {input.choices.map(choice => (
                    <option key={choice.value} value={choice.value}>
                      {choice.label}
                    </option>
                  ))}
                </select>
              </label>
            )
          )}
        </span>
      )}
      {fillable && (
        <label className="j-check">
          <input
            type="checkbox"
            checked={Boolean(style.fill_color)}
            disabled={locked}
            onChange={event =>
              restyle({
                fill_color: event.target.checked ? fillFor(style.color) : null,
              })
            }
          />
          Fill
        </label>
      )}
      {style.extend_right !== undefined && (
        <label className="j-check">
          <input
            type="checkbox"
            checked={style.extend_right === true}
            disabled={locked}
            onChange={event => restyle({extend_right: event.target.checked})}
          />
          Extend right
        </label>
      )}
      {style.extend_left !== undefined && (
        <label className="j-check">
          <input
            type="checkbox"
            checked={style.extend_left === true}
            disabled={locked}
            onChange={event => restyle({extend_left: event.target.checked})}
          />
          Extend left
        </label>
      )}
      {TEXT_TOOLS.has(doc.tool_id) && (
        <input
          className="j-input style-bar__text"
          aria-label="Drawing text"
          value={text}
          maxLength={2000}
          disabled={locked}
          onChange={event => setText(event.target.value)}
          onBlur={() => {
            if (text !== (doc.text ?? '')) {
              retextDrawing(store, doc.drawing_id, text, now());
              bump();
            }
          }}
          onKeyDown={event => {
            if (event.key === 'Enter')
              (event.target as HTMLInputElement).blur();
          }}
        />
      )}
      <span className="style-bar__sep" aria-hidden="true" />
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
        aria-label="Bring to front"
        title="Bring to front"
        onClick={() => move('front')}
      >
        <ArrowUpToLine size={14} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
        aria-label="Send to back"
        title="Send to back"
        onClick={() => move('back')}
      >
        <ArrowDownToLine size={14} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
        aria-label={locked ? 'Unlock drawing' : 'Lock drawing'}
        aria-pressed={locked}
        data-testid="style-lock"
        onClick={() => {
          setDrawingFlags(store, doc.drawing_id, {locked: !locked}, now());
          bump();
        }}
      >
        {locked ? (
          <Lock size={14} aria-hidden="true" />
        ) : (
          <LockOpen size={14} aria-hidden="true" />
        )}
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
        aria-label="Hide drawing"
        onClick={() => {
          setDrawingFlags(store, doc.drawing_id, {visible: false}, now());
          runtime.drawing.selection.set(null);
          bump();
        }}
      >
        {doc.visible ? (
          <Eye size={14} aria-hidden="true" />
        ) : (
          <EyeOff size={14} aria-hidden="true" />
        )}
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
        aria-label="Delete drawing"
        disabled={locked}
        onClick={() => {
          if (store.remove(doc.drawing_id)) {
            runtime.drawing.controllers.get(chartId)?.select(null);
            runtime.drawing.selection.set(null);
          }
          bump();
        }}
      >
        <Trash2 size={14} aria-hidden="true" />
      </button>
    </div>
  );
}
