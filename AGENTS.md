# Fluxo Hermes + Claude Code + Codex

Este repositório implementa uma colaboração entre CLIs. Preserve o papel de cada um:

- **Hermes** é o orquestrador local: coleta contexto, aplica alterações, executa testes e confirma o resultado.
- **Claude Code** é opcional para planejamento e revisão independente.
- **Codex CLI** é opcional para leitura pesada, investigação, tarefas repetitivas e revisão de diffs.

## Regras operacionais

1. Comece por `README.md` e consulte `HERMES.md` para os comandos deste projeto.
2. Não presuma que o Codex CLI está disponível. Antes de delegar, execute `codex --version`. Se falhar, siga com Hermes e registre o bloqueio, sem tentar contornar o sandbox nem acessar credenciais.
3. Para uma delegação ao Codex, declare antes: modelo, effort e sandbox. Prefira leitura com `-s read-only`; somente o dono da máquina pode autorizar execução sem sandbox.
4. Não altere arquivos de credenciais, `.env`, `auth.json`, nem os diretórios de configuração de Claude/Codex.
5. Depois de qualquer alteração de código, execute a verificação pertinente. Para a medição, use `python skills/codex/status.py --selftest`.
6. O painel deve ser aberto por `abrir-painel.cmd`, que fixa o diretório de trabalho correto para localizar `skills/codex/status.py`.

As regras específicas do Claude Code permanecem em `CLAUDE.md`.
