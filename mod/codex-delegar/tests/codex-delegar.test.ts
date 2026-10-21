import { expect, mock, test } from 'claude-code/testing'
import {
  fnv1a64,
  formatQuotaWindows,
  cacheIsFresh,
  jobsPollingInterval,
  mergeCache,
  mergeSessionJobs,
  mergePokeConfig,
  selectQuotaAccount,
  spriteNumber,
  trainerSpriteKey,
  usageColor,
} from '../hooks/register'

const options = { repoPath: 'C:/repo' }

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
  on('process.run', () => ({ value: {
    exitCode: 128,
    stdout: '',
    stderr: 'not a git repository',
    isStdoutTruncated: false,
    isStderrTruncated: false,
  } }))
  on('process.spawn', async function* (_$, e) {
    request = e
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
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

test('saida relativa usa o cwd no arquivo e no argv', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming' })
  let request: { argv: readonly string[] } | undefined
  on('process.run', () => ({ value: {
    exitCode: 128,
    stdout: '',
    stderr: 'not a git repository',
    isStdoutTruncated: false,
    isStderrTruncated: false,
  } }))
  on('process.spawn', async function* (_$, e) {
    request = e
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
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
  const response = 'resposta completa '.repeat(30)
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
  on('fs.exists', () => ({ value: true }))
  on('fs.read', () => ({ value: response }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  const call = await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'teste',
    cwd: 'C:/outro',
  })
  const job = JSON.parse(String(call.result)) as { job_id: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  const status = await $.tool.call({ tool: 'mcp__codex-delegar__codex_job', job_id: job.job_id })
  expect(status.result).toContain('"status":"ok"')
  expect(status.result).not.toContain('modelo sem capacidade')
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
  on('ui.open', () => ({ value: { isPlaced: true } }))
  const call = await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'teste',
    cwd: 'C:/outro',
  })
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
  await $.prompt.submit({ text: 'novo turno' })
  for (const tool of ['Read', 'Grep', 'Edit', 'Read']) {
    await $.tool.call({ tool, file_path: `${tool}.md` })
  }
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
  on('ui.open', () => ({ value: { isPlaced: true } }))
  const call = await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'teste',
    cwd: 'C:/outro',
    modelo: 'gpt-5.6-terra',
  })
  const job = JSON.parse(String(call.result)) as { job_id: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  const status = await $.tool.call({ tool: 'mcp__codex-delegar__codex_job', job_id: job.job_id })
  expect(status.result).toContain('gpt-5.6-luna')
})

test('job criado aparece no running antes de terminar', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming' })
  let release: (() => void) | undefined
  on('process.run', () => ({ value: {
    exitCode: 128,
    stdout: '',
    stderr: 'not a git repository',
    isStdoutTruncated: false,
    isStderrTruncated: false,
  } }))
  on('process.spawn', async function* () {
    await new Promise<void>(resolve => { release = resolve })
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'teste',
    cwd: 'C:/outro',
  })
  const tree = await $.ui.render({
    plugin: 'codex-delegar',
    surface: 'terminal',
    component: 'Pane',
    requestId: 'codex',
    props: {
      title: 'Codex',
      isFocused: false,
      bodyColumns: 80,
      placement: 'inline',
      scroll: { offset: 0, bodyRows: 12 },
      view: {},
    },
  })
  expect(JSON.stringify(tree)).toContain('outro')
  release?.()
  await new Promise(resolve => setTimeout(resolve, 25))
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
  on('fs.exists', () => ({ value: ++existsCalls === 1 || existsCalls === 3 }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
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
  on('tool.register', () => ({ value: undefined }))
  on('command.register', () => ({ value: undefined }))
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
  on('fs.exists', () => ({ value: ++existsCalls === 3 }))
  on('fs.read', () => ({ value: 'need clarification' }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
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
