# Painel — Claude + Codex

Aplicativo desktop nativo em Rust/egui que exibe delegações, cotas Claude/Codex e free tier. Ele atualiza a cada 2 segundos em uma thread separada, lendo `python <repo>/skills/codex/status.py --json` e `~/.local/bin/cswap.exe list --json`.

## Compilar

Com Rust instalado:

```text
cargo build --release
```

### Sprites dos companheiros

Antes de abrir o painel, popule o cache local com:

```text
python ..\skills\codex\sprites.py
```

O script consulta as contas do `cswap list --json` e grava `poke-cache/` em
`$CLAUDE_CONFIG_DIR` (ou `~/.claude`). O painel só lê esse cache; se ele faltar,
a seção CLAUDE continua normal, sem sprites. Os sprites vêm da PokeAPI em runtime
e não são redistribuídos neste repositório. Projeto pessoal, não comercial, sem
vínculo com Nintendo, Game Freak, Creatures ou The Pokémon Company.

O painel não mede tokens, tempo ou cotas. Essa é uma decisão de arquitetura: ele apenas lê as duas fontes externas, preservando uma única implementação da medição e permitindo que uma fonte falhe sem interromper as demais.

## Fonte

A interface usa `assets/Silkscreen-Regular.ttf`, da Silkscreen Project Authors. A fonte é embutida no binário e distribuída sob a SIL Open Font License, versão 1.1 (OFL). O texto integral da licença está em `assets/OFL.txt`.
