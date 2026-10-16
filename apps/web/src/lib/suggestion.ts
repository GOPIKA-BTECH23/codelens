// A finding's `suggestion` is free text from the model: sometimes a sentence ("Use a
// parameterised query"), sometimes a code snippet, often both. We show prose as plain text and
// put ONLY code in code blocks. Everything is rendered as React text, never as HTML, so a
// suggestion cannot inject markup.

export type SuggestionBlock = { kind: 'text'; text: string } | { kind: 'code'; text: string }

const FENCE = /^\s*```/

// Statement keywords that start a line of code. They only count together with code-looking
// punctuation (see looksLikeCode), so prose such as "return early when the token is missing"
// stays text.
const KEYWORD_START =
  /^(def|class|async def|import|from \S+ import|return|raise|yield|assert|if|elif|else|for|while|with|try|except|finally|const|let|var|function|export|await|print|echo)\b/

const STRONG_KEYWORD_LINE =
  /^(def |class |async def |import \S|from \S+ import \S|const \S|let \S|var \S|function[ (]|export \S|@\w)/

/** True for a line that is clearly code, not a sentence. Deliberately conservative. */
export function looksLikeCode(line: string): boolean {
  const text = line.trim()
  if (!text) return false

  // Sentences end with . ? or !; code almost never does.
  if (/[.?!]$/.test(text)) return false
  // A capitalised word followed by more words is how sentences start.
  if (/^[A-Z][a-z]+\s+\S/.test(text) && !/[=(){};]/.test(text)) return false

  if (STRONG_KEYWORD_LINE.test(text)) return true

  // `x = something`, `self.items[0] += 1`: a single-token target, then an assignment.
  if (/^[A-Za-z_$][\w$.[\]'"]*\s*([+\-*/%|&^]|\*\*|\/\/)?=(?!=)\s*\S/.test(text)) return true

  // A call on its own: `cursor.execute(sql, (uid,))`.
  if (/^[A-Za-z_$][\w$.]*\(.*\)\s*;?$/.test(text)) return true

  // Block syntax: `if x:`, `} else {`, `}`, `);`
  if (/[{};]$/.test(text) || /^[)}\]]+[;,]?$/.test(text)) return true
  if (KEYWORD_START.test(text) && /:$/.test(text)) return true

  // `return x`, `raise Error(...)`: needs a code character, or exactly one word after.
  if (/^(return|raise|yield|assert|await|print)\b/.test(text)) {
    const rest = text.replace(/^\S+\s*/, '')
    return rest === '' || /^\S+$/.test(rest) || /[()[\]{}=<>"'_.]/.test(rest)
  }
  return false
}

function isIndented(line: string): boolean {
  return /^( {4}|\t)/.test(line)
}

/**
 * Split a suggestion into text and code blocks.
 *  1. ``` fenced blocks are code (fence lines removed).
 *  2. Outside fences, a run of code-looking lines becomes a code block; indented lines and
 *     blank lines between code lines continue the block. Everything else is text.
 */
export function classifySuggestion(input: string): SuggestionBlock[] {
  const segments: { kind: 'text' | 'code'; lines: string[] }[] = []
  const add = (kind: 'text' | 'code', line: string) => {
    const last = segments[segments.length - 1]
    if (last && last.kind === kind) last.lines.push(line)
    else segments.push({ kind, lines: [line] })
  }

  let inFence = false
  const lines = input.replace(/\r\n/g, '\n').split('\n')
  for (const [i, line] of lines.entries()) {
    if (FENCE.test(line)) {
      inFence = !inFence
      // The fence line itself is dropped, but it starts a new block.
      segments.push({ kind: inFence ? 'code' : 'text', lines: [] })
      continue
    }
    if (inFence) {
      add('code', line)
      continue
    }
    const inCode = segments[segments.length - 1]?.kind === 'code'
    if (looksLikeCode(line) || (inCode && isIndented(line) && line.trim() !== '')) {
      add('code', line)
    } else if (inCode && line.trim() === '') {
      // A blank line stays inside the code block only if code follows it.
      const next = lines.slice(i + 1).find((l) => l.trim() !== '')
      add(next !== undefined && (looksLikeCode(next) || isIndented(next)) ? 'code' : 'text', line)
    } else {
      add('text', line)
    }
  }

  const blocks: SuggestionBlock[] = []
  for (const { kind, lines: body } of segments) {
    // Trim blank lines at both ends of a block.
    const text = body.join('\n').replace(/^\n+|\n+\s*$/g, '')
    if (text.trim()) blocks.push({ kind, text })
  }
  return blocks
}
