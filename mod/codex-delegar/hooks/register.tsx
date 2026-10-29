import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderElement, RenderInput } from 'claude-code'

import type {
  ClaudeAccount,
  CodexJob,
  CodexQuota,
  DashboardData,
  FileSnapshot,
  RasterSprite,
  TarefaAgente,
  TarefaCota,
  TarefasJson,
} from '../types'

const PANE = 'codex'
const jobs = atom({ plugin: 'codex-delegar', key: 'jobs' } as const, [] as CodexJob[])
const quota = atom({ plugin: 'codex-delegar', key: 'quota' } as const, {
  windows: [],
  contas: [],
} as CodexQuota)
const suggestionTurn = atom({ plugin: 'codex-delegar', key: 'suggestionTurn' } as const, 0)
const suggestionCount = atom({ plugin: 'codex-delegar', key: 'suggestionCount' } as const, 0)
const dashboard = atom({ plugin: 'codex-delegar', key: 'dashboard' } as const, {
  claude: [],
  tarefas: [],
  cotas: [],
  poke: true,
  map: {},
  wild: [],
  sprites: {},
  errors: [],
} as DashboardData)

const PAPEIS = ['escrita', 'revisao', 'leitura', 'correcao', 'apoio'] as const
type Papel = typeof PAPEIS[number]

type DelegarInput = {
  prompt: string
  modelo?: string
  effort?: CodexJob['effort']
  cwd?: string
  saida?: string
  tarefa?: string
  papel?: Papel
}

// Uma linha só: sem quebra, sem fechar o comentário e sem um " papel:" que o status.py leria como o campo seguinte.
const markerValue = (value: string): string => value
  .replace(/<!--|-->/g, ' ')
  .replace(/\s+/g, ' ')
  .replace(/ papel:/g, ' papel=')
  .trim()
  .slice(0, 120)

// Cabeçalho que o status.py casa com o rollout: <!-- job:<ID> tarefa:<tarefa> papel:<papel> -->
export const jobMarker = (id: string, tarefa?: string, papel?: string): string => {
  const fields = [`job:${markerValue(id).replace(/ /g, '-')}`]
  const task = tarefa === undefined ? '' : markerValue(tarefa)
  if (task) fields.push(`tarefa:${task}`)
  if (PAPEIS.includes(papel as Papel)) fields.push(`papel:${papel}`)
  return `<!-- ${fields.join(' ')} -->`
}

type QuotaJson = {
  model?: string
  effort?: string
  ts?: number
  codex?: {
    modelo?: string
    effort?: string
    tokens?: number
    quota_pct?: number
    quotas?: Array<{ janela: number; pct?: number; reseta_em?: number }>
    contas?: Array<{
      perfil: string
      email?: string | null
      plano?: string | null
      status: 'ok' | 'expirado' | 'sem_login' | 'erro'
      tokens?: number
      quotas?: Array<{ janela: number; pct?: number; reseta_em?: number }>
      number?: number
    }>
  }
}

export const parseStatusSnapshot = (parsed: QuotaJson, activeProfile: string): CodexQuota => ({
  model: parsed.codex?.modelo ?? parsed.model,
  effort: parsed.codex?.effort ?? parsed.effort,
  activeProfile,
  quotaPct: parsed.codex?.quota_pct,
  tokens: parsed.codex?.tokens,
  windows: parsed.codex?.quotas ?? [],
  contas: parsed.codex?.contas ?? [],
  fetchedAt: parsed.ts ? parsed.ts * 1000 : undefined,
})

const tail = (text: string): string => text.trim().slice(-1000)

const CACHE_TTL_MS = 60000
const MAX_JOB_HISTORY = 50
const WINDOWS_HOME_FALLBACK = 'C:/Users/Default'

type RuntimePaths = {
  config: string
  poke: string
  cswap: string
  sharedCache: string
  jobsCache: string
  codex: string
}

const pathWithoutTrailingSlash = (value: string): string => value.replace(/[\\/]$/, '')

