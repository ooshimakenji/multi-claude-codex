# Operação com Hermes

## Projeto Hermes

O projeto ativo do Hermes é **Multi Claude Codex**, apontando para a raiz deste repositório. Abra o Hermes nesta pasta para que `AGENTS.md` seja carregado automaticamente:

```text
C:\Users\vinicius.oshima\Downloads\github\multi-claude-codex
```

## Papéis

| Componente | Responsabilidade |
|---|---|
| Hermes | Orquestrar, editar arquivos, executar testes e verificar o resultado. |
| Claude Code | Planejamento e revisão independente, quando solicitado. |
| Codex CLI | Leitura volumosa, investigação, execução repetitiva e revisão, quando saudável. |

## Pré-voo

```bash
claude --version
codex --version
codex login status
python skills/codex/status.py --selftest
```

O Codex CLI desta máquina está indisponível enquanto `codex --version` reportar a dependência Windows ausente, `codex login status` falhar ou `python skills/codex/status.py --line` mostrar `[relogar]` para o perfil ativo. Não configure MCP nem tente delegar até os pré-voos passarem; para falha de sessão, registre `Codex sem login` ou `sessão do Codex expirada`.

## Delegação segura ao Codex

Quando o pré-voo estiver saudável, use primeiro trabalho de leitura:

```bash
codex exec -m gpt-5.6-luna -s read-only --skip-git-repo-check -o codex-out.md "<tarefa>" < /dev/null
```

Antes do comando, informe no pedido: `modelo`, `effort` e `sandbox`. Revise o resultado e o diff antes de aceitar qualquer alteração.

## Verificação e métricas

```bash
python skills/codex/status.py --selftest
python skills/codex/status.py --json
```

`status.py` lê dados locais de Claude/Codex. Não inclua seus arquivos de autenticação em commits, logs compartilhados ou prompts.

## Painel

Use `abrir-painel.cmd` na raiz do repositório. Ele abre o binário já compilado a partir de `painel/`, permitindo que o aplicativo localize automaticamente `skills/codex/status.py`.

Para recompilar o painel no futuro, instale Rust/Cargo e execute:

```bash
cd painel
cargo test --release
cargo build --release
```
