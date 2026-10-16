import type { FindingOut, ReviewFileOut } from '../api/types'

/** New-file line numbers that appear in the file's stored diff. */
export function newLinesOf(file: ReviewFileOut): Set<number> {
  const lines = new Set<number>()
  for (const hunk of file.hunks) {
    for (const line of hunk.lines) if (line.new_line !== null) lines.add(line.new_line)
  }
  return lines
}

/** Where a finding is shown: its last line (like GitHub), else its first, if the diff has it. */
export function anchorLine(finding: FindingOut, shown: ReadonlySet<number>): number | null {
  if (finding.end_line && shown.has(finding.end_line)) return finding.end_line
  return shown.has(finding.line) ? finding.line : null
}

/**
 * Split a file's findings into the ones the diff can show inline and the rest. The backend only
 * keeps findings on changed lines, so "the rest" is rare (a purged diff, a truncated patch),
 * but a finding must never silently disappear.
 */
export function partitionByAnchor(
  files: ReviewFileOut[],
  findings: FindingOut[],
): { anchored: Map<string, FindingOut[]>; unanchored: FindingOut[] } {
  const shownByPath = new Map(files.map((f) => [f.path, newLinesOf(f)]))
  const anchored = new Map<string, FindingOut[]>()
  const unanchored: FindingOut[] = []
  for (const finding of findings) {
    const shown = shownByPath.get(finding.file)
    if (shown && anchorLine(finding, shown) !== null) {
      anchored.set(finding.file, [...(anchored.get(finding.file) ?? []), finding])
    } else {
      unanchored.push(finding)
    }
  }
  return { anchored, unanchored }
}
