import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderInput } from 'claude-code'

import type {
  ClaudeAccount,
  CodexJob,
  CodexQuota,
  DashboardData,
  DashboardJob,
  FileSnapshot,
  RasterSprite,
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
  running: [],
  poke: true,
  map: {},
  wild: [],
  sprites: {},
  errors: [],
} as DashboardData)

type DelegarInput = {
  prompt: string
  modelo?: string
  effort?: CodexJob['effort']
  cwd?: string
  saida?: string
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
    codex: (await $.env.get('CODEX_CLI')) ?? `${appData}/npm/codex.cmd`,
  }
}

const activeCodexProfile = async ($: EngineInterface): Promise<string> => pathWithoutTrailingSlash(
  (await $.env.get('CODEX_HOME')) ?? `${(await $.env.get('USERPROFILE')) ?? WINDOWS_HOME_FALLBACK}/.codex`,
).split(/[\\/]/).pop() ?? '.codex'

type JobsJson = { jobs?: DashboardJob[] }
type CacheEntry<T> = { ts: number; data: T }
type SharedCache = { cswap?: CacheEntry<unknown>; status?: CacheEntry<unknown> }
type SessionAgent = {
  id: string
  description: string
  type: string
  status: 'pending' | 'running' | 'waiting' | 'idle' | 'completed' | 'failed' | 'killed'
  name?: string
}

const activeAgentStatuses = new Set<SessionAgent['status']>(['pending', 'running', 'waiting'])

