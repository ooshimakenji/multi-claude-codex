---
name: codex
description: Delegar trabalho pesado de leitura, review e execução mecânica ao Codex CLI em vez de gastar contexto do Claude. Use ao revisar diffs, varrer repositório, resumir arquivo grande, auditar módulo, investigar bug, ou aplicar mudança repetitiva. Também ao escolher qual modelo do Codex usar, ou ao medir quanto cada lado da ponte custou.
---

# Delegar ao Codex

O Claude é o recurso escasso: janela de 5h que estoura e excedente cobrado.
O Codex roda em assinatura separada, normalmente ociosa. Toda leitura pesada que
sai do Claude e entra no Codex é ganho direto.

**Divisão de papéis:** Claude planeja, orquestra e revisa a saída. Codex lê e executa.

## Escolher o modelo

Catálogo vivo em `$CODEX_HOME/models_cache.json` (`~/.codex` por padrão).

| Apelido | Modelo | Quando |
|---|---|---|
| `trivial` | free tier via `ask.py` — cadeia ordenada por latência medida (ver abaixo) | texto curto e mecânico: mensagem de commit, resumo de diff, rename |

**Cadeia do `ask.py --provedor nvidia`** (medida 2026-09-01 com `auditoria_fotos/bench_codigo.py`,
que **executa** o código gerado contra 4 asserts): `gpt-oss-120b` 3,8s → `minimax-m3` 6,6s →
`kimi-k3` 18,8s → `nemotron-3.5-lightning` 66–104s → `nemotron-3-nano-omni` →
**`gemini:gemini-flash-lite-latest`** 2,2s.

Um item pode ser `provedor:modelo`. O tail é de **outro fornecedor de propósito**: a cadeia
só avança em 404/410, e o 404 da NVIDIA é `"Not found for account"` — mata todos os modelos
NVIDIA juntos, então mais um deles no fim da fila não seria fallback nenhum.
⚠️ O tail divide cota com o pipeline de fotos (mesmo modelo, mesma chave), por isso fica por último.
| `rapido` | `gpt-5.6-luna` | mecânico, repetitivo, leitura de volume (resumir arquivo grande, varrer repo) |
| `normal` | `gpt-5.6-terra` | default geral |
| `pesado` | `gpt-5.6-sol` | difícil ou ambíguo; aceita `model_reasoning_effort` até `ultra` |

`git push` e `git pull` não precisam de modelo nenhum — são Bash direto. A camada
`trivial` serve para tarefa de texto, não para operação de shell.

Se a lista parecer curta ou velha, é catálogo em cache: `codex update`.

**Regra do modelo declarado:** diga `modelo / effort / sandbox` na mensagem
**antes** de rodar. Não é promessa — `status.py` confere depois no rollout.
Vale igual para `mcp__codex__codex`: passar `model` e `sandbox` explícitos em toda
chamada, porque não existe flag global que troque o default do MCP.

## Comandos

```bash
# review do diff atual (read-only, nativo do CLI)
# `codex review` NÃO aceita -m. A chave é `review_model`, não `model`.
codex review --uncommitted -c review_model="gpt-5.6-terra"
codex review --base main   -c review_model="gpt-5.6-terra"

# leitura pesada / investigação, em background
codex exec -m gpt-5.6-luna -s read-only --skip-git-repo-check \
  -o codex-out.md "<prompt>"
```

Antes de disparar, `codex login status` deve sair com exit 0. Depois que o job terminar,
sucesso é o arquivo passado em `-o` existir e ter conteúdo substancial; não confie no exit
code do `codex exec`, pois já houve exit 0 com zero trabalho feito.

`codex exec` sempre com `run_in_background`: o harness notifica no fim e cancela
por `TaskStop`. É isso que substitui um gestor de jobs.

## Escrita em disco

Bloco separado de propósito — não copie por engano. Em algumas máquinas Windows
o `--sandbox workspace-write` do Codex falha com "somente leitura" (sandbox de
token restrito do próprio Codex, não da harness). Aí a única via é sem sandbox:

```bash
codex exec -m gpt-5.6-terra --dangerously-bypass-approvals-and-sandbox "<prompt>"
```

Isso roda comando sem confinamento nenhum. Só com o dono da máquina ciente, e o
Claude revisa o diff depois.

## Medir

```bash
python skills/codex/status.py    # estado dos dois lados + delta desde a última rodada
```

`status.py --line` é a versão leve e read-only, para a statusline do Claude Code
(não grava o marco, então não zera o delta).

Quando a sessão do perfil ativo estiver expirada ou ausente, `status.py --line`
acrescenta `[relogar]` à parte `codex ...` da linha: rode `codex login` de novo antes
de insistir numa delegação.

Na statusline, acompanhe **os tokens**, não o `%`: `used_percent` só vem em inteiro,
então uma delegação normal fica em `0.0%` mesmo tendo rodado.

Rode **antes e depois** de delegar. O total do transcript é o custo da sessão
inteira; só o delta responde "quanto essa delegação economizou".

## Outros provedores

O Codex aceita qualquer endpoint compatível com OpenAI via `model_providers` —
ver `providers.example.toml`. Trate como one-shot de leitura/resumo, não como
agente que edita: o loop do Codex foi feito para o tool-calling da OpenAI.
