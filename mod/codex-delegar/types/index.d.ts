export type CodexJobStatus = 'rodando' | 'ok' | 'falhou' | 'pergunta'

export type CodexJob = {
  id: string
  modelo: string
  effort: 'low' | 'medium' | 'high'
  cwd: string
  saida: string
  status: CodexJobStatus
  startedAt: number
  perguntaExistia: boolean
  finishedAt?: number
  motivo?: string
}

export type CodexQuota = {
  tokens?: number
  quotaPct?: number
  windows: Array<{ janela: number; pct: number }>
  contas: Array<{ perfil: string; email: string; plano: string; status: 'ok' | 'expirado'; tokens: number }>
  fetchedAt?: number
}

declare module 'claude-code' {
  interface PluginState {
    'codex-delegar': {
      jobs: CodexJob[]
      quota: CodexQuota
      suggestionTurn: number
      suggestionCount: number
    }
  }
}
