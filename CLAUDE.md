## Delegação (Claude → Codex)

### Quando o Codex CLI está saudável

- Antes de **LER** >2 arquivos ou um diff grande, delegar ao Codex em vez de ler eu mesmo.
- Antes de **ESCREVER** código novo ou bloco grande de texto, delegar ao Codex (`luna`).
  Eu faço o briefing e reviso o resultado — não escrevo.
- Divisão: **Codex lê e escreve. Claude planeja, orquestra, revisa e decide.**
- **"Já tenho o contexto" NÃO é razão para não delegar**: o briefing custa menos que a
  escrita, e o contexto que eu poupo é pago de novo em todo turno seguinte.
- Se o review reprovar, o defeito **volta ao Codex** (`luna`, o mesmo que escreveu) — não corrijo na mão. Escalar de modelo é exceção, não o padrão.
- Toda delegação declara `modelo / effort / sandbox` **antes** de rodar, com
  `run_in_background` e `< /dev/null` (o `codex exec` lê stdin mesmo com prompt no
  argumento e pendura para sempre sem isso).

### Quando o Codex CLI está indisponível

- Faça o pré-voo `codex --version` antes da primeira delegação do turno.
- Em seguida, faça `codex login status`: é barato, só lê o estado cacheado e não mexe em `auth.json`; exit 0 = logado, exit != 0 = não logado. As duas checagens precisam passar antes de disparar qualquer `codex exec` em background.
- Se qualquer uma falhar, ou se `python skills/codex/status.py --line` mostrar `[relogar]` para o perfil ativo, **não** tente delegar, não use MCP do Codex e não contorne sandbox ou credenciais.
- O fallback é: **Hermes executa e verifica; Claude planeja e revisa.** Registre o bloqueio de
  delegação na resposta, mas siga a tarefa dentro das permissões normais do projeto. Se a causa for a sessão,
  registre `Codex sem login` ou `sessão do Codex expirada`, em vez de `Codex indisponível`.
- Assim que os dois pré-voos voltarem a passar e o perfil ativo não mostrar `[relogar]`, aplique novamente as regras de delegação acima.

### Como saber se a delegação em background deu certo

Um job `codex exec ... -o arquivo.md &` só conta como sucesso se o arquivo de saída existir
e tiver conteúdo substancial, não os ~100–200 bytes de uma recusa ou erro. **Nunca confie
só no exit code do processo**: já houve exit 0 com zero trabalho feito (briefing vazio, sandbox
bloqueado, diretório sem git). Se o arquivo não aparecer ou for suspeitosamente pequeno, trate
como delegação bloqueada, siga o mesmo fallback acima e não tente de novo automaticamente sem
investigar a causa.

Comandos e tabela de modelos: skill `codex`.
