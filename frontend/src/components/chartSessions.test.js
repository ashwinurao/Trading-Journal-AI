import { sessionMarks, toEtWallTs } from './chartSessions';

// ET wall-clock as the chart's shifted seconds, for hand-built expectations.
const wall = s => Math.floor(Date.parse(`${s}Z`) / 1000);

describe('toEtWallTs', () => {
  test('winter bar uses UTC-5: 14:30 UTC on 2026-11-05 reads as 09:30', () => {
    expect(toEtWallTs('2026-11-05T14:30:00Z')).toBe(wall('2026-11-05T09:30:00'));
  });
  test('summer bar uses UTC-4: 13:30 UTC on 2026-09-15 reads as 09:30', () => {
    expect(toEtWallTs('2026-09-15T13:30:00Z')).toBe(wall('2026-09-15T09:30:00'));
  });
  test('after-hours bar after midnight UTC stays on its ET day', () => {
    expect(toEtWallTs('2026-11-06T00:59:00Z')).toBe(wall('2026-11-05T19:59:00'));
  });
});

describe('sessionMarks', () => {
  test('one day with extended hours: pre-market and after-hours are separate runs', () => {
    const t = ['04:00', '09:29', '09:30', '15:59', '16:00', '19:59'].map(h => wall(`2026-11-05T${h}:00`));
    // 09:30 and 15:59 are regular hours and must NOT be shaded.
    expect(sessionMarks(t)).toEqual({ extRuns: [[0, 1], [4, 5]], dayStarts: [] });
  });
  test('after-hours runs into the next pre-market; the day line falls on 04:00', () => {
    const t = [wall('2026-11-05T15:59:00'), wall('2026-11-05T16:00:00'), wall('2026-11-06T04:00:00'), wall('2026-11-06T09:30:00')];
    expect(sessionMarks(t)).toEqual({ extRuns: [[1, 2]], dayStarts: [2] });
  });
  test('regular hours only: no shading, the day line falls on 09:30', () => {
    const t = [wall('2026-11-05T15:55:00'), wall('2026-11-06T09:30:00')];
    expect(sessionMarks(t)).toEqual({ extRuns: [], dayStarts: [1] });
  });
  test('no bars: nothing to draw', () => {
    expect(sessionMarks([])).toEqual({ extRuns: [], dayStarts: [] });
  });
});
