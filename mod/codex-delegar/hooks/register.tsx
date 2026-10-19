import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { CodexJob, CodexQuota } from '../types'

const PANE = 'codex'
const jobs = atom({ plugin: 'codex-delegar', key: 'jobs' } as const, [] as CodexJob[])
const quota = atom({ plugin: 'codex-delegar', key: 'quota' } as const, {
  windows: [],
  contas: [],
} as CodexQuota)
const suggestionTurn = atom({ plugin: 'codex-delegar', key: 'suggestionTurn' } as const, 0)
const suggestionCount = atom({ plugin: 'codex-delegar', key: 'suggestionCount' } as const, 0)

type DelegarInput = {
  prompt: string
  modelo?: string
  effort?: 'low' | 'medium' | 'high'
  cwd?: string
  saida?: string
}

type QuotaJson = {
  codex?: {
    tokens?: number
    quota_pct?: number
    quotas?: Array<{ janela: number; pct: number }>
    contas?: Array<{ perfil: string; email: string; plano: string; status: 'ok' | 'expirado'; tokens: number }>
  }
}

const tail = (text: string): string => text.trim().slice(-1000)

export const selectQuotaAccount = (currentQuota: CodexQuota): CodexQuota['contas'][number] | undefined =>
  currentQuota.contas.find(item => item.tokens === currentQuota.tokens)
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
  return `${label} ${item.pct}%`
}).join(' | ') || 'cota indisponível'

const duration = (job: CodexJob, now: number): number =>
  Math.max(0, Math.round(((job.finishedAt ?? now) - job.startedAt) / 1000))

const updateJob = async (
  $: Parameters<typeof read>[0],
  id: string,
  change: (job: CodexJob) => CodexJob,
): Promise<void> => {
  await update($, jobs, list => list.map(job => job.id === id ? change(job) : job))
}

const readOutput = async ($: Parameters<typeof read>[0], path: string): Promise<string | undefined> => {
  if (!(await $.fs.exists(path))) {
    return undefined
  }
  return await $.fs.read(path)
}

const finishJob = async (
  $: Parameters<typeof read>[0],
  job: CodexJob,
  exitCode: number | null,
  output: string,
  diagnostics: string,
): Promise<void> => {
  const now = Date.now()
  const answer = await readOutput($, job.saida)
  const questionPath = `${job.cwd}/PERGUNTA.md`
  const hasQuestion = await $.fs.exists(questionPath)
  const question = hasQuestion ? await $.fs.read(questionPath) : ''
  let status: CodexJob['status'] = 'falhou'
  let motivo = tail(diagnostics || output || `exit code ${exitCode}`)

  if (`${output}\n${diagnostics}`.includes('Selected model is at capacity')) {
    motivo = 'modelo sem capacidade, relançar em gpt-5.6-terra'
  } else if (!job.perguntaExistia && hasQuestion) {
    status = 'pergunta'
    motivo = question
  } else if (answer !== undefined && answer.length > 200) {
    status = 'ok'
    motivo = ''
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
  $: Parameters<typeof read>[0],
  job: CodexJob,
  prompt: string,
  isGitRepo: boolean,
): Promise<void> => {
  const appData = (await $.env.get('APPDATA')) ?? ''
  const codex = `${appData.replace(/[\\/]$/, '')}/npm/codex.cmd`
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

const createOutputPath = async ($: Parameters<typeof read>[0], id: string): Promise<string> => {
  const temp = (await $.env.get('TEMP')) ?? (await $.env.get('TMP')) ?? '.'
  return `${temp.replace(/[\\/]$/, '')}/codex-delegar-${id}.md`
}

const isGitRepository = async (
  $: Parameters<typeof read>[0],
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

const loadQuota = async ($: Parameters<typeof read>[0], repoPath: string): Promise<void> => {
  const script = `${repoPath.replace(/[\\/]$/, '')}/skills/codex/status.py`

  try {
    const result = await $.process.run(['python', script, '--json'], {
      cwd: repoPath,
      timeoutMs: 30000,
    })
    const parsed = JSON.parse(result.stdout) as QuotaJson
    const codex = parsed.codex ?? {}
    await update($, quota, () => ({
      quotaPct: codex.quota_pct,
      tokens: codex.tokens,
      windows: codex.quotas ?? [],
      contas: codex.contas ?? [],
      fetchedAt: Date.now(),
    }))
  } catch {
    await update($, quota, current => ({ ...current, fetchedAt: Date.now() }))
  }
}

export const register: Register = (on, options) => {
  let sessionCwd = ''
  let nextJob = 0
  let lastSuggestedTurn = 0
  let currentTurn = 0
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
          effort: { type: 'string', enum: ['low', 'medium', 'high'], default: 'medium' },
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
    void loadQuota($, String(options.repoPath))
    $.clock.every(60000, () => loadQuota($, String(options.repoPath)))
    return next(e)
  })

  on('tool.call', { tool: 'mcp__codex-delegar__delegar_codex' }, async ($, e) => {
    const input = e as unknown as DelegarInput
    const cwd = input.cwd ?? sessionCwd
    const modelo = input.modelo ?? 'gpt-5.6-luna'
    const effort = input.effort ?? 'medium'
    const id = `${Date.now()}-${nextJob++}`
    const saida = input.saida ?? await createOutputPath($, id)
    const perguntaPath = `${cwd}/PERGUNTA.md`
    const perguntaExistia = await $.fs.exists(perguntaPath)
    const job: CodexJob = {
      id,
      modelo,
      effort,
      cwd,
      saida,
      status: 'rodando',
      startedAt: Date.now(),
      perguntaExistia,
    }
    await update($, jobs, list => [...list, job])
    const isGitRepo = await isGitRepository($, cwd, sessionCwd)
    void startJob($, job, input.prompt, isGitRepo)

    const result = { job_id: id, saida, mensagem: 'O resultado chega depois, em background.' }
    return { result: JSON.stringify(result) }
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
    await update($, suggestionTurn, () => currentTurn)
    await update($, suggestionCount, () => 0)
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    if (e.tool !== 'Read' && e.tool !== 'Grep' && e.tool !== 'Edit') {
      return next(e)
    }

    const turn = currentTurn
    if (turn !== lastSuggestedTurn) {
      lastSuggestedTurn = turn
      await update($, suggestionTurn, () => turn)
      await update($, suggestionCount, () => 0)
    }

    const input = e as unknown as { file_path?: string; path?: string }
    const filePath = input.file_path ?? input.path
    if (filePath === undefined || filesThisTurn.has(filePath)) {
      return next(e)
    }

    filesThisTurn.add(filePath)
    const count = await update($, suggestionCount, current => current + 1)
    if (count === 3) {
      $.ui.toast('3+ arquivos neste turno: considere delegar_codex')
    }
    return next(e)
  })

  on('command.run', { command: 'codex' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Codex', rows: 12 })
    return { text: 'Painel do Codex aberto.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const list = await read($, jobs)
    const currentQuota = await read($, quota)
    const now = Date.now()
    const account = selectQuotaAccount(currentQuota)
    const windows = formatQuotaWindows(currentQuota)
    const lines = list.length === 0
      ? [<Text dimColor>Nenhum job nesta sessão.</Text>]
      : list.slice(-20).map(job => (
        <Text key={job.id}>
          {job.id} {job.modelo} {job.status} {duration(job, now)}s {job.saida}
        </Text>
      ))

    return (
      <Box flexDirection="column">
        <Text>conta: {account?.email ?? 'indisponível'} | {windows}</Text>
        {lines}
      </Box>
    )
  })
}
