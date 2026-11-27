/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Native drawing settings, table cells, images and coordinates. */

'use client';

import {
  isDrawingContract,
  type DrawingDocument,
  type DrawingAnchor,
} from '@joseph/contracts';
import {isReferenceDocument} from '@joseph/drawing-tools';
import type {DrawingStore} from '@joseph/drawing-core';
import {useState, type ReactElement} from 'react';
import {ScriptDialog} from './script-dialog';
import styles from './script-ui.module.css';

/**
 * Reads a bounded local raster image for a portable drawing document.
 * @param file User-selected PNG, JPEG or WebP, at most 2 MB and 2000 px/side.
 * @return Embedded data URI and natural dimensions; never an external URL.
 * @throws Error if the selected file cannot be decoded or exceeds the limits.
 */
export async function readDrawingImage(
  file: File
): Promise<{name: string; cssWidth: number; cssHeight: number}> {
  if (
    !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) ||
    file.size > 2_000_000
  ) {
    throw new Error('Choose a PNG, JPEG or WebP image no larger than 2 MB.');
  }
  const name = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => void resolve(String(reader.result));
    reader.onerror = () =>
      void reject(new Error('The selected image could not be read.'));
    reader.readAsDataURL(file);
  });
  const image = new Image();
  image.src = name;
  await image.decode();
  if (image.naturalWidth > 2000 || image.naturalHeight > 2000) {
    throw new Error('The image may be at most 2000 pixels on either side.');
  }
  const scale = Math.min(
    1,
    300 / image.naturalWidth,
    300 / image.naturalHeight
  );
  return {
    name,
    cssWidth: image.naturalWidth * scale,
    cssHeight: image.naturalHeight * scale,
  };
}

/**
 * Edits a native tool's properties with one canonical undoable commit.
 * @param props Original document, its store, close and revision callbacks.
 * @return Focused settings dialog; source-specific geometry stays in the plugin.
 */
