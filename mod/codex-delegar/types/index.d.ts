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

export type TarefaEstado = 'rodando' | 'concluido' | 'falhou' | 'pergunta' | 'travado'

// Contrato de fixtures/tarefas.json: o status.py gera, o mod e o painel só leem.
export type TarefaAgente = {
  id: string
  tipo: 'codex' | 'claude' | 'gemini' | 'processo'
  modelo: string | null
  effort: string | null
  papel: string | null
  inicio: string
  fim: string | null
  segundos: number | null
  estado: TarefaEstado
  progresso: string | null
  resultado: string | null
  veredito: { tipo: 'voltou' | 'relatorio_falso'; achados?: string[] } | null
  tokens: number | null
  cotaAprox: number | null
  conta: string | null
}

export type Tarefa = {
  nome: string
  worktree: string | null
  agentes: TarefaAgente[]
}

export type TarefasResumo = {
  rodando: number
  feitosHoje: number
  voltaram: number
  falharam: number
}

export type TarefaCota = {
  conta: string
  janela: number
  pct: number | null
  resetaEm: string | null
}

export type TarefasJson = {
  tarefas?: Tarefa[]
  resumo?: TarefasResumo
  cotas?: TarefaCota[]
  ts?: number
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
  tarefas: Tarefa[]
  resumo?: TarefasResumo
  cotas: TarefaCota[]
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