const runtimePaths = async ($: EngineInterface): Promise<RuntimePaths> => {
  const home = pathWithoutTrailingSlash(
    (await $.env.get('USERPROFILE'))
      ?? (await $.env.get('HOME'))
      ?? WINDOWS_HOME_FALLBACK,
  )
  const claudeConfig = pathWithoutTrailingSlash(
    (await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${home}/.claude`,
  )
  const appData = pathWithoutTrailingSlash((await $.env.get('APPDATA')) ?? `${home}/AppData/Roaming`)
  return {
    config: (await $.env.get('PANEL_CONFIG')) ?? `${claudeConfig}/painel-config.json`,
    poke: (await $.env.get('POKE_CACHE')) ?? `${claudeConfig}/poke-cache`,
    cswap: (await $.env.get('CSWAP_EXE')) ?? `${home}/.local/bin/cswap.exe`,
    sharedCache: (await $.env.get('CODEX_DELEGAR_CACHE')) ?? `${claudeConfig}/codex-delegar-cache.json`,
    jobsCache: `${claudeConfig}/codex-status-jobs.json`,
    codex: (await $.env.get('CODEX_CLI')) ?? `${appData}/npm/codex.cmd`,
  }
}

const activeCodexProfile = async ($: EngineInterface): Promise<string> => pathWithoutTrailingSlash(
  (await $.env.get('CODEX_HOME')) ?? `${(await $.env.get('USERPROFILE')) ?? WINDOWS_HOME_FALLBACK}/.codex`,
).split(/[\\/]/).pop() ?? '.codex'

type CacheEntry<T> = { ts: number; data: T }
type SharedCache = { cswap?: CacheEntry<unknown>; status?: CacheEntry<unknown> }
const emptyDashboard = (): DashboardData => ({
  claude: [],
  codexFiveHour: undefined,
  codexSevenDay: undefined,
  tarefas: [],
  cotas: [],
  poke: true,
  map: {},
  wild: [],
  sprites: {},
  errors: [],
})

const readJson = async <T,>($: EngineInterface, path: string): Promise<T | undefined> => {
  try {
    return JSON.parse(await $.fs.read(path)) as T
  } catch {
    return undefined
  }
}

const readPokeMode = async ($: EngineInterface, configPath: string): Promise<boolean> => {
  const config = await readJson<Record<string, unknown>>($, configPath)
  return typeof config?.modo_poke === 'boolean' ? config.modo_poke : true
}

export const cacheIsFresh = (entry: CacheEntry<unknown> | undefined, now: number): boolean =>
  entry !== undefined && typeof entry.ts === 'number' && now - entry.ts < CACHE_TTL_MS

export const mergeCache = (
  cache: SharedCache | undefined,
  key: 'cswap' | 'status',
  data: unknown,
  ts: number,
): SharedCache => ({
  ...(cache ?? {}),
  [key]: { ts, data },
})

const runCachedJson = async <T,>(
  $: EngineInterface,
  key: 'cswap' | 'status',
  argv: string[],
  cwd?: string,
): Promise<T> => {
  const paths = await runtimePaths($)
  const cache = await readJson<SharedCache>($, paths.sharedCache)
  const cached = cache?.[key]
  if (cached !== undefined && cacheIsFresh(cached, Date.now())) return cached.data as T
  const result = await $.process.run(argv, { ...(cwd === undefined ? {} : { cwd }), timeoutMs: 30000 })
  if (result.exitCode !== 0) throw new Error(result.stderr || `${key} exit ${result.exitCode}`)
  const data = JSON.parse(result.stdout) as T
  try {
    await $.fs.write(paths.sharedCache, JSON.stringify(mergeCache(cache, key, data, Date.now())))
  } catch {
    // The dashboard still works when the shared cache is not writable.
  }
  return data
}

export const mergePokeConfig = (config: Record<string, unknown>, value: boolean): string =>
  `${JSON.stringify({ ...config, modo_poke: value }, null, 2)}\n`

export const setPokeMode = async ($: EngineInterface, value: boolean): Promise<void> => {
  const paths = await runtimePaths($)
  const current = (await readJson<Record<string, unknown>>($, paths.config)) ?? {}
  await $.fs.write(paths.config, mergePokeConfig(current, value))
  await update($, dashboard, state => ({ ...state, poke: value }))
}

export const fnv1a64 = (value: string): bigint => {
  let hash = 0xcbf29ce484222325n
  for (const byte of new TextEncoder().encode(value)) {
    hash = (hash ^ BigInt(byte)) * 0x100000001b3n & 0xffffffffffffffffn
  }
  return hash
}

export const usageColor = (pct: number | undefined): 'success' | 'warning' | 'error' =>
  (pct ?? 0) > 85 ? 'error' : (pct ?? 0) >= 60 ? 'warning' : 'success'

const usageOf = (account: ClaudeAccount | undefined, window: 'fiveHour' | 'sevenDay') =>
  account?.usage?.[window] ?? account?.lastGoodUsage?.[window]

const percent = (value: number | undefined): string => value === undefined ? '—' : `${Math.round(value)}%`

const modelLabel = (model: string): string => model.replace(/^gpt-/, '')

const readSprite = async ($: EngineInterface, path: string): Promise<RasterSprite | undefined> => {
  return await readJson<RasterSprite>($, path)
}

export const selectQuotaAccount = (currentQuota: CodexQuota): CodexQuota['contas'][number] | undefined =>
  currentQuota.contas.find(item => item.perfil === currentQuota.activeProfile)
    ?? currentQuota.contas.find(item => item.tokens === currentQuota.tokens)
    ?? currentQuota.contas.find(item => item.status === 'ok')
    ?? currentQuota.contas[0]

const windowLabel = (janela: number): string => janela === 300
  ? '5h'
  : janela === 10080
    ? '7d'
    : janela === 43200
      ? '30d'
      : `${janela}min`

export const formatQuotaWindows = (currentQuota: CodexQuota): string => currentQuota.windows.map(item => {
  return `${windowLabel(item.janela)} ${item.pct === undefined ? '—' : `${item.pct}%`}`
}).join(' | ') || 'cota indisponível'

const duration = (job: CodexJob, now: number): number =>
  Math.max(0, Math.round(((job.finishedAt ?? now) - job.startedAt) / 1000))

const updateJob = async (
  $: EngineInterface,
  id: string,
  change: (job: CodexJob) => CodexJob,
): Promise<void> => {
  await update($, jobs, list => trimJobHistory(list.map(job => job.id === id ? change(job) : job)))
}

const trimJobHistory = (list: CodexJob[]): CodexJob[] => {
  const running = list.filter(job => job.status === 'rodando')
  const finished = list.filter(job => job.status !== 'rodando').slice(-MAX_JOB_HISTORY)
  return [...running, ...finished].slice(-MAX_JOB_HISTORY)
}

const readOutput = async ($: EngineInterface, path: string): Promise<string | undefined> => {
  if (!(await $.fs.exists(path))) {
    return undefined
  }
  return await $.fs.read(path)
}

const fileSnapshot = async ($: EngineInterface, path: string): Promise<FileSnapshot> => {
  if (!(await $.fs.exists(path))) return { existe: false }
  const content = await $.fs.read(path)
  const escaped = path.replace(/'/g, "''")
  try {
    const result = await $.process.run([
      'powershell',
      '-NoProfile',
      '-Command',
      `$item = Get-Item -LiteralPath '${escaped}'; `
        + '[Console]::Write("$($item.Length)|$($item.LastWriteTimeUtc.Ticks)")',
    ], { timeoutMs: 10000 })
    const [size, ticks] = result.stdout.trim().split('|').map(Number)
    if (Number.isFinite(size) && Number.isFinite(ticks)) {
      return { existe: true, tamanho: size, mtimeMs: (ticks ?? 0) / 10000 }
    }
  } catch {
    // A API de testes não expõe metadados; o tamanho continua sendo um fallback seguro.
  }
  return { existe: true, tamanho: content.length }
}

const fileChanged = (before: FileSnapshot, after: FileSnapshot): boolean => {
  if (!after.existe) return false
  if (!before.existe) return true
  return before.tamanho !== after.tamanho
    || (before.mtimeMs !== undefined && after.mtimeMs !== undefined && before.mtimeMs !== after.mtimeMs)
}

const parentDirectory = (path: string): string => {
  const normalized = path.replace(/[\\/]+$/, '')
  const index = Math.max(normalized.lastIndexOf('/'), normalized.lastIndexOf('\\'))
  return index > 0 ? normalized.slice(0, index) : '.'
}

const ensureOutputDirectory = async ($: EngineInterface, path: string): Promise<void> => {
  const directory = parentDirectory(path)
  if (await $.fs.exists(directory)) return
  const result = await $.process.run(['cmd', '/d', '/c', 'mkdir', directory], { timeoutMs: 10000 })
  if (result.exitCode !== 0 && !(await $.fs.exists(directory))) {
    throw new Error(`diretório de saída não existe: ${directory}`)
  }
}

const resolveOutputPath = (cwd: string, output: string): string =>
  /^(?:[A-Za-z]:[\\/]|[\\/])/.test(output)
    ? output
    : `${cwd.replace(/[\\/]$/, '')}/${output.replace(/^[\\/]+/, '')}`

const finishJob = async (
  $: EngineInterface,
  job: CodexJob,
  exitCode: number | null,
  output: string,
  diagnostics: string,
): Promise<void> => {
  const now = Date.now()
  const answer = await readOutput($, job.saida)
  const questionPath = `${job.cwd}/PERGUNTA.md`
  const questionAfter = await fileSnapshot($, questionPath)
  const hasQuestion = fileChanged(job.perguntaAntes ?? { existe: job.perguntaExistia }, questionAfter)
  const question = hasQuestion ? await $.fs.read(questionPath) : ''
  const outputAfter = await fileSnapshot($, job.saida)
  const outputChanged = fileChanged(job.saidaAntes ?? { existe: false }, outputAfter)
  let status: CodexJob['status'] = 'falhou'
  let motivo = tail(diagnostics || output || `exit code ${exitCode}`)

  if (answer !== undefined && answer.length > 200 && outputChanged) {
    status = 'ok'
    motivo = ''
  } else if (`${output}\n${diagnostics}`.includes('Selected model is at capacity')) {
    motivo = 'modelo sem capacidade, relance com outro modelo'
  } else if (hasQuestion) {
    status = 'pergunta'
    motivo = question
  }

  await updateJob($, job.id, current => ({
    ...current,
    status,
    finishedAt: now,
    motivo: motivo || undefined,
  }))
  $.ui.toast(`${status} ${job.saida}`)
}

const startJob = async (
  $: EngineInterface,
  job: CodexJob,
  prompt: string,
  isGitRepo: boolean,
  codex: string,
): Promise<void> => {
  const argv = [
    codex,
    'exec',
    '-m',
    job.modelo,
    '-c',
    `model_reasoning_effort=${job.effort}`,
    '--approve-for-me',
  ]

  if (!isGitRepo) {
    argv.push('--skip-git-repo-check')
  }

  argv.push('-C', job.cwd, '-o', job.saida, '-')

  try {
    const child = $.process.spawn({ argv, cwd: job.cwd, input: prompt })
    let stdout = ''
    let stderr = ''

    for await (const chunk of child) {
      if (chunk.stream === 'stdout') {
        stdout += chunk.text
      } else {
        stderr += chunk.text
      }
    }

    const result = await child.result
    await finishJob($, job, result.code, stdout, stderr)
  } catch (error) {
    await finishJob($, job, null, '', String(error))
  }
}

const runPreflight = async (
  $: EngineInterface,
  paths: RuntimePaths,
  repoPath: string,
): Promise<void> => {
  const version = await $.process.run([paths.codex, '--version'], { timeoutMs: 15000 })
  if (version.exitCode !== 0) throw new Error(version.stderr || 'codex --version falhou')
  const login = await $.process.run([paths.codex, 'login', 'status'], { timeoutMs: 15000 })
  if (login.exitCode !== 0) throw new Error(login.stderr || 'Codex sem login')
  const line = await $.process.run([
    'python',
    `${pathWithoutTrailingSlash(repoPath)}/skills/codex/status.py`,
    '--line',
  ], { cwd: repoPath, timeoutMs: 30000 })
  if (line.exitCode !== 0) throw new Error(line.stderr || 'status.py --line falhou')
  if (line.stdout.includes('[relogar]')) throw new Error('sessão do Codex expirada')
}

const createOutputPath = async ($: EngineInterface, id: string): Promise<string> => {
  const temp = (await $.env.get('TEMP')) ?? (await $.env.get('TMP')) ?? '.'
  return `${temp.replace(/[\\/]$/, '')}/codex-delegar-${id}.md`
}

const isGitRepository = async (
  $: EngineInterface,
  cwd: string,
  sessionCwd: string,
): Promise<boolean> => {
  if (cwd === sessionCwd) {
    return (await $.session.repo()) !== null
  }

  const result = await $.process.run(['git', '-C', cwd, 'rev-parse', '--show-toplevel'], {
    cwd,
    timeoutMs: 5000,
  })
  return result.exitCode === 0
}

const loadQuota = async ($: EngineInterface, repoPath: string): Promise<void> => {
  const script = `${repoPath.replace(/[\\/]$/, '')}/skills/codex/status.py`

  try {
    const result = await $.process.run(['python', script, '--json'], {
      cwd: repoPath,
      timeoutMs: 30000,
    })
    const parsed = JSON.parse(result.stdout) as QuotaJson
    const activeProfile = await activeCodexProfile($)
    await update($, quota, () => ({ ...parseStatusSnapshot(parsed, activeProfile), fetchedAt: Date.now() }))
  } catch {
    await update($, quota, current => ({ ...current, fetchedAt: Date.now() }))
  }
}

const shortError = (error: unknown): string => tail(String(error)).replace(/\s+/g, ' ').slice(0, 120)

export const spriteNumber = (
  provider: 'claude' | 'codex',
  email: string,
  map: DashboardData['map'],
): number | undefined => map[`${provider}:${email}`]?.numero

export const trainerSpriteKey = (bodyRows: number): 'treinador-p' | 'treinador-g' =>
  bodyRows >= 30 ? 'treinador-g' : 'treinador-p'

export const jobsPollingInterval = (panePlaced: boolean): number => panePlaced ? 3000 : 15000

const loadSprites = async (
  $: EngineInterface,
  accounts: ClaudeAccount[],
  codexAccounts: Array<{ email?: string | null; number?: number }>,
  wild: Array<{ numero: number }>,
  map: DashboardData['map'],
  pokePath: string,
): Promise<Record<string, RasterSprite>> => {
  const requests = new Map<string, string>()
  const add = (key: string, file: string) => requests.set(key, `${pokePath}/terminal/${file}`)
  for (const account of accounts) {
    const number = spriteNumber('claude', account.email, map)
    if (number !== undefined) {
      add(`claude:${account.email}:front-p`, `${number}-frente-p.json`)
      add(`claude:${account.email}:costas-g`, `${number}-costas-g.json`)
    }
  }
  for (const account of codexAccounts) {
    if (!account.email) continue
    const number = spriteNumber('codex', account.email, map)
    if (number !== undefined) {
      add(`codex:${account.email}:front-p`, `${number}-frente-p.json`)
      add(`codex:${account.email}:front-g`, `${number}-frente-g.json`)
    }
  }
  for (const pokemon of wild) {
    add(`wild:${pokemon.numero}`, `${pokemon.numero}-frente-p.json`)
  }
  add('treinador-p', 'treinador-p.json')
  add('treinador-g', 'treinador-g.json')
  const loaded: Record<string, RasterSprite> = {}
  await Promise.all([...requests].map(async ([key, path]) => {
    const sprite = await readSprite($, path)
    if (sprite) loaded[key] = sprite
  }))
  return loaded
}

const refreshDashboard = async ($: EngineInterface, repoPath: string): Promise<void> => {
  const paths = await runtimePaths($)
  const current = await read($, dashboard)
  const next: DashboardData = { ...current, errors: [] }
  const errors: string[] = []
  let codexAccounts: Array<{ email?: string | null }> = []
  const activeProfile = await activeCodexProfile($)

  try {
    const parsed = await runCachedJson<{ accounts?: ClaudeAccount[] }>($, 'cswap', [paths.cswap, 'list', '--json'])
    next.claude = parsed.accounts ?? []
  } catch (error) {
    errors.push(`cswap: ${shortError(error)}`)
  }

  try {
    const parsed = await runCachedJson<QuotaJson>($, 'status', [
      'python',
      `${repoPath.replace(/[\\/]$/, '')}/skills/codex/status.py`,
      '--json',
    ], repoPath)
    codexAccounts = parsed.codex?.contas ?? []
    const codexWindows = parsed.codex?.quotas ?? []
    next.codexFiveHour = codexWindows.find(window => window.janela === 300)?.pct
    next.codexSevenDay = codexWindows.find(window => window.janela === 10080)?.pct
    await update($, quota, () => ({ ...parseStatusSnapshot(parsed, activeProfile), fetchedAt: Date.now() }))
  } catch (error) {
    errors.push(`status: ${shortError(error)}`)
  }

  try {
    next.poke = await readPokeMode($, paths.config)
    const map = await readJson<DashboardData['map']>($, `${paths.poke}/mapa.json`)
    const wild = await readJson<Array<{ numero: number; nome: string } | number>>($, `${paths.poke}/selvagens.json`)
    next.map = map ?? current.map
    next.wild = (wild ?? []).flatMap(item => typeof item === 'number'
      ? [{ numero: item, nome: `#${item}` }]
      : [item])
    next.sprites = await loadSprites($, next.claude, codexAccounts, next.wild, next.map, paths.poke)
  } catch (error) {
    errors.push(`sprites: ${shortError(error)}`)
  }

  next.updatedAt = Date.now()
  const saved = await update($, dashboard, state => ({
    ...next,
    tarefas: state.tarefas,
    resumo: state.resumo,
    cotas: state.cotas,
    errors: [...errors, ...state.errors.filter(error => error.startsWith('jobs:'))],
  }))
  $.ui.status(abovePromptText(saved))
}

