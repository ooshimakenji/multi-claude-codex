export type CodexEffort = 'low' | 'medium' | 'high' | 'xhigh' | 'max' | 'ultra'

export type CodexJobStatus = 'rodando' | 'ok' | 'falhou' | 'pergunta'

export type FileSnapshot = {
  existe: boolean
  tamanho?: number
  mtimeMs?: number
}

export type CodexJob = {
  id: string
  modelo: string
  effort: CodexEffort
  cwd: string
  saida: string
  status: CodexJobStatus
  startedAt: number
  perguntaExistia: boolean
  perguntaAntes?: FileSnapshot
  saidaAntes?: FileSnapshot
  finishedAt?: number
  motivo?: string
}

export type CodexQuota = {
  model?: string
  effort?: string
  activeProfile?: string
  tokens?: number
  quotaPct?: number
  windows: Array<{ janela: number; pct?: number; reseta_em?: number }>
  contas: Array<{
    perfil: string
    email?: string | null
    plano?: string | null
    status: 'ok' | 'expirado' | 'sem_login' | 'erro'
    tokens?: number
    quotas?: Array<{ janela: number; pct?: number; reseta_em?: number }>
  }>
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