export function ReferenceSettings({
  doc,
  store,
  onClose,
  onChange,
}: {
  doc: DrawingDocument;
  store: DrawingStore;
  onClose: () => void;
  onChange: () => void;
}): ReactElement {
  const props = (doc.props ?? {}) as Record<string, unknown>;
  const [tab, setTab] = useState('Style');
  const [styleText, setStyleText] = useState(
    JSON.stringify(props.reference_style ?? {}, null, 2)
  );
  const [data, setData] = useState<Record<string, unknown>>({
    ...((props.reference_data ?? {}) as Record<string, unknown>),
  });
  const [anchors, setAnchors] = useState(
    doc.anchors.map(anchor => ({...anchor}))
  );
  const [text, setText] = useState(doc.text ?? '');
  const [error, setError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [cells, setCells] = useState<string[][]>(() => {
    const style = props.reference_style as Record<string, unknown> | undefined;
    return Array.isArray(style?.tableCells)
      ? style.tableCells
          .slice(0, 20)
          .map(row =>
            Array.isArray(row)
              ? row
                  .slice(0, 20)
                  .map(cell => (typeof cell === 'string' ? cell : ''))
              : ['']
          )
      : [
          ['', ''],
          ['', ''],
        ];
  });
  const updateAnchor = (index: number, patch: Partial<DrawingAnchor>) => {
    setAnchors(current =>
      current.map((anchor, position) =>
        position === index ? {...anchor, ...patch} : anchor
      )
    );
  };
  const save = () => {
    try {
      if (styleText.length > 65_536) {
        throw new Error('Tool style must be at most 64 KiB.');
      }
      const style: unknown = JSON.parse(styleText);
      if (!style || typeof style !== 'object' || Array.isArray(style)) {
        throw new Error('Tool style must be a JSON object.');
      }
      const selectedStyle = {
        ...style,
        ...(doc.tool_id === 'reference.table' ? {tableCells: cells} : {}),
      };
      const next: DrawingDocument = {
        ...doc,
        anchors,
        text,
        props: {...props, reference_style: selectedStyle, reference_data: data},
        updated_at_ms: Date.now(),
      };
      if (!isDrawingContract(next) || !isReferenceDocument(next)) {
        throw new Error(
          'The drawing coordinates or tool properties are invalid.'
        );
      }
      if (store.find(doc.drawing_id)?.updated_at_ms !== doc.updated_at_ms) {
        throw new Error(
          'This drawing changed while settings were open. Reopen settings to use its latest version.'
        );
      }
      if (
        !store.update(doc.drawing_id, () => next, 'Change drawing settings')
      ) {
        throw new Error('Unlock the drawing before editing it.');
      }
      onChange();
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };
  return (
    <ScriptDialog title="Drawing settings" onClose={onClose}>
      <div
        className={styles.toolbar}
        role="tablist"
        aria-label="Drawing settings sections"
      >
        {['Style', 'Coordinates', 'Content'].map(name => (
          <button
            key={name}
            role="tab"
            className="j-btn"
            aria-selected={tab === name}
            aria-controls="drawing-settings-panel"
            onClick={() => void setTab(name)}
          >
            {name}
          </button>
        ))}
      </div>
      <div id="drawing-settings-panel" role="tabpanel" aria-label={tab}>
        {tab === 'Style' && (
          <label className={styles.field}>
            Tool-specific style
            <p>
              Fibonacci and Gann levels, line labels, profile rows, position
              risk, fills and text formatting use this tool&apos;s native
              properties.
            </p>
            <textarea
              className={styles.source}
              aria-label="Tool-specific style"
              value={styleText}
              spellCheck={false}
              onChange={event => void setStyleText(event.target.value)}
            />
          </label>
        )}
        {tab === 'Coordinates' && (
          <div className={styles.grid}>
            {anchors.map((anchor, index) => (
              <div className={styles.row} key={index}>
                <label className={styles.field}>
                  Anchor {index + 1} · UTC
                  <input
                    className="j-input"
                    type="datetime-local"
                    step="1"
                    aria-label={`Anchor ${index + 1} UTC time`}
                    value={
                      Number.isFinite(anchor.time_ms) &&
                      Math.abs(anchor.time_ms) <= 8.64e15
                        ? new Date(anchor.time_ms).toISOString().slice(0, 19)
                        : ''
                    }
                    onChange={event =>
                      void updateAnchor(index, {
                        time_ms: Date.parse(`${event.target.value}Z`),
                      })
                    }
                  />
                </label>
                <label className={styles.field}>
                  Price
                  <input
                    className="j-input"
                    inputMode="decimal"
                    aria-label={`Anchor ${index + 1} price`}
                    value={anchor.price}
                    onChange={event =>
                      void updateAnchor(index, {price: event.target.value})
                    }
                  />
                </label>
              </div>
            ))}
          </div>
        )}
        {tab === 'Content' && (
          <div className={styles.grid}>
            <label className={styles.field}>
              Text
              <input
                className="j-input"
                aria-label="Drawing annotation text"
                maxLength={2000}
                value={text}
                onChange={event => void setText(event.target.value)}
              />
            </label>
            {doc.tool_id === 'reference.font-icon' && (
              <label className={styles.field}>
                Glyph
                <input
                  className="j-input"
                  aria-label="Drawing glyph"
                  maxLength={32}
                  value={String(data.glyph ?? '★')}
                  onChange={event =>
                    void setData({...data, glyph: event.target.value})
                  }
                />
              </label>
            )}
            {doc.tool_id === 'reference.image' && (
              <label className={styles.field}>
                Local image · PNG, JPEG or WebP
                <input
                  type="file"
                  aria-label="Drawing image"
                  accept="image/png,image/jpeg,image/webp"
                  disabled={reading}
                  onChange={event => {
                    const file = event.target.files?.[0];
                    if (!file) return;
                    setReading(true);
                    setError(null);
                    void readDrawingImage(file)
                      .then(
                        image => void setData(current => ({...current, image})),
                        failure => void setError(String(failure))
                      )
                      .finally(() => void setReading(false));
                  }}
                />
              </label>
            )}
            {doc.tool_id === 'reference.table' && (
              <>
                <div className={styles.toolbar}>
                  <button
                    className="j-btn"
                    disabled={cells.length >= 20}
                    onClick={() =>
                      void setCells([
                        ...cells,
                        Array.from({length: cells[0]?.length ?? 2}, () => ''),
                      ])
                    }
                  >
                    Add row
                  </button>
                  <button
                    className="j-btn"
                    disabled={(cells[0]?.length ?? 0) >= 20}
                    onClick={() =>
                      void setCells(cells.map(row => [...row, '']))
                    }
                  >
                    Add column
                  </button>
                </div>
                <table
                  className={styles.table}
                  aria-label="Drawing table cells"
                >
                  <tbody>
                    {cells.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {row.map((cell, columnIndex) => (
                          <td key={columnIndex}>
                            <input
                              className="j-input"
                              maxLength={2000}
                              aria-label={`Row ${rowIndex + 1} column ${columnIndex + 1}`}
                              value={cell}
                              onChange={event =>
                                void setCells(current =>
                                  current.map((values, index) =>
                                    index === rowIndex
                                      ? values.map((value, column) =>
                                          column === columnIndex
                                            ? event.target.value
                                            : value
                                        )
                                      : values
                                  )
                                )
                              }
                            />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      <div className={styles.toolbar}>
        <button
          className="j-btn"
          disabled={doc.locked || reading}
          onClick={save}
        >
          Apply drawing settings
        </button>
        <button className="j-btn" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ScriptDialog>
  );
}