const TAREFAS_STALE_MS = 5000

// O status.py é o único escritor do cache; aqui só se lê. Cache velho ou ausente pede um --jobs, que o regrava.
const loadTarefas = async ($: EngineInterface, repoPath: string): Promise<TarefasJson> => {
  const paths = await runtimePaths($)
  const cached = await readJson<TarefasJson>($, paths.jobsCache)
  if (cached?.ts !== undefined && Date.now() - cached.ts * 1000 < TAREFAS_STALE_MS) return cached
  const result = await $.process.run([
    'python',
    `${pathWithoutTrailingSlash(repoPath)}/skills/codex/status.py`,
    '--jobs',
  ], { cwd: repoPath, timeoutMs: 30000 })
  if (result.exitCode !== 0) throw new Error(result.stderr || `jobs exit ${result.exitCode}`)
  return JSON.parse(result.stdout) as TarefasJson
}

const refreshTarefas = async ($: EngineInterface, repoPath: string): Promise<void> => {
  try {
    const parsed = await loadTarefas($, repoPath)
    await update($, dashboard, state => ({
      ...state,
      tarefas: parsed.tarefas ?? [],
      resumo: parsed.resumo,
      cotas: parsed.cotas ?? [],
      errors: state.errors.filter(error => !error.startsWith('jobs:')),
    }))
  } catch (error) {
    await update($, dashboard, state => ({
      ...state,
      errors: [
        ...state.errors.filter(item => !item.startsWith('jobs:')),
        `jobs: ${shortError(error)}`,
      ],
    }))
  }
  const next = await read($, dashboard)
  $.ui.status(abovePromptText(next))
}

