import { describe, expect, it } from 'vitest'
import { niceMax } from './chart'

describe('niceMax', () => {
  it('rounds up to 1, 2, 2.5 or 5 times a power of ten', () => {
    expect(niceMax(5)).toBe(5)
    expect(niceMax(7)).toBe(10)
    expect(niceMax(1.2)).toBe(2)
    expect(niceMax(23)).toBe(25)
    expect(niceMax(0.3)).toBeCloseTo(0.5)
  })

  it('keeps tiny values visible instead of flooring the axis at 1', () => {
    expect(niceMax(0.001)).toBeCloseTo(0.001)
    expect(niceMax(0.0042)).toBeCloseTo(0.005)
  })

  it('falls back to 1 for empty or zero data', () => {
    expect(niceMax(0)).toBe(1)
    expect(niceMax(Number.NaN)).toBe(1)
  })
})
