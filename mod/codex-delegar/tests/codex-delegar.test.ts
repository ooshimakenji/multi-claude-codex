import { expect, mock, test } from 'claude-code/testing'

declare function setTimeout(callback: (...args: any[]) => void, ms: number): unknown
import fixture from './tarefas.fixture'
import {
  fnv1a64,
  formatQuotaWindows,
  formatReset,
  cacheIsFresh,
  jobMarker,
  jobsPollingInterval,
  mergeCache,
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
  expect(request?.input).toBe(`<!-- job:${delegation.job_id} -->\nlinha 1\nlinha 2`)
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

test('marcador do job leva tarefa e papel numa linha só', { options }, () => {
  expect(jobMarker('1-0', 'Presença · turma', 'escrita')).toBe('<!-- job:1-0 tarefa:Presença · turma papel:escrita -->')
  expect(jobMarker('1-0', undefined, 'revisao')).toBe('<!-- job:1-0 papel:revisao -->')
  expect(jobMarker('1-0', '  ', 'leitura')).toBe('<!-- job:1-0 papel:leitura -->')
  expect(jobMarker('1-0')).toBe('<!-- job:1-0 -->')
})

test('marcador sanitiza quebra de linha, comentário e papel inválido', { options }, () => {
  const marker = jobMarker('1-0', 'linha 1\r\n\tlinha 2 --> <!-- x papel:falso', 'outro')
  expect(marker).toBe('<!-- job:1-0 tarefa:linha 1 linha 2 x papel=falso -->')
  expect(marker.includes('\n')).toBe(false)
  expect(marker.match(/-->/g)?.length).toBe(1)
  expect(jobMarker('1-0', 'x'.repeat(300)).length).toBeLessThan(160)
})

test('delegar_codex prefixa o prompt com tarefa e papel', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  let request: { input?: string } | undefined
  installDelegationRun(on)
  on('process.spawn', async function* (_$, e) {
    request = e
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({
    tool: 'mcp__codex-delegar__delegar_codex',
    prompt: 'corpo',
    cwd: 'C:/outro',
    tarefa: 'Centro de comando',
    papel: 'escrita',
  })
  const delegation = JSON.parse(String(call.result)) as { job_id: string }
  await new Promise(resolve => setTimeout(resolve, 25))
  expect(request?.input).toBe(`<!-- job:${delegation.job_id} tarefa:Centro de comando papel:escrita -->\ncorpo`)
})

test('pane agrupa a fixture tarefas.json por tarefa', { options }, async ($, on) => {
  mock.env(on, { USERPROFILE: 'C:/Users/test' })
  const lidos: string[] = []
  on('fs.read', ($, e) => {
    lidos.push(e.path.replace(/\\/g, '/'))
    return { value: JSON.stringify({ ...fixture, ts: Date.now() / 1000 }) }
  })
  on('process.run', () => {
    throw new Error('cache fresco não deve chamar o status.py')
  })
  on('ui.status', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  const render = async () => JSON.stringify(await $.ui.render({
    surface: 'terminal',
    component: 'Pane',
    requestId: 'codex',
    props: {
      title: 'Codex',
      isFocused: false,
      bodyColumns: 200,
      placement: 'inline',
      scroll: { offset: 0, bodyRows: 40 },
      view: {},
    },
  }))
  // /codex lê o cache do status.py em background; o render só desenha o estado.
  await $.command.run({
    command: 'codex',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 200 },
  })
  await new Promise(resolve => setTimeout(resolve, 25))
  const tree = await render()
  expect(fixture.tarefas).toHaveLength(3)
  expect(lidos).toContain('C:/Users/test/.claude/codex-status-jobs.json')
  expect(tree).toContain('CENTRO DE COMANDO')
  expect(tree).not.toContain('HERMES')
  for (const tarefa of fixture.tarefas) expect(tree).toContain(tarefa.nome)
  expect(tree).toContain('Sem tarefa')
  const agentes = fixture.tarefas.flatMap(tarefa => tarefa.agentes)
  expect(agentes).toHaveLength(8)
  for (const agente of agentes) expect(tree).toContain(`${agente.tipo} · `)
  for (const agente of agentes) expect(tree).toContain(agente.papel)
  expect(tree).toContain('codex · 5.6-luna/medium · ler dados de presença · rodando · 14:02')
  expect(tree).toContain('Regra validada para 3 turmas')
  expect(tree).toContain('rodando 2 · feitos hoje 3 · voltaram 1 · falharam 1')
  expect(tree).toContain(`.codex 5h 3% (reseta ${formatReset('2026-10-29T13:00:00-03:00', Date.now())})`)
})