const abovePromptText = (data: DashboardData): string => {
  const claude = data.claude.find(account => account.active) ?? data.claude[0]
  const claudeFive = usageOf(claude, 'fiveHour')?.pct
  const claudeSeven = usageOf(claude, 'sevenDay')?.pct
  const running = data.tarefas.flatMap(tarefa => tarefa.agentes).filter(agente => agente.estado === 'rodando')
  const first = running[0]
  const count = data.resumo?.rodando ?? running.length
  const label = data.poke
    ? wildName(data, first)
    : first?.modelo ?? first?.tipo
  const seconds = first?.segundos === null || first?.segundos === undefined ? '' : ` ${formatDuration(first.segundos)}`
  const job = first ? `${count} job(s) (${label ?? '—'}${seconds})` : `${count} job(s)`
  return [
    `claude ${percent(claudeFive)}·${percent(claudeSeven)}`,
    `codex ${percent(data.codexFiveHour)}·${percent(data.codexSevenDay)}`,
    `● ${job}`,
  ].join('  ')
}

const pad2 = (value: number): string => value.toString().padStart(2, '0')

const formatDuration = (seconds: number): string => seconds >= 3600
  ? `${Math.floor(seconds / 3600)}h${pad2(Math.floor(seconds / 60) % 60)}`
  : `${pad2(Math.floor(seconds / 60))}:${pad2(seconds % 60)}`

