<#
.SYNOPSIS
  Cria um perfil adicional do Claude Code (credencial separada) nesta maquina.

.DESCRIPTION
  Um processo do Claude Code = uma credencial. Subagentes herdam a auth e nao existe
  switcher nativo. A separacao e por CLAUDE_CONFIG_DIR: cada perfil tem a sua propria
  .credentials.json, mas compartilha transcripts e plugins com o perfil principal
  atraves de junctions -- entao trocar de perfil nao perde historico.

  Cria:
    ~/.claude-<Nome>/                 pasta do perfil
    ~/.claude-<Nome>/projects  -->    junction para ~/.claude/projects   (historico compartilhado)
    ~/.claude-<Nome>/plugins   -->    junction para ~/.claude/plugins    (plugins compartilhados)
    ~/.claude-<Nome>/skills    -->    junction para ~/.claude/skills     (skills compartilhadas)
    ~/.claude-<Nome>/CLAUDE.md -->    hardlink para ~/.claude/CLAUDE.md  (mesma regra em todos)
    ~/.claude-<Nome>/settings.json    copia do principal
    <ShimDir>/claude-<Nome>.ps1       atalho que seta CLAUDE_CONFIG_DIR e chama claude

  Junction de diretorio no Windows nao exige admin.

.EXAMPLE
  .\new-profile.ps1 -Nome b
  claude-b            # primeira vez: /login
  claude-b --continue # retoma a conversa da pasta atual com a outra credencial

.NOTES
  NUNCA rode dois perfis ao mesmo tempo no mesmo projeto: pelas junctions os dois
  escrevem no mesmo <uuid>.jsonl e a conversa corrompe.

  O primeiro turno do perfil novo reenvia a conversa inteira (caro). Troque em ponto
  natural, nao no meio de uma cadeia de edicoes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$Nome,

    [string]$Base    = (Join-Path $env:USERPROFILE '.claude'),
    [string]$ShimDir = (Join-Path $env:APPDATA 'npm')
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Base)) {
    throw "Perfil principal nao encontrado em $Base. Rode o Claude Code uma vez antes."
}
if (-not (Test-Path $ShimDir)) {
    throw "Pasta de atalhos nao encontrada em $ShimDir. Passe -ShimDir com o diretorio que esta no seu PATH."
}

$Perfil = Join-Path $env:USERPROFILE ".claude-$Nome"
if (Test-Path $Perfil) {
    throw "$Perfil ja existe. Escolha outro nome ou apague a pasta."
}

New-Item -ItemType Directory -Path $Perfil | Out-Null

# skills entra aqui junto de projects/plugins: sem ela o perfil de fallback sobe
# sem a regra de delegacao, que e justamente o que se quer preservar ao trocar.
foreach ($compartilhado in 'projects', 'plugins', 'skills') {
    $alvo = Join-Path $Base $compartilhado
    if (Test-Path $alvo) {
        New-Item -ItemType Junction -Path (Join-Path $Perfil $compartilhado) -Target $alvo | Out-Null
        Write-Host "  junction $compartilhado -> $alvo"
    }
}

# CLAUDE.md e arquivo, nao pasta: junction nao serve. Hardlink mantem os perfis
# em sincronia sem exigir admin (mesmo volume); se falhar, copia e segue.
$claudeMd = Join-Path $Base 'CLAUDE.md'
if (Test-Path $claudeMd) {
    $destino = Join-Path $Perfil 'CLAUDE.md'
    try {
        New-Item -ItemType HardLink -Path $destino -Target $claudeMd -ErrorAction Stop | Out-Null
        Write-Host "  CLAUDE.md hardlink (compartilhado)"
    } catch {
        Copy-Item $claudeMd $destino
        Write-Host "  CLAUDE.md copiado (hardlink indisponivel: copia nao acompanha edicoes)"
    }
}

$settings = Join-Path $Base 'settings.json'
if (Test-Path $settings) {
    # Copia, nao junction: cada perfil pode querer modelo/effort proprios.
    Copy-Item $settings (Join-Path $Perfil 'settings.json')
    Write-Host "  settings.json copiado"
}

$shim = Join-Path $ShimDir "claude-$Nome.ps1"
@"
`$env:CLAUDE_CONFIG_DIR = "`$env:USERPROFILE\.claude-$Nome"
claude @args
"@ | Set-Content -Path $shim -Encoding utf8

Write-Host ""
Write-Host "Perfil '$Nome' criado em $Perfil"
Write-Host "Atalho: $shim"
Write-Host ""
Write-Host "Proximos passos:"
Write-Host "  claude-$Nome                                                   # /login com a outra conta"
Write-Host "  claude-$Nome mcp add --scope user codex -- codex mcp-server   # tem que ser SOB o atalho novo"
Write-Host ""
Write-Host "Conferir depois do login: /status mostra o e-mail do perfil novo, e uma"
Write-Host "chamada trivial ao MCP codex retorna (MCP registrado nao e MCP funcionando)."
