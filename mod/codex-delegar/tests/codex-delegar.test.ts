import { expect, mock, test } from 'claude-code/testing'

declare function setTimeout(callback: (...args: any[]) => void, ms: number): unknown
import {
  fnv1a64,
  formatQuotaWindows,
  cacheIsFresh,
  jobsPollingInterval,
  mergeCache,
  mergeSessionJobs,
  mergePokeConfig,
  parseStatusSnapshot,
  selectQuotaAccount,
  spriteNumber,
  trainerSpriteKey,
  usageColor,
} from '../hooks/register'

const options = { repoPath: 'C:/repo' }

const processResult = (exitCode: number, stdout = '', stderr = '') => ({ value: {
  exitCode,
  stdout,
  stderr,
  isStdoutTruncated: false,
  isStderrTruncated: false,
} })

// git fora de repo, pré-voo ok e metadados do PowerShell; com changedOutput a saída muda depois do job.
const installDelegationRun = (on: any, changedOutput = false): void => {
  let metadataCall = 0
  on('process.run', ($: unknown, e: { argv?: readonly string[] }) => {
    const command = e.argv?.[0] ?? ''
    if (command === 'git') return processResult(128, '', 'not a git repository')
    if (command === 'powershell') {
      metadataCall += 1
      return processResult(0, changedOutput && metadataCall >= 4 ? '200|2' : '1|1')
    }
    return processResult(0)
  })
}

test('FNV-1a 64-bit matches the known vectors', { options }, () => {
  expect(fnv1a64('')).toBe(0xcbf29ce484222325n)
  expect(fnv1a64('hello')).toBe(0xa430d84680aabd0bn)
})

test('usage color follows the panel bands', { options }, () => {
  expect(usageColor(59)).toBe('success')
  expect(usageColor(60)).toBe('warning')
  expect(usageColor(85)).toBe('warning')
  expect(usageColor(86)).toBe('error')
})

test('sprites use only the mapa.json number, never the cswap slot', { options }, () => {
  const map = {
    'claude:a@x': { numero: 25, nome: 'A' },
    'codex:b@x': { numero: 26, nome: 'B' },
  }
  expect(spriteNumber('claude', 'a@x', map)).toBe(25)
  expect(spriteNumber('codex', 'b@x', map)).toBe(26)
  expect(spriteNumber('claude', 'missing@x', map)).toBeUndefined()
})

test('shared cache has a 60 second TTL and preserves the other provider', { options }, () => {
  const cache = mergeCache({ cswap: { ts: 900, data: { accounts: [] } } }, 'status', { codex: {} }, 1000)
  expect(cacheIsFresh(cache.cswap, 59900)).toBe(true)
  expect(cacheIsFresh(cache.cswap, 60900)).toBe(false)
  expect(cache.cswap?.data).toEqual({ accounts: [] })
})

test('job polling is adaptive and trainer uses the compact sprite by default', { options }, () => {
  expect(jobsPollingInterval(true)).toBe(3000)
  expect(jobsPollingInterval(false)).toBe(15000)
  expect(trainerSpriteKey(29)).toBe('treinador-p')
  expect(trainerSpriteKey(30)).toBe('treinador-g')
})

test('session agents include only active agents and use the matched job duration', { options }, () => {
  const jobs = [
    { tipo: 'claude' as const, modelo: 'opus', projeto: 'old', segundos: 4, arquivo: 'session-abc-agent-1.jsonl' },
    { tipo: 'claude' as const, modelo: 'opus', projeto: 'other', segundos: 5, arquivo: 'session-abc-agent-2.jsonl' },
  ]
  const merged = mergeSessionJobs(jobs, [
    { id: 'agent-1', description: 'first', type: 'Explore', status: 'waiting' as const },
  ], 'session-abc')
  expect(merged[0]?.agentStatus).toBe('waiting')
  expect(merged[0]?.segundos).toBe(4)
  expect(merged.filter(job => job.arquivo === 'session-abc-agent-1.jsonl')).toHaveLength(1)
  expect(merged[1]?.arquivo).toBe('session-abc-agent-2.jsonl')
})

