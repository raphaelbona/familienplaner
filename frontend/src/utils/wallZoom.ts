const DESIGN_WIDTH = 1280;
const MIN_ZOOM = 1;
const MAX_ZOOM = 2.25;

/** UI scale for the wall tablet: kiosk browsers often report a very wide CSS viewport (2400px+) with tiny text,
 *  so scale the UI up until it behaves like a ~1280px wide layout. */
export function computeWallZoom(viewportWidth: number): number {
  if (!Number.isFinite(viewportWidth) || viewportWidth <= DESIGN_WIDTH) return MIN_ZOOM;
  const zoom = Math.round((viewportWidth / DESIGN_WIDTH) * 100) / 100;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}
