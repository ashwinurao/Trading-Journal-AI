// Extended-hours shading and day lines for the intraday trade chart
// (Ash, 2026-11-07: "pre-market and post market hours shaded darker" and
// "a standard way to differentiate between days"; same look as Edge Scanner PR #29).

const ET = 'America/New_York';
const etFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: ET, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
});

// lightweight-charts reads every time with UTC getters and has no time-zone
// option, so bar times are shifted until the "UTC" it reads back is New York
// wall-clock time. The offset comes from the bar's own date: -4 h in summer,
// -5 h from the first Sunday of November (a fixed -4 h put every winter bar an
// hour late on the axis and an hour away from its fill markers).
export function toEtWallTs(isoUtcStr) {
  const g = {};
  for (const p of etFmt.formatToParts(new Date(isoUtcStr))) if (p.type !== 'literal') g[p.type] = parseInt(p.value, 10);
  if (g.hour === 24) g.hour = 0;
  return Math.floor(Date.UTC(g.year, g.month - 1, g.day, g.hour, g.minute, g.second) / 1000);
}

// Regular hours are 09:30-16:00 ET. A bar counts by its start time.
const RTH_FROM = 9 * 60 + 30;
const RTH_TO = 16 * 60;

/** Split ET-shifted bar times (sorted, oldest first) into runs of extended-hours
 *  bars ([first, last] indexes) and the indexes where a new ET day starts. */
export function sessionMarks(times) {
  const extRuns = [];
  const dayStarts = [];
  let runStart = -1;
  let prevDay = null;
  times.forEach((ts, i) => {
    const d = new Date(ts * 1000);
    const day = d.toISOString().slice(0, 10);
    if (i > 0 && day !== prevDay) dayStarts.push(i);
    prevDay = day;
    const minute = d.getUTCHours() * 60 + d.getUTCMinutes();
    if (minute < RTH_FROM || minute >= RTH_TO) {
      if (runStart < 0) runStart = i;
    } else if (runStart >= 0) {
      extRuns.push([runStart, i - 1]);
      runStart = -1;
    }
  });
  if (runStart >= 0) extRuns.push([runStart, times.length - 1]);
  return { extRuns, dayStarts };
}

/** A series primitive that paints the extended-hours bands and dashed day lines
 *  behind the candles. Bar indexes are the chart's logical indexes, so the
 *  candle series must hold every bar the marks were built from. */
export class SessionPrimitive {
  constructor(marks, colors) {
    this._chart = null;
    this._marks = marks;
    this._colors = colors;
    const renderer = { draw: () => {}, drawBackground: target => this._drawBackground(target) };
    this._view = { zOrder: () => 'bottom', renderer: () => renderer };
  }

  attached({ chart }) { this._chart = chart; }
  detached() { this._chart = null; }
  paneViews() { return [this._view]; }

  _drawBackground(target) {
    const ts = this._chart?.timeScale();
    if (!ts) return;
    // logicalToCoordinate returns 0 for a fractional index in lightweight-charts 4,
    // so the bar width comes from two whole indexes. Edges sit half a bar either
    // side of a bar's centre: a band covers its bars and a day line falls between days.
    const x0 = ts.logicalToCoordinate(0);
    const x1 = ts.logicalToCoordinate(1);
    if (x0 == null || x1 == null) return;
    const edge = i => x0 + (i - 0.5) * (x1 - x0);
    const { extRuns, dayStarts } = this._marks;
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      const width = bitmapSize.width / hpr;
      ctx.fillStyle = this._colors.ext;
      for (const [a, b] of extRuns) {
        const from = edge(a), to = edge(b + 1);
        if (to < 0 || from > width) continue;
        const left = Math.round(from * hpr);
        ctx.fillRect(left, 0, Math.max(1, Math.round(to * hpr) - left), bitmapSize.height);
      }
      // Dashed vertical line at each new day, like the "session breaks" line of
      // the common charting platforms.
      const w = Math.max(1, Math.floor(hpr));
      ctx.strokeStyle = this._colors.dayLine;
      ctx.lineWidth = w;
      ctx.setLineDash([4 * vpr, 4 * vpr]);
      ctx.beginPath();
      for (const i of dayStarts) {
        const x = edge(i);
        if (x < 0 || x > width) continue;
        const px = Math.round(x * hpr) + (w % 2 ? 0.5 : 0);
        ctx.moveTo(px, 0);
        ctx.lineTo(px, bitmapSize.height);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    });
  }
}
