import { expect, mock, test } from 'claude-code/testing'
import { formatQuotaWindows, selectQuotaAccount } from '../hooks/register'

const options = { repoPath: 'C:/repo' }

test('delegar_codex monta argv, stdin e skip-git-repo-check fora de git', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  let request: { argv: readonly string[]; input?: string } | undefined
  on('process.run', () => ({ value: { exitCode: 128, stdout: '', stderr: 'not a git repository', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('process.spawn', async function* (_$, e) {
    request = e
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: false }))
  on('ui.toast', () => ({ value: undefined }))
  const call = await $.tool.call({ tool: 'mcp__codex-delegar__delegar_codex', prompt: 'linha 1\nlinha 2', cwd: 'C:/fora' })
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
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
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
  await $.prompt.submit({ text: 'novo turno' })
  for (const tool of ['Read', 'Grep', 'Edit', 'Read']) {
    await $.tool.call({ tool, file_path: `${tool}.md` })
  }
  expect(toasts).toEqual(['3+ arquivos neste turno: considere delegar_codex'])
})

test('capacity in process diagnostics marks the job to retry', { options }, async ($, on) => {
  mock.env(on, { APPDATA: 'C:/Users/test/AppData/Roaming', TEMP: 'C:/Temp' })
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
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
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('process.spawn', async function* () {
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: ++existsCalls === 1 || existsCalls === 3 }))
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
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('process.spawn', async function* () {
    yield { stream: 'stdout', text: 'started' }
    return { value: { code: 0, signal: null } }
  })
  on('fs.exists', () => ({ value: ++existsCalls === 3 }))
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
