/**
 * @fileoverview Icon lookup by name (tool manifests reference icons by id).
 */
import type {ReactElement} from 'react';
import {
  Activity,
  AlignJustify,
  ArrowDownRight,
  ArrowRight,
  ArrowRightFromLine,
  ArrowUpRight,
  Bell,
  BookOpen,
  Brush,
  Columns2,
  Filter,
  GitBranch,
  Info,
  LineChart,
  List,
  Minus,
  MoveDiagonal,
  MoveUpRight,
  RectangleHorizontal,
  Ruler,
  SeparatorVertical,
  Shapes,
  Spline,
  Square,
  TrendingUp,
  Type,
  Wallet,
  Workflow,
  type LucideIcon,
} from 'lucide-react';

/** Known icons. */
const ICONS: Readonly<Record<string, LucideIcon>> = {
  activity: Activity,
  'align-justify': AlignJustify,
  'arrow-down-right': ArrowDownRight,
  'arrow-right': ArrowRight,
  'arrow-right-from-line': ArrowRightFromLine,
  'arrow-up-right': ArrowUpRight,
  bell: Bell,
  book: BookOpen,
  brush: Brush,
  'columns-2': Columns2,
  filter: Filter,
  'git-branch': GitBranch,
  info: Info,
  chart: LineChart,
  list: List,
  minus: Minus,
  'move-diagonal': MoveDiagonal,
  'move-up-right': MoveUpRight,
  'rectangle-horizontal': RectangleHorizontal,
  ruler: Ruler,
  'separator-vertical': SeparatorVertical,
  shapes: Shapes,
  spline: Spline,
  square: Square,
  'trending-up': TrendingUp,
  type: Type,
  wallet: Wallet,
  workflow: Workflow,
};

/**
 * Renders a named icon.
 * @param props Icon name and size.
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
