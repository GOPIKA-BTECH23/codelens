import { Save } from 'lucide-react'
import { useState } from 'react'
import { useUpdateSettings } from '../api/queries'
import type { RepoSettings, Severity } from '../api/types'
import { errorMessage } from '../lib/errors'
import { parseIgnorePaths } from '../lib/ignorePaths'
import { SEVERITIES, SEVERITY_LABEL } from '../lib/labels'
import { useToast } from './toast'
import { Button } from './ui/Button'
import { Checkbox, Field, Select, Textarea } from './ui/Field'

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
    <form onSubmit={submit} className="space-y-5">
      <div>
        <label className="flex items-start gap-2.5 text-sm text-fg">
          <Checkbox
            checked={securityOn}
            onChange={(e) => setSecurityOn(e.target.checked)}
            className="mt-0.5"
          />
          Security agent enabled
        </label>
        <p className="mt-1 pl-6.5 text-xs text-muted">When off, pull requests are not reviewed.</p>
      </div>

      <Field label="Minimum severity to post" hint="Lower findings are hidden and counted.">
        <Select
          value={threshold}
          onChange={(e) => setThreshold(e.target.value as Severity)}
          className="w-full"
        >
          {[...SEVERITIES].reverse().map((s) => (
            <option key={s} value={s}>
              {SEVERITY_LABEL[s]}
            </option>
          ))}
        </Select>
      </Field>

      <div>
        <Field label="Ignore paths (one glob per line)">
          <Textarea
            value={ignoreText}
            onChange={(e) => setIgnoreText(e.target.value)}
            rows={4}
            placeholder={'vendor/**\n**/*.generated.ts'}
            className="w-full font-mono text-xs"
          />
        </Field>
        {tooMany && (
          <p role="alert" className="mt-1 text-xs text-danger-fg">
            At most 50 patterns.
          </p>
        )}
      </div>

      <Button
        type="submit"
        variant="primary"
        disabled={save.isPending || tooMany}
        icon={<Save />}
        className="w-full"
      >
        {save.isPending ? 'Saving…' : 'Save settings'}
      </Button>
    </form>
  )
}
