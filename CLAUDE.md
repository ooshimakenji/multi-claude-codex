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
- Se o comando falhar, **não** tente delegar, não use MCP do Codex e não contorne sandbox ou credenciais.
- O fallback é: **Hermes executa e verifica; Claude planeja e revisa.** Registre o bloqueio de
  delegação na resposta, mas siga a tarefa dentro das permissões normais do projeto.
- Assim que o pré-voo voltar a passar, aplique novamente as regras de delegação acima.

Comandos e tabela de modelos: skill `codex`.
