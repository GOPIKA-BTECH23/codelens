import { describe, expect, it } from 'vitest'
import { classifySuggestion, looksLikeCode } from './suggestion'

const kinds = (text: string) => classifySuggestion(text).map((b) => b.kind)

describe('classifySuggestion', () => {
  it('keeps a plain sentence as text', () => {
    expect(classifySuggestion('Use a parameterised query instead of string formatting.')).toEqual([
      { kind: 'text', text: 'Use a parameterised query instead of string formatting.' },
    ])
  })

  it('does not turn prose that mentions code words into a code block', () => {
    for (const sentence of [
      'Make the function return a 404 instead of the stack trace.',
      'return early when the token is missing',
      'if the input is empty, reject the request',
      'Import the secret from an environment variable and rotate it.',
      'Replace the f-string with bound parameters (see the docs).',
      'Never use eval() on user input.',
    ]) {
      expect(kinds(sentence), sentence).toEqual(['text'])
    }
  })

  it('puts real code in a code block', () => {
    expect(
      classifySuggestion('cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))'),
    ).toEqual([
      { kind: 'code', text: 'cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))' },
    ])
    expect(kinds('api_key = os.environ["API_KEY"]')).toEqual(['code'])
    expect(kinds('return jsonify(error="not found"), 404')).toEqual(['code'])
    expect(kinds('const token = process.env.TOKEN;')).toEqual(['code'])
  })

  it('splits a mix of prose and code in order', () => {
    const blocks = classifySuggestion(
      'Bind the value instead of formatting it:\ncursor.execute(sql, (uid,))\nThis prevents injection.',
    )
    expect(blocks).toEqual([
      { kind: 'text', text: 'Bind the value instead of formatting it:' },
      { kind: 'code', text: 'cursor.execute(sql, (uid,))' },
      { kind: 'text', text: 'This prevents injection.' },
    ])
  })

  it('keeps indented continuation lines and inner blank lines in one block', () => {
    const blocks = classifySuggestion(
      'def load(path):\n    base = Path("/srv")\n\n    return (base / path).resolve()',
    )
    expect(blocks).toHaveLength(1)
    expect(blocks[0]?.kind).toBe('code')
    expect(blocks[0]?.text).toContain('\n\n    return')
  })

  it('treats fenced blocks as code and drops the fence lines', () => {
    const blocks = classifySuggestion('Do this:\n```python\nx = 1\n```\nThen retest.')
    expect(blocks).toEqual([
      { kind: 'text', text: 'Do this:' },
      { kind: 'code', text: 'x = 1' },
      { kind: 'text', text: 'Then retest.' },
    ])
  })

  it('keeps even prose-looking lines inside a fence as code', () => {
    expect(classifySuggestion('```\nUse the secret manager\n```')).toEqual([
      { kind: 'code', text: 'Use the secret manager' },
    ])
  })

  it('returns nothing for empty input', () => {
    expect(classifySuggestion('')).toEqual([])
    expect(classifySuggestion('\n\n')).toEqual([])
  })
})

describe('looksLikeCode', () => {
  it('is false for blank lines and sentences', () => {
    expect(looksLikeCode('')).toBe(false)
    expect(looksLikeCode('Validate the path first.')).toBe(false)
  })
  it('is true for statements and block syntax', () => {
    expect(looksLikeCode('def f(x):')).toBe(true)
    expect(looksLikeCode('} else {')).toBe(true)
    expect(looksLikeCode('x += 1')).toBe(true)
  })
})