const emptyDashboard = (): DashboardData => ({
  claude: [],
  codexFiveHour: undefined,
  codexSevenDay: undefined,
  running: [],
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

const compactClock = (clock: string | undefined): string | undefined => {
  if (!clock) return undefined
  const match = clock.match(/^([A-Z][a-z]{2}) (\d{1,2}) (\d{2}:\d{2})$/)
  if (!match) return clock
  const months: Record<string, string> = {
    Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06',
    Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12',
  }
  return `${(match[2] ?? '').padStart(2, '0')}/${months[match[1] ?? ''] ?? match[1] ?? ''} ${match[3] ?? ''}`
}

const percent = (value: number | undefined): string => value === undefined ? '—' : `${Math.round(value)}%`

const bar = (value: number | undefined): string => {
  const filled = value === undefined ? 0 : Math.round(Math.max(0, Math.min(100, value)) / 10)
  return `${'█'.repeat(filled)}${'░'.repeat(10 - filled)}`
}

const modelLabel = (model: string): string => model.replace(/^gpt-/, '')

const jobSeconds = (job: CodexJob, now: number): number => duration(job, now)

const localJobs = (list: CodexJob[], now: number): DashboardJob[] => list
  .filter(job => job.status === 'rodando')
  .map(job => ({
    tipo: 'codex',
    modelo: job.modelo,
    projeto: job.cwd.split(/[\\/]/).pop() ?? job.cwd,
    segundos: jobSeconds(job, now),
    arquivo: job.saida,
  }))

const readSprite = async ($: EngineInterface, path: string): Promise<RasterSprite | undefined> => {
  return await readJson<RasterSprite>($, path)
}

export const selectQuotaAccount = (currentQuota: CodexQuota): CodexQuota['contas'][number] | undefined =>
  currentQuota.contas.find(item => item.perfil === currentQuota.activeProfile)
    ?? currentQuota.contas.find(item => item.tokens === currentQuota.tokens)
    ?? currentQuota.contas.find(item => item.status === 'ok')
    ?? currentQuota.contas[0]

export const formatQuotaWindows = (currentQuota: CodexQuota): string => currentQuota.windows.map(item => {
  const label = item.janela === 300
    ? '5h'
    : item.janela === 10080
      ? '7d'
      : item.janela === 43200
        ? '30d'
        : `${item.janela}min`
  return `${label} ${item.pct === undefined ? '—' : `${item.pct}%`}`
}).join(' | ') || 'cota indisponível'

export const mergeSessionJobs = (
  source: DashboardJob[],
  agents: SessionAgent[],
  sessionId: string,
): DashboardJob[] => {
  const sessionJobs = source.filter(job => job.tipo === 'claude' && job.arquivo.includes(sessionId))
  const matched = new Set<string>()
  const sessionRows = agents.filter(agent => activeAgentStatuses.has(agent.status)).map(agent => {
    const job = sessionJobs.find(item => item.arquivo.includes(agent.id))
    if (job) {
      matched.add(job.arquivo)
      return { ...job, agentId: agent.id, agentStatus: agent.status, projeto: agent.name ?? agent.description }
    }
    return {
      tipo: 'claude' as const,
      modelo: agent.type,
      projeto: agent.name ?? agent.description,
      arquivo: `agent:${agent.id}`,
      agentId: agent.id,
      agentStatus: agent.status,
    }
  })
  const terminatedMatches = new Set(sessionJobs.filter(job => agents.some(agent =>
    job.arquivo.includes(agent.id) && !activeAgentStatuses.has(agent.status))).map(job => job.arquivo))
  return [...sessionRows, ...source.filter(job => !matched.has(job.arquivo) && !terminatedMatches.has(job.arquivo))]
}

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

const jobsForSession = async (
  $: EngineInterface,
  source: DashboardJob[],
): Promise<DashboardJob[]> => mergeSessionJobs(source, await $.agent.list() as SessionAgent[], await $.session.id())

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
    const result = await $.process.run([
      'python',
      `${repoPath.replace(/[\\/]$/, '')}/skills/codex/status.py`,
      '--jobs',
    ], { cwd: repoPath, timeoutMs: 30000 })
    if (result.exitCode !== 0) throw new Error(result.stderr || `jobs exit ${result.exitCode}`)
    const parsed = JSON.parse(result.stdout) as JobsJson
    const session = await jobsForSession($, parsed.jobs ?? [])
    const local = localJobs(await read($, jobs), Date.now())
    const all = [...session, ...local]
    next.running = all.filter((job, index, list) => list.findIndex(item => item.arquivo === job.arquivo) === index)
  } catch (error) {
    errors.push(`jobs: ${shortError(error)}`)
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

  next.errors = errors
  next.updatedAt = Date.now()
  await update($, dashboard, () => next)
  const summary = abovePromptText(next)
  $.ui.status(summary)
}

const refreshJobs = async ($: EngineInterface, repoPath: string): Promise<void> => {
  const current = await read($, dashboard)
  try {
    const result = await $.process.run([
      'python',
      `${repoPath.replace(/[\\/]$/, '')}/skills/codex/status.py`,
      '--jobs',
    ], { cwd: repoPath, timeoutMs: 30000 })
    if (result.exitCode !== 0) throw new Error(result.stderr || `jobs exit ${result.exitCode}`)
    const parsed = JSON.parse(result.stdout) as JobsJson
    const session = await jobsForSession($, parsed.jobs ?? [])
    const local = localJobs(await read($, jobs), Date.now())
    const all = [...session, ...local]
    const running = all.filter((job, index, list) => list.findIndex(item => item.arquivo === job.arquivo) === index)
    await update($, dashboard, state => ({
      ...state,
      running,
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
  const first = data.running[0]
  const label = data.poke
    ? wildName(data, first)
    : first?.modelo
  const job = first ? `${data.running.length} job(s) (${label ?? '—'}${first.segundos === undefined ? '' : ` ${formatDuration(first.segundos)}`})` : '0 job(s)'
  return [
    `claude ${percent(claudeFive)}·${percent(claudeSeven)}`,
    `codex ${percent(data.codexFiveHour)}·${percent(data.codexSevenDay)}`,
    `● ${job}`,
  ].join('  ')
}

const formatDuration = (seconds: number): string =>
  `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`

const wildName = (data: DashboardData, job: DashboardJob | undefined): string | undefined => {
  if (!job || data.wild.length === 0) return undefined
  const wild = data.wild[Number(fnv1a64(job.arquivo) % BigInt(data.wild.length))]
  return wild?.nome
}

const truncate = (text: string, columns: number): string =>
  columns <= 0 ? '' : text.length <= columns ? text : text.slice(0, Math.max(0, columns - 1)) + '…'

const accountName = (email: string): string => email.split('@')[0] || email

const accountSpecies = (data: DashboardData, provider: 'claude' | 'codex', email: string): string | undefined =>
  data.map[`${provider}:${email}`]?.nome

const sprite = (
  data: DashboardData,
  provider: 'claude' | 'codex',
  email: string,
  pose: string,
  key: string,
  terminal: boolean,
  Raster: any,
) => {
  const cells = data.sprites[`${provider}:${email}:${pose}`]
  if (!terminal || !cells) return null
  return <Raster key={key} columns={cells.cols} rows={cells.rows} cells={cells.cells} />
}

const trainerSprite = (data: DashboardData, Raster: any, terminal: boolean, bodyRows: number) => {
  const cells = data.sprites[trainerSpriteKey(bodyRows)]
  if (!terminal || !cells) return null
  return <Raster key="trainer-sprite" columns={cells.cols} rows={cells.rows} cells={cells.cells} />
}

const quotaCell = (
  account: ClaudeAccount,
  window: 'fiveHour' | 'sevenDay',
  width: number,
) => {
  const value = usageOf(account, window)
  const age = account.usage?.[window] ? undefined : account.lastGoodAgeSeconds
  const ageText = age === undefined ? '' : ` ${Math.max(1, Math.round(age / 60))}m`
  const details = `${percent(value?.pct)}${ageText}`
  const countdown = value?.countdown ?? '—'
  const clock = compactClock(value?.clock)
  const suffix = width > 35 ? ` ${countdown}${width > 48 ? ` ${clock ?? ''}` : ''}` : ''
  return `${bar(value?.pct)} ${details}${suffix}`
}

const renderClaudeAccount = (
  Text: any,
  Box: any,
  Raster: any,
  data: DashboardData,
  account: ClaudeAccount,
  columns: number,
  terminal: boolean,
) => {
  const five = usageOf(account, 'fiveHour')?.pct
  const seven = usageOf(account, 'sevenDay')?.pct
  const fainted = [five, seven].some(value => (value ?? 0) >= 100)
  const species = accountSpecies(data, 'claude', account.email)
  const label = data.poke && species ? `${species} · ${account.email}` : account.email
  const marks = account.usageStatus === 'relogin_required'
    ? ' relogar'
    : fainted
      ? ' desmaiou'
      : ''
  return (
    <Box key={`claude-${account.email}`} flexDirection="row" gap={1}>
      {sprite(
        data,
        'claude',
        account.email,
        account.active ? 'costas-g' : 'front-p',
        `claude-sprite-${account.email}`,
        terminal,
        Raster,
      )}
      <Text dimColor={fainted}>
        {account.active ? '● ' : '○ '}{truncate(label, Math.max(12, columns - 42))}{marks}
      </Text>
      <Text color={usageColor(five)}>{quotaCell(account, 'fiveHour', columns)}</Text>
      <Text color={usageColor(seven)}>{quotaCell(account, 'sevenDay', columns)}</Text>
    </Box>
  )
}

const renderDashboardPane = async (
  $: EngineInterface,
  e: RenderInput<'Pane'>,
): Promise<unknown> => {
  const { Box, Text, Button, Raster } = $.ui.resolve(e)
  const data = await read($, dashboard)
  const currentQuota = await read($, quota)
  const list = await read($, jobs)
  const terminal = e.surface === 'terminal'
  const now = Date.now()
  const activeClaude = data.claude.find(account => account.active) ?? data.claude[0]
  const codex = selectQuotaAccount(currentQuota)
  const running = data.running
  const current = list.find(job => job.status === 'rodando')
  const currentLabel = current
    ? `${current.cwd.split(/[\\/]/).pop() ?? current.cwd} · ${formatDuration(duration(current, now))}`
    : 'aguardando delegação'
  const codexModel = modelLabel(currentQuota.model ?? current?.modelo ?? '—')
  const codexEffort = currentQuota.effort ?? current?.effort ?? '—'
  const pokeButton = (
    <Button key="poke" hotkey="p" plain onPress={async () => setPokeMode($, !data.poke)}>
      {data.poke ? 'p: sóbrio' : 'p: Pokémon'}
    </Button>
  )
  const errors = data.errors.map(error => <Text key={error} color="error">{error}</Text>)
  if (data.poke) {
    const wildRows = running.map(job => (
      <Text key={`wild-${job.arquivo}`}>
        {wildName(data, job) ?? 'selvagem'} selvagem · {job.projeto}{job.segundos === undefined ? '' : ` · ${formatDuration(job.segundos)}`}
      </Text>
    ))
    return (
      <Box flexDirection="column">
        <Box flexDirection="row" justifyContent="space-between">
          <Text color="success">CENTRO DE COMANDO // HERMES</Text>
          {pokeButton}
        </Box>
        <Box flexDirection="row" gap={1}>
          {activeClaude
            ? sprite(data, 'claude', activeClaude.email, 'costas-g', 'active-claude', terminal, Raster)
            : null}
          {trainerSprite(data, Raster, terminal, e.props.scroll.bodyRows)}
          <Text color="claude">
            CLAUDE · {accountName(activeClaude?.email ?? '—')} · HP{' '}
            {percent(activeClaude
              ? 100 - (usageOf(activeClaude, 'fiveHour')?.pct ?? 0)
              : undefined)}
          </Text>
        </Box>
        <Text color="subtle">delegação atual · {currentLabel}</Text>
        <Box flexDirection="row" gap={1}>
          {codex?.email ? sprite(data, 'codex', codex.email, 'front-g', 'active-codex', terminal, Raster) : null}
          <Text color="success">CODEX · {accountName(codex?.email ?? '—')} · {codexModel}/{codexEffort}</Text>
        </Box>
        <Text color="subtle">RODANDO AGORA</Text>
        {wildRows.length > 0 ? wildRows : <Text dimColor>nenhuma delegação em andamento</Text>}
        <Text color="subtle">CLAUDE</Text>
        {data.claude.map(account => renderClaudeAccount(
          Text,
          Box,
          Raster,
          data,
          account,
          e.props.bodyColumns,
          terminal,
        ))}
        <Text color="subtle">CODEX</Text>
        <Text>
          {codex
            ? `${accountName(codex.email ?? '—')} · ${codex.plano} · ${codexModel}/${codexEffort}`
            : 'carregando…'}
        </Text>
        {errors}
      </Box>
    )
  }
  return (
    <Box flexDirection="column">
      <Box flexDirection="row" justifyContent="space-between"><Text color="success">AGORA</Text>{pokeButton}</Box>
      <Box flexDirection="row" gap={2}>
        <Text color="claude">CLAUDE · {accountName(activeClaude?.email ?? '—')}</Text>
        <Text color="subtle">{currentLabel}</Text>
        <Text color="success">CODEX · {accountName(codex?.email ?? '—')} · {codexModel}/{codexEffort}</Text>
      </Box>
      <Text color="subtle">RODANDO AGORA</Text>
      {running.length > 0 ? running.map(job => (
        <Text key={`job-${job.arquivo}`}>
          {job.agentStatus ? `${job.agentStatus} ` : ''}
          {job.tipo.toUpperCase()} · {modelLabel(job.modelo)} | {job.projeto}{job.segundos === undefined ? '' : ` | ${formatDuration(job.segundos)}`}
        </Text>
      )) : <Text dimColor>nenhuma delegação em andamento</Text>}
      <Text color="subtle">CLAUDE</Text>
      {data.claude.map(account => renderClaudeAccount(
        Text,
        Box,
        Raster,
        data,
        account,
        e.props.bodyColumns,
        terminal,
      ))}
      <Text color="subtle">CODEX</Text>
      <Text>
        {codex
          ? `${accountName(codex.email ?? '—')} · ${codex.plano} · ${codexModel} · ${codexEffort}`
          : 'carregando…'}
      </Text>
      {errors}
    </Box>
  )
}

export const register: Register = (on, options) => {
  let sessionCwd = ''
  let nextJob = 0
  let lastSuggestedTurn = 0
  let currentTurn = 0
  let lastJobsPoll = 0
  let refreshPaneOnRender = false
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
      void refreshJobs($, String(options.repoPath))
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
      void startJob($, job, input.prompt, isGitRepo, paths.codex)

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
    refreshPaneOnRender = true
    await $.ui.open({ id: PANE, title: 'Codex', rows: 12 })
    return { text: 'Painel do Codex aberto.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    if (refreshPaneOnRender) {
      refreshPaneOnRender = false
      lastJobsPoll = Date.now()
      void refreshJobs($, String(options.repoPath))
    }
    return renderDashboardPane($, e)
  })

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
