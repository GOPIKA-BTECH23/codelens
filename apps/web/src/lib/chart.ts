/**
 * A "nice" top for a y axis: the smallest 1, 2, 2.5 or 5 x 10^k that is >= the data maximum.
 * Works at any scale, so a day costing $0.0010 gets an axis of $0.0010, not $1.00 (which drew
 * the bars as invisible slivers). An all-zero series gets an axis of 1.
 */
export function niceMax(value: number): number {
  if (!(value > 0) || !Number.isFinite(value)) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  for (const step of [1, 2, 2.5, 5, 10]) {
    const candidate = step * magnitude
    // Small epsilon: 0.3 / 0.1 is 2.9999999999999996 in floating point.
    if (candidate >= value * (1 - 1e-9)) return candidate
  }
  return 10 * magnitude
}
