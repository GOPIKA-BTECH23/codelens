/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Sentry DSN for browser errors. Unset = Sentry is off. A DSN is public by design. */
  readonly VITE_SENTRY_DSN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
