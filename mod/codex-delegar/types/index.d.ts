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
  model?: string
  effort?: string
  tokens?: number
  quotaPct?: number
  windows: Array<{ janela: number; pct: number }>
  contas: Array<{ perfil: string; email: string; plano: string; status: 'ok' | 'expirado'; tokens: number }>
  fetchedAt?: number
}

export type ClaudeUsageWindow = {
  pct?: number
  countdown?: string
  clock?: string
}

export type ClaudeAccount = {
  email: string
  number?: number
  active?: boolean
  disabled?: boolean
  usageStatus?: string
  usage?: {
    fiveHour?: ClaudeUsageWindow
    sevenDay?: ClaudeUsageWindow
  } | null
  lastGoodUsage?: {
    fiveHour?: ClaudeUsageWindow
    sevenDay?: ClaudeUsageWindow
  } | null
  lastGoodAgeSeconds?: number
}

export type DashboardJob = {
  tipo: 'claude' | 'codex'
  modelo: string
  projeto: string
  segundos?: number
  arquivo: string
  agentId?: string
  agentStatus?: 'pending' | 'running' | 'waiting' | 'idle' | 'completed' | 'failed' | 'killed'
}

export type RasterSprite = {
  cols: number
  rows: number
  cells: string
}

export type DashboardData = {
  claude: ClaudeAccount[]
  codexFiveHour?: number
  codexSevenDay?: number
  running: DashboardJob[]
  poke: boolean
  map: Record<string, { numero: number; nome: string }>
  wild: Array<{ numero: number; nome: string }>
  sprites: Record<string, RasterSprite>
  errors: string[]
  updatedAt?: number
}

declare module 'claude-code' {
  interface PluginState {
    'codex-delegar': {
      jobs: CodexJob[]
      quota: CodexQuota
      suggestionTurn: number
      suggestionCount: number
      dashboard: DashboardData
    }
  }
}
