/**
 * @fileoverview Keeps a chart's indicator layers in sync with backend
 * records, removing layers that disappear and clearing on instrument
 * switches.
 */
import type {OverlayLayer} from '@joseph/chart-core';
import {buildIndicatorLayers, type IndicatorLayerInput} from './mapping';

/** Overlay sink (structurally a subset of `ChartPort`). */
export interface OverlaySink {
  /**
   * Adds or replaces a layer.
   * @param layer Layer.
   */
  setOverlayLayer(layer: OverlayLayer): void;
  /**
   * Removes a layer.
   * @param layerId Layer id.
   */
  removeOverlayLayer(layerId: string): void;
}

/** Applies indicator layers to one chart. */
export class IndicatorLayerController {
  private shown = new Set<string>();
  private instrumentKey: string | null = null;

  /**
   * Creates the controller.
   * @param sink Chart overlay sink.
   */
  constructor(private readonly sink: OverlaySink) {}

  /**
   * Renders the latest records.
   * @param input Records and settings.
   * @return Layers applied.
   */
  update(input: IndicatorLayerInput): OverlayLayer[] {
    const key = `${input.instrumentId}|${input.timeframe}`;
    if (this.instrumentKey !== null && this.instrumentKey !== key) this.clear();
    this.instrumentKey = key;
    const layers = buildIndicatorLayers(input);
    const next = new Set(layers.map(layer => layer.layerId));
    for (const id of this.shown) {
      if (!next.has(id)) this.sink.removeOverlayLayer(id);
    }
    for (const layer of layers) this.sink.setOverlayLayer(layer);
    this.shown = next;
    return layers;
  }

  /** Removes every indicator layer. */
  clear(): void {
    for (const id of this.shown) this.sink.removeOverlayLayer(id);
    this.shown = new Set();
  }

  /**
   * Layer ids currently shown.
   * @return Ids.
   */
  layerIds(): string[] {
    return [...this.shown];
  }
}
