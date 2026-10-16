import { useState } from 'react'
import { useUpdateSettings } from '../api/queries'
import type { RepoSettings, Severity } from '../api/types'
import { errorMessage } from '../lib/errors'
import { parseIgnorePaths } from '../lib/ignorePaths'
import { SEVERITIES } from '../lib/labels'
import { useToast } from './toast'

interface Props {
  repoId: string
  settings: RepoSettings
}

/** Admin-only review settings. The API checks the role again; this form just hides itself. */
export function RepoSettingsForm({ repoId, settings }: Props) {
  const toast = useToast()
  const save = useUpdateSettings(repoId)
  const [securityOn, setSecurityOn] = useState(settings.enabled_agents.includes('security'))
  const [threshold, setThreshold] = useState<Severity>(settings.severity_threshold)
  const [ignoreText, setIgnoreText] = useState(settings.ignore_paths.join('\n'))
  const ignorePaths = parseIgnorePaths(ignoreText)
  const tooMany = ignorePaths.length > 50

  function submit(event: React.SyntheticEvent) {
    event.preventDefault()
    save.mutate(
      {
        enabled_agents: securityOn ? ['security'] : [],
        severity_threshold: threshold,
        ignore_paths: ignorePaths,
      },
      {
        onSuccess: () => toast.show('Settings saved', 'success'),
        onError: (error) => toast.show(`Could not save settings: ${errorMessage(error)}`, 'error'),
      },
    )
  }

  return (
    <form onSubmit={submit} className="mt-2 max-w-xl space-y-4">
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={securityOn}
          onChange={(e) => setSecurityOn(e.target.checked)}
        />
        Security agent enabled
      </label>

      <label className="block">
        <span className="block text-sm font-medium">Minimum severity to post</span>
        <select
          value={threshold}
          onChange={(e) => setThreshold(e.target.value as Severity)}
          className="mt-1 rounded border border-slate-300 bg-transparent px-2 py-1.5 capitalize dark:border-slate-700 dark:bg-slate-950"
        >
          {[...SEVERITIES].reverse().map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="block text-sm font-medium">Ignore paths (one glob per line)</span>
        <textarea
          value={ignoreText}
          onChange={(e) => setIgnoreText(e.target.value)}
          rows={4}
          placeholder={'vendor/**\n**/*.generated.ts'}
          className="mt-1 w-full rounded border border-slate-300 bg-transparent px-2 py-1.5 font-mono text-sm dark:border-slate-700"
        />
        {tooMany && (
          <span role="alert" className="text-sm text-red-700 dark:text-red-300">
            At most 50 patterns.
          </span>
        )}
      </label>

      <button
        type="submit"
        disabled={save.isPending || tooMany}
        className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
      >
        {save.isPending ? 'Saving…' : 'Save settings'}
      </button>
    </form>
  )
}