test('completed agents and their matched status jobs are not shown', { options }, () => {
  const merged = mergeSessionJobs([
    { tipo: 'claude' as const, modelo: 'opus', projeto: 'done', segundos: 9, arquivo: 'session-abc-agent-done.jsonl' },
    { tipo: 'claude' as const, modelo: 'opus', projeto: 'live', segundos: 7, arquivo: 'session-abc-agent-live.jsonl' },
  ], [
    { id: 'agent-done', description: 'done', type: 'Explore', status: 'completed' as const },
    { id: 'agent-live', description: 'live', type: 'Explore', status: 'running' as const },
  ], 'session-abc')
  expect(merged.some(job => job.agentId === 'agent-done')).toBe(false)
  expect(merged.some(job => job.arquivo.includes('agent-done'))).toBe(false)
  expect(merged.find(job => job.agentId === 'agent-live')?.segundos).toBe(7)
})

test('poke toggle preserves unrelated config keys', { options }, () => {
  expect(JSON.parse(mergePokeConfig({ tema: 'escuro', modo_poke: false }, true))).toEqual({
    tema: 'escuro',
    modo_poke: true,
  })
})

test('delegar_codex monta argv, stdin e skip-git-repo-check fora de git', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  let request: { argv: readonly string[]; input?: string } | undefined
  installDelegationRun(on)
  on('process.spawn', async function* (_$, e) {
    request = e
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'linha 1\nlinha 2',
    cwd: 'C:/fora',
  })
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
  await new Promise(resolve => setTimeout(resolve, 25))
  expect(typeof call.result).toBe('string')
  expect(call.result).toContain('O resultado chega depois')
  const delegation = JSON.parse(String(call.result)) as { job_id: string; saida: string }
  expect(delegation.job_id).toBeTruthy()
  expect(delegation.saida).toBe('C:/Temp/codex-delegar-' + delegation.job_id + '.md')
  expect(request?.argv).toContain('--approve-for-me')
  expect(request?.argv).toContain('--skip-git-repo-check')
  expect(request?.input).toBe('linha 1\nlinha 2')
})

