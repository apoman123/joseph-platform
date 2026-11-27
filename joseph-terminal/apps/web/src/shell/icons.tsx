/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Icon lookup by name (tool manifests reference icons by id).
 */

import type {ReactElement} from 'react';
import {
  Activity,
  Crosshair,
  Image as ImageIcon,
  Star,
  AlignJustify,
  Anchor,
  ArrowDownRight,
  ArrowRight,
  ArrowRightFromLine,
  ArrowUpRight,
  AudioWaveform,
  Bell,
  BookOpen,
  Brush,
  ChartNetwork,
  ChartScatter,
  Circle,
  Columns2,
  Ellipse,
  Filter,
  GitBranch,
  GitFork,
  Grid3x3,
  Info,
  LineChart,
  List,
  Minus,
  MountainSnow,
  MoveDiagonal,
  MoveUpRight,
  Rainbow,
  RectangleHorizontal,
  Route,
  Ruler,
  SeparatorVertical,
  Shapes,
  Spline,
  SplinePointer,
  Square,
  Tally5,
  Target,
  TrendingUp,
  TrendingUpDown,
  Triangle,
  TriangleRight,
  Type,
  VectorSquare,
  Wallet,
  Waves,
  Workflow,
  type LucideIcon,
} from 'lucide-react';

/** Icons by the ids that tool manifests use. */
const ICONS: Readonly<Record<string, LucideIcon>> = {
  'activity': Activity,
  'crosshair': Crosshair,
  'image': ImageIcon,
  'star': Star,
  'align-justify': AlignJustify,
  'anchor': Anchor,
  'arrow-down-right': ArrowDownRight,
  'arrow-right': ArrowRight,
  'arrow-right-from-line': ArrowRightFromLine,
  'arrow-up-right': ArrowUpRight,
  'audio-waveform': AudioWaveform,
  'bell': Bell,
  'book': BookOpen,
  'brush': Brush,
  'chart-network': ChartNetwork,
  'chart-scatter': ChartScatter,
  'circle': Circle,
  'columns-2': Columns2,
  'ellipse': Ellipse,
  'filter': Filter,
  'git-branch': GitBranch,
  'git-fork': GitFork,
  'grid-3x3': Grid3x3,
  'info': Info,
  'chart': LineChart,
  'list': List,
  'minus': Minus,
  'mountain-snow': MountainSnow,
  'move-diagonal': MoveDiagonal,
  'move-up-right': MoveUpRight,
  'rainbow': Rainbow,
  'rectangle-horizontal': RectangleHorizontal,
  'route': Route,
  'ruler': Ruler,
  'separator-vertical': SeparatorVertical,
  'shapes': Shapes,
  'spline': Spline,
  'spline-pointer': SplinePointer,
  'square': Square,
  'tally-5': Tally5,
  'target': Target,
  'trending-up': TrendingUp,
  'trending-up-down': TrendingUpDown,
  'triangle': Triangle,
  'triangle-right': TriangleRight,
  'type': Type,
  'vector-square': VectorSquare,
  'wallet': Wallet,
  'waves': Waves,
  'workflow': Workflow,
};

/**
 * Renders a named icon. An unknown or missing name renders a square, so a
 * manifest typo never breaks the toolbar. Icons are decorative
 * (`aria-hidden`); their buttons carry the accessible name.
 * @param props Icon name and size in pixels.
 * @return Icon element.
 */
export function Icon({
  name,
  size = 18,
}: {
  name?: string;
  size?: number;
}): ReactElement {
  const Component = (name && ICONS[name]) || Square;
  return <Component size={size} strokeWidth={1.75} aria-hidden="true" />;
}
