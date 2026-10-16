/** Parse the textarea: one glob per line, blanks dropped, duplicates removed. */
export function parseIgnorePaths(text: string): string[] {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  return [...new Set(lines)]
}