const wildName = (data: DashboardData, agente: TarefaAgente | undefined): string | undefined => {
  if (!agente || data.wild.length === 0) return undefined
  const wild = data.wild[Number(fnv1a64(agente.id) % BigInt(data.wild.length))]
  return wild?.nome
}

const truncate = (text: string, columns: number): string =>
  columns <= 0 ? '' : text.length <= columns ? text : text.slice(0, Math.max(0, columns - 1)) + '…'

const estadoColor = (estado: TarefaAgente['estado']): 'success' | 'warning' | 'error' | 'subtle' =>
  estado === 'falhou'
    ? 'error'
    : estado === 'pergunta' || estado === 'travado'
      ? 'warning'
      : estado === 'rodando'
        ? 'success'
        : 'subtle'

// tipo · modelo/effort · papel · estado · tempo · progresso (ou o resumo, quando terminou)
const agentLine = (agente: TarefaAgente): string => {
  const terminou = agente.estado === 'concluido' || agente.estado === 'falhou'
  return [
    agente.tipo,
    agente.modelo ? `${modelLabel(agente.modelo)}/${agente.effort ?? '—'}` : '—',
    agente.papel ?? '—',
    agente.estado,
    agente.segundos === null ? '—' : formatDuration(agente.segundos),
    (terminou ? agente.resultado : null) ?? agente.progresso ?? '—',
  ].join(' · ')
}

