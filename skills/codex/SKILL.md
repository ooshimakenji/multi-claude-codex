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
| `rapido` | `gpt-5.6-luna` | mecânico, repetitivo, leitura de volume (resumir arquivo grande, varrer repo) |
| `normal` | `gpt-5.6-terra` | default geral |
| `pesado` | `gpt-5.6-sol` | difícil ou ambíguo; aceita `model_reasoning_effort` até `ultra` |

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
  -o "$SCRATCH/codex-out.md" "<prompt>"
```

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

Rode **antes e depois** de delegar. O total do transcript é o custo da sessão
inteira; só o delta responde "quanto essa delegação economizou".

## Outros provedores

O Codex aceita qualquer endpoint compatível com OpenAI via `model_providers` —
ver `providers.example.toml`. Trate como one-shot de leitura/resumo, não como
agente que edita: o loop do Codex foi feito para o tool-calling da OpenAI.
