<#
.SYNOPSIS
  Cria um perfil adicional de Claude Code ou Codex nesta maquina.

.DESCRIPTION
  Cada perfil usa uma pasta de configuracao separada, mas compartilha os dados
  definidos na tabela de provedores por meio de junctions, hardlinks e copias.
  Assim, trocar de perfil preserva o historico e as regras compartilhadas.

  O nome dos arquivos, diretorios, variavel de ambiente e comando vem da tabela
  de provedores no inicio do script.

.EXAMPLE
  .\new-profile.ps1 -Provider claude -Nome b
  claude-b            # primeira vez: /login
  claude-b --continue # retoma a conversa da pasta atual

.EXAMPLE
  .\new-profile.ps1 -Provider codex -Nome b
  codex-b login

.NOTES
  NUNCA rode dois perfis do mesmo provedor ao mesmo tempo no mesmo projeto:
  os dados compartilhados podem ser escritos por dois processos e corromper.

  O primeiro turno do perfil novo pode reenviar a conversa inteira (caro).
  Troque em ponto natural, nao no meio de uma cadeia de edicoes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('claude', 'codex')]
    [string]$Provider,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$Nome,

    [string]$ShimDir = (Join-Path $env:APPDATA 'npm')
)

$Provedores = @{
    claude = @{
        EnvVar    = 'CLAUDE_CONFIG_DIR'
        Base      = '.claude'
        Cli       = 'claude'
        Junctions = @('projects', 'plugins', 'skills')
        Links     = @('CLAUDE.md')
        Copias    = @('settings.json')
    }
    codex = @{
        EnvVar    = 'CODEX_HOME'
        Base      = '.codex'
        Cli       = 'codex'
        Junctions = @('sessions', 'skills', 'plugins')
        Links     = @('config.toml')
        Copias    = @()
    }
}

$ErrorActionPreference = 'Stop'
$Config = $Provedores[$Provider]
$BaseNome = $Config.Base.TrimStart('.')
$Base = Join-Path $env:USERPROFILE $Config.Base
$Perfil = Join-Path $env:USERPROFILE ".$BaseNome-$Nome"
$ShimNome = "$($Config.Cli)-$Nome"

if (-not (Test-Path $Base)) {
    throw "Perfil principal nao encontrado em $Base. Rode o $($Config.Cli) uma vez antes."
}
if (-not (Test-Path $ShimDir)) {
    throw "Pasta de atalhos nao encontrada em $ShimDir. Passe -ShimDir com o diretorio que esta no seu PATH."
}

if (Test-Path $Perfil) {
    throw "$Perfil ja existe. Escolha outro nome ou apague a pasta."
}

New-Item -ItemType Directory -Path $Perfil | Out-Null

# No Codex, sessions/ e junction porque o status.py soma os rollouts de
# $CODEX_HOME/sessions/; perfis separados fragmentariam a medicao.
# Os sqlite state_*, logs_*, queue_* e os arquivos -wal/-shm nao entram em
# junction nenhuma: banco com journal nao deve ser compartilhado entre processos.
foreach ($compartilhado in $Config.Junctions) {
    $alvo = Join-Path $Base $compartilhado
    if (Test-Path $alvo) {
        New-Item -ItemType Junction -Path (Join-Path $Perfil $compartilhado) -Target $alvo | Out-Null
        Write-Host "  junction $compartilhado -> $alvo"
    }
}

# Arquivos entram como hardlink, nao junction: assim todos os perfis mantem a
# mesma regra sem exigir admin (mesmo volume); se falhar, copia e segue.
foreach ($link in $Config.Links) {
    $origem = Join-Path $Base $link
    if (Test-Path $origem) {
        $destino = Join-Path $Perfil $link
        try {
            New-Item -ItemType HardLink -Path $destino -Target $origem -ErrorAction Stop | Out-Null
            Write-Host "  $link hardlink (compartilhado)"
        } catch {
            Copy-Item $origem $destino
            Write-Host "  $link copiado (hardlink indisponivel: copia nao acompanha edicoes)"
        }
    }
}

foreach ($copia in $Config.Copias) {
    $origem = Join-Path $Base $copia
    if (Test-Path $origem) {
        # Copia, nao junction: cada perfil pode querer configuracao propria.
        Copy-Item $origem (Join-Path $Perfil $copia)
        Write-Host "  $copia copiado"
    }
}

# Dois atalhos, nao um .ps1. Um .ps1 morre em duas frentes: o Git Bash nao
# executa .ps1, e no PowerShell a ExecutionPolicy padrao pode recusar script
# nao assinado. O .cmd funciona em PowerShell, cmd e Git Bash; o shim sem
# extensao e para quem chama de dentro do MSYS.
$shimCmd = Join-Path $ShimDir "$ShimNome.cmd"
# Duas armadilhas do cmd.exe aqui, as duas silenciosas -- o atalho cai no perfil
# principal sem erro nenhum:
#  1) CRLF obrigatorio: um .cmd com quebras LF tem o SET ignorado. Como este
#     script e salvo com LF, um here-string herdaria LF.
#  2) CALL obrigatorio: sem ele, chamar outro .cmd (o shim npm do CLI) encadeia
#     em vez de chamar, o batch atual termina, o ENDLOCAL implicito do SETLOCAL
#     dispara e a variavel some ANTES do CLI subir.
$linhasCmd = @(
    "@ECHO off"
    "SETLOCAL"
    "SET ""$($Config.EnvVar)=%USERPROFILE%\.$BaseNome-$Nome"""
    "CALL $($Config.Cli) %*"
)
[System.IO.File]::WriteAllText($shimCmd, (($linhasCmd -join "`r`n") + "`r`n"), [System.Text.Encoding]::ASCII)

$shimSh = Join-Path $ShimDir $ShimNome
# LF e sem BOM: o sh do MSYS engasga com CRLF na linha do shebang.
$conteudoSh = "#!/bin/sh`nexport $($Config.EnvVar)=`"`$USERPROFILE\.$BaseNome-$Nome`"`nexec $($Config.Cli) `"`$@`"`n"
[System.IO.File]::WriteAllText($shimSh, $conteudoSh, (New-Object System.Text.UTF8Encoding $false))

Write-Host ""
Write-Host "Perfil '$Nome' criado em $Perfil"
Write-Host "Atalhos: $shimCmd  (PowerShell/cmd)"
Write-Host "         $shimSh  (Git Bash)"
Write-Host ""
Write-Host "Proximos passos:"
if ($Provider -eq 'claude') {
    Write-Host "  $ShimNome                                                   # /login com a outra conta"
    Write-Host "  $ShimNome mcp add --scope user codex -- codex mcp-server   # tem que ser SOB o atalho novo"
    Write-Host ""
    Write-Host "Conferir depois do login: /status mostra o e-mail do perfil novo, e uma"
    Write-Host "chamada trivial ao MCP codex retorna (MCP registrado nao e MCP funcionando)."
} else {
    Write-Host "  $ShimNome login"
}
