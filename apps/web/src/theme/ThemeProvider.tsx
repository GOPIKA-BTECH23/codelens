import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { THEME_STORAGE_KEY, ThemeContext, type Theme, initialTheme } from './theme'

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme)
    } catch {
      // not persisted; the toggle still works for this visit
    }
  }, [theme])

  const toggle = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), [])
  const value = useMemo(() => ({ theme, toggle }), [theme, toggle])
  return <ThemeContext value={value}>{children}</ThemeContext>
}