export const formatReset = (iso: string | null, now: number): string => {
  const date = iso ? new Date(iso) : undefined
  if (!date || Number.isNaN(date.getTime())) return '—'
  const clock = `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
  return new Date(now).toDateString() === date.toDateString()
    ? clock
    : `${pad2(date.getDate())}/${pad2(date.getMonth() + 1)} ${clock}`
}

const quotaLines = (cotas: TarefaCota[], now: number): string[] => {
  const contas = [...new Set(cotas.map(cota => cota.conta))]
  return contas.map(conta => `${conta} ${cotas
    .filter(cota => cota.conta === conta)
    .map(cota => `${windowLabel(cota.janela)} ${percent(cota.pct ?? undefined)}`
      + ` (reseta ${formatReset(cota.resetaEm, now)})`)
    .join(' · ')}`)
}

const renderDashboardPane = async (
  $: EngineInterface,
  e: RenderInput<'Pane'>,
): Promise<RenderElement> => {
  const { Box, Text } = $.ui.resolve(e)
  const data = await read($, dashboard)
  const now = Date.now()
  const columns = e.props.bodyColumns
  const resumo = data.resumo
  const groups = data.tarefas.map(tarefa => (
    <Box key={`tarefa-${tarefa.nome}`} flexDirection="column">
      <Text color="claude">{truncate(tarefa.nome, columns)}</Text>
      {tarefa.agentes.map(agente => (
        <Text key={`agente-${agente.id}`} color={estadoColor(agente.estado)}>
          {truncate(`  ${agentLine(agente)}`, columns)}
        </Text>
      ))}
    </Box>
  ))
  const summary = resumo
    ? `rodando ${resumo.rodando} · feitos hoje ${resumo.feitosHoje} · voltaram ${resumo.voltaram}`
      + ` · falharam ${resumo.falharam}`
    : 'resumo indisponível'
  return (
    <Box flexDirection="column">
      <Text color="success">CENTRO DE COMANDO</Text>
      {groups.length > 0 ? groups : <Text dimColor>nenhuma tarefa nas últimas 2 h</Text>}
      <Text color="subtle">{truncate(summary, columns)}</Text>
      {quotaLines(data.cotas, now).map(line => (
        <Text key={`cota-${line}`} color="subtle">{truncate(line, columns)}</Text>
      ))}
      {data.errors.map(error => <Text key={error} color="error">{truncate(error, columns)}</Text>)}
    </Box>
  )
}

export const register: Register = (on, options) => {
  let sessionCwd = ''
  let nextJob = 0
  let lastSuggestedTurn = 0
  let currentTurn = 0
  let lastJobsPoll = 0
  const filesThisTurn = new Set<string>()

  on('session.start', async ($, e, next) => {
    sessionCwd = e.cwd
    await $.tool.register({
      name: 'delegar_codex',
      description: 'Inicia uma delegação Codex em background e devolve job_id e arquivo de saída.',
      isDeferred: false,
      inputSchema: {
        type: 'object',
        properties: {
          prompt: { type: 'string' },
          modelo: { type: 'string', default: 'gpt-5.6-luna' },
          effort: { type: 'string', enum: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'], default: 'medium' },
          cwd: { type: 'string' },
          saida: { type: 'string' },
          tarefa: { type: 'string' },
          papel: { type: 'string', enum: [...PAPEIS] },
        },
        required: ['prompt'],
      },
    })
    await $.tool.register({
      name: 'codex_job',
      description: 'Consulta o status e a resposta de uma delegação Codex.',
      isDeferred: false,
      inputSchema: {
        type: 'object',
        properties: { job_id: { type: 'string' } },
        required: ['job_id'],
      },
    })
    await $.command.register({
      name: 'codex',
      description: 'Abre o painel de jobs e cota do Codex.',
    })
    void refreshDashboard($, String(options.repoPath))
    $.clock.every(60000, () => refreshDashboard($, String(options.repoPath)))
    $.clock.every(3000, async () => {
      const placed = (await $.ui.panes()).some(pane => pane.id === PANE && pane.isPlaced && pane.isShown)
      const now = Date.now()
      const interval = jobsPollingInterval(placed)
      if (now - lastJobsPoll < interval) return
      lastJobsPoll = now
      void refreshTarefas($, String(options.repoPath))
    })
    return next(e)
  })

  on('tool.call', { tool: 'mcp__codex-delegar__delegar_codex' }, async ($, e) => {
    try {
      const input = e as unknown as DelegarInput
      const paths = await runtimePaths($)
      await runPreflight($, paths, String(options.repoPath))
      const cwd = input.cwd ?? sessionCwd
      const modelo = input.modelo ?? 'gpt-5.6-luna'
      const effort = input.effort ?? 'medium'
      const id = `${Date.now()}-${nextJob++}`
      const saida = resolveOutputPath(cwd, input.saida ?? await createOutputPath($, id))
      const existing = (await read($, jobs)).find(job => job.status === 'rodando' && job.saida === saida)
      if (existing) {
        return { result: JSON.stringify({ status: 'falhou', motivo: `saída já está em uso pelo job ${existing.id}` }) }
      }
      await ensureOutputDirectory($, saida)
      const perguntaPath = `${cwd}/PERGUNTA.md`
      const perguntaAntes = await fileSnapshot($, perguntaPath)
      const saidaAntes = await fileSnapshot($, saida)
      const job: CodexJob = {
        id,
        modelo,
        effort,
        cwd,
        saida,
        status: 'rodando',
        startedAt: Date.now(),
        perguntaExistia: perguntaAntes.existe,
        perguntaAntes,
        saidaAntes,
      }
      await update($, jobs, list => trimJobHistory([...list, job]))
      const isGitRepo = await isGitRepository($, cwd, sessionCwd)
      const prompt = `${jobMarker(id, input.tarefa, input.papel)}\n${input.prompt}`
      void startJob($, job, prompt, isGitRepo, paths.codex)

      const result = { job_id: id, saida, mensagem: 'O resultado chega depois, em background.' }
      return { result: JSON.stringify(result) }
    } catch (error) {
      $.ui.toast(`delegação: ${shortError(error)}`)
      return { result: JSON.stringify({ status: 'falhou', motivo: shortError(error) }) }
    }
  })

  on('tool.call', { tool: 'mcp__codex-delegar__codex_job' }, async ($, e) => {
    const found = (await read($, jobs)).find(job => job.id === (e as unknown as { job_id: string }).job_id)
    if (!found) {
      const result = { status: 'falhou', duracao: 0, motivo: 'job_id não encontrado' }
      return { result: JSON.stringify(result) }
    }

    const now = Date.now()
    const answer = found.status === 'ok' ? await readOutput($, found.saida) : undefined
    const result = {
      status: found.status,
      duracao: duration(found, now),
      ...(answer === undefined ? {} : { conteudo: answer.slice(0, 20000) }),
      ...(found.motivo === undefined ? {} : { motivo: found.motivo }),
    }
    return { result: JSON.stringify(result) }
  })

  on('prompt.submit', async ($, e, next) => {
    currentTurn += 1
    lastSuggestedTurn = currentTurn
    filesThisTurn.clear()
    try {
      await update($, suggestionTurn, () => currentTurn)
      await update($, suggestionCount, () => 0)
    } catch {
      // A sugestão é só um aviso: falha de estado não pode segurar o prompt.
    }
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    if (e.tool !== 'Read' && e.tool !== 'Grep' && e.tool !== 'Edit') {
      return next(e)
    }

    const input = e as unknown as { file_path?: string; path?: string }
    const filePath = input.file_path ?? input.path
    try {
      const turn = currentTurn
      if (turn !== lastSuggestedTurn) {
        lastSuggestedTurn = turn
        await update($, suggestionTurn, () => turn)
        await update($, suggestionCount, () => 0)
      }

      if (filePath !== undefined && !filesThisTurn.has(filePath)) {
        filesThisTurn.add(filePath)
        const count = await update($, suggestionCount, current => current + 1)
        if (count === 3) {
          $.ui.toast('3+ arquivos neste turno: considere delegar_codex')
        }
      }
    } catch {
      // A sugestão é só um aviso: falha de estado não pode segurar a ferramenta.
    }
    return next(e)
  })

  on('command.run', { command: 'codex' }, async ($, e) => {
    if (e.args.trim().toLowerCase() === 'poke') {
      const current = await read($, dashboard)
      await setPokeMode($, !current.poke)
      return { text: `Modo Pokémon ${!current.poke ? 'ligado' : 'desligado'}.` }
    }
    // O render é puro e não grava estado: a recarga sai daqui, do comando que abre o pane.
    lastJobsPoll = Date.now()
    void refreshTarefas($, String(options.repoPath))
    await $.ui.open({ id: PANE, title: 'Codex', rows: 12 })
    return { text: 'Painel do Codex aberto.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => renderDashboardPane($, e))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || e.props.maxRows < 1) return next(e)
    const data = await read($, dashboard)
    const below = await next(e)
    const { Box, Text } = $.ui.resolve(e)
    const line = truncate(abovePromptText(data), e.props.bodyColumns)
    return (
      <Box flexDirection="column">
        <Text color="subtle">{line}</Text>
        {below}
      </Box>
    )
  })
}