test('exit 0 sem arquivo de saída deixa o job falhou', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  on('process.run', () => ({ value: {
    exitCode: 0,
    stdout: '',
    stderr: '',
    isStdoutTruncated: false,
    isStderrTruncated: false,
  } }))
  on('process.spawn', async function* () {
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({ tool: 'mcp__codex-delegar__delegar_codex', prompt: 'teste', cwd: 'C:/outro' })
  const job = JSON.parse(String(call.result)) as { job_id: string }
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
  await new Promise(resolve => setTimeout(resolve, 25))
  const status = await $.tool.call({ tool: 'mcp__codex-delegar__codex_job', job_id: job.job_id })
  expect(status.result).toContain('falhou')
})

test('sugestao aparece uma vez no terceiro arquivo do turno', { options }, async ($, on) => {
  const toasts: string[] = []
  on('prompt.submit', ($, e) => ({ text: e.text }))
  on('tool.call', () => ({ result: {}, text: '' }))
  on('ui.toast', ($, e, next) => {
    toasts.push(e.text)
    return next(e)
  })
  await $.prompt.submit({ text: 'novo turno', wait: false, origin: { kind: 'composer' } })
  await $.tool.call({ tool: 'Read', file_path: 'Read.md' })
  await $.tool.call({ tool: 'Grep', pattern: 'Grep', path: 'Grep.md' })
  await $.tool.call({ tool: 'Edit', file_path: 'Edit.md', old_string: 'old', new_string: 'new' })
  await $.tool.call({ tool: 'Read', file_path: 'Read.md' })
  expect(toasts).toEqual(['3+ arquivos neste turno: considere delegar_codex'])
})

test('capacity in process diagnostics marks the job to retry', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  on('process.run', () => ({ value: {
    exitCode: 0,
    stdout: '',
    stderr: '',
    isStdoutTruncated: false,
    isStderrTruncated: false,
  } }))
  on('process.spawn', async function* () {
    yield { stream: 'stderr', text: 'Selected model is at capacity' }
    return { value: { code: 1, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({ tool: 'mcp__codex-delegar__delegar_codex', prompt: 'teste', cwd: 'C:/outro' })
  const job = JSON.parse(String(call.result)) as { job_id: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  const status = await $.tool.call({ tool: 'mcp__codex-delegar__codex_job', job_id: job.job_id })
  expect(status.result).toContain('modelo sem capacidade')
})

test('preexisting PERGUNTA.md does not turn the job into a question', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  let existsCalls = 0
  on('process.run', () => ({ value: {
    exitCode: 0,
    stdout: '',
    stderr: '',
    isStdoutTruncated: false,
    isStderrTruncated: false,
  } }))
  on('process.spawn', async function* () {
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: ++existsCalls === 2 || existsCalls === 5 }))
  on('fs.read', () => ({ value: 'pergunta antiga' }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({ tool: 'mcp__codex-delegar__delegar_codex', prompt: 'teste', cwd: 'C:/outro' })
  const job = JSON.parse(String(call.result)) as { job_id: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  const status = await $.tool.call({ tool: 'mcp__codex-delegar__codex_job', job_id: job.job_id })
  expect(status.result).toContain('falhou')
  expect(status.result).not.toContain('pergunta')
})

test('Pane uses active tokens and formats numeric windows', { options }, async ($, on) => {
  on('process.run', () => ({ value: {
    exitCode: 0,
    stdout: JSON.stringify({ codex: {
      tokens: 1441671359,
      quotas: [{ janela: 300, pct: 1 }, { janela: 10080, pct: 14 }, { janela: 43200, pct: 2 }, { janela: 17, pct: 3 }],
      contas: [
        { perfil: '.codex', email: 'a@x', plano: 'free', status: 'expirado', tokens: 543129333 },
        { perfil: '.codex-b', email: 'b@x', plano: 'team', status: 'ok', tokens: 1441671359 },
      ],
    } }),
    stderr: '',
    isStdoutTruncated: false,
    isStderrTruncated: false,
  } }))
  on('tool.register', () => ({ value: { tool: 'codex-delegar' } }))
  on('command.register', () => ({ value: { command: 'codex' } }))
  on('clock.every', () => ({ value: undefined }))
  on('session.start', ($, e, next) => next(e))
  const quota = {
    tokens: 1441671359,
    windows: [{ janela: 300, pct: 1 }, { janela: 10080, pct: 14 }, { janela: 43200, pct: 2 }, { janela: 17, pct: 3 }],
    contas: [
      { perfil: '.codex', email: 'a@x', plano: 'free', status: 'expirado' as const, tokens: 543129333 },
      { perfil: '.codex-b', email: 'b@x', plano: 'team', status: 'ok' as const, tokens: 1441671359 },
    ],
  }
  expect(selectQuotaAccount(quota)?.email).toBe('b@x')
  expect(formatQuotaWindows(quota)).toBe('5h 1% | 7d 14% | 30d 2% | 17min 3%')
})

test('job created question file is reported as a question', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  let existsCalls = 0
  on('process.run', () => ({ value: {
    exitCode: 0,
    stdout: '',
    stderr: '',
    isStdoutTruncated: false,
    isStderrTruncated: false,
  } }))
  on('process.spawn', async function* () {
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: ++existsCalls === 5 }))
  on('fs.read', () => ({ value: 'need clarification' }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({ tool: 'mcp__codex-delegar__delegar_codex', prompt: 'teste', cwd: 'C:/outro' })
  const job = JSON.parse(String(call.result)) as { job_id: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  const status = await $.tool.call({ tool: 'mcp__codex-delegar__codex_job', job_id: job.job_id })
  expect(status.result).toContain('pergunta')
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`Pane monta em ${surface}`, { options }, async ($, on) => {
    const mounted = await $.ui.mount({ plugin: 'codex-delegar', surface, component: 'Pane', requestId: 'codex', props: {
      title: 'Codex',
      isFocused: false,
      bodyColumns: 80,
      placement: 'inline',
      scroll: { offset: 0, bodyRows: 12 },
      view: {},
    } })
    expect(mounted.surface).toBe(surface)
  })
}

test('status.py JSON real keeps model, effort and every Codex login state', { options }, () => {
  const quota = parseStatusSnapshot({
    codex: {
      modelo: 'gpt-5.6-luna',
      effort: 'ultra',
      quotas: [{ janela: 300, pct: 3, reseta_em: 1787759175 }],
      contas: [
        { perfil: '.codex', email: null, plano: null, status: 'sem_login' },
        { perfil: '.codex-b', email: null, plano: null, status: 'erro' },
      ],
    },
    ts: 100,
  }, '.codex-b')
  expect(quota.model).toBe('gpt-5.6-luna')
  expect(quota.effort).toBe('ultra')
  expect(quota.windows[0]?.reseta_em).toBe(1787759175)
  expect(selectQuotaAccount(quota)?.perfil).toBe('.codex-b')
  expect(quota.contas.map(account => account.status)).toEqual(['sem_login', 'erro'])
})

test('saida relativa usa o cwd no arquivo e no argv', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming' })
  let request: { argv: readonly string[] } | undefined
  installDelegationRun(on)
  on('process.spawn', async function* (_$, e) {
    request = e
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'teste',
    cwd: 'C:/outro',
    saida: 'analise/codex-job4-cenario.log',
  })
  const delegation = JSON.parse(String(call.result)) as { saida: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  expect(delegation.saida).toBe('C:/outro/analise/codex-job4-cenario.log')
  expect(request?.argv).toContain(delegation.saida)
})

test('resposta substancial vence capacidade', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  installDelegationRun(on, true)
  on('process.spawn', async function* () {
    yield { stream: 'stderr', text: 'Selected model is at capacity' }
    return { value: { code: 1, signal: null } }
  })
  on('fs.exists', () => ({ value: true }))
  on('fs.read', () => ({ value: 'resposta completa '.repeat(30) }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({ tool: 'mcp__codex-delegar__delegar_codex', prompt: 'teste', cwd: 'C:/outro' })
  const job = JSON.parse(String(call.result)) as { job_id: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  const status = await $.tool.call({ tool: 'mcp__codex-delegar__codex_job', job_id: job.job_id })
  expect(status.result).toContain('"status":"ok"')
  expect(status.result).not.toContain('modelo sem capacidade')
})

test('arquivo de saída já existente e inalterado não produz sucesso', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  let existsCalls = 0
  installDelegationRun(on)
  on('process.spawn', async function* () {
    return { value: { code: 0, signal: null } }
  })
  // diretório, saída antes, leitura da resposta e saída depois existem; PERGUNTA.md não.
  on('fs.exists', () => ({ value: [1, 3, 4, 6].includes(++existsCalls) }))
  on('fs.read', () => ({ value: 'arquivo antigo '.repeat(30) }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'teste',
    cwd: 'C:/outro',
    saida: 'resultado.md',
  })
  const job = JSON.parse(String(call.result)) as { job_id: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  const status = await $.tool.call({ tool: 'mcp__codex-delegar__codex_job', job_id: job.job_id })
  expect(status.result).toContain('falhou')
})

test('dois jobs não podem compartilhar a mesma saída', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  installDelegationRun(on)
  on('process.spawn', async function* () {
    await new Promise(() => undefined)
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  await $.tool.call({ tool: 'mcp__codex-delegar__delegar_codex', prompt: 'um', cwd: 'C:/outro', saida: 'mesmo.md' })
  const second = await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'dois',
    cwd: 'C:/outro',
    saida: 'mesmo.md',
  })
  expect(second.result).toContain('saída já está em uso')
})

test('falha do pré-voo do hook de delegação volta como resultado controlado', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming' })
  let spawned = false
  on('process.run', () => processResult(1, '', 'Codex sem login'))
  on('process.spawn', async function* () {
    spawned = true
    return { value: { code: 0, signal: null } }
  })
  on('ui.toast', () => ({ value: undefined }))
  const result = await $.tool.call({ tool: 'mcp__codex-delegar__delegar_codex', prompt: 'teste', cwd: 'C:/outro' })
  expect(result.result).toContain('falhou')
  expect(result.result).toContain('Codex sem login')
  expect(spawned).toBe(false)
})

test('hooks observadores chamam next mesmo quando o estado falha', { options }, async ($, on) => {
  let promptPassed = false
  let toolPassed = false
  on('state.set', () => { throw new Error('estado indisponível') })
  on('prompt.submit', ($, e) => {
    promptPassed = true
    return { text: e.text }
  })
  on('tool.call', () => {
    toolPassed = true
    return { result: {}, text: '' }
  })
  await $.prompt.submit({ text: 'falha controlada', wait: false, origin: { kind: 'composer' } })
  await $.tool.call({ tool: 'Read', file_path: 'arquivo.md' })
  expect(promptPassed).toBe(true)
  expect(toolPassed).toBe(true)
})
