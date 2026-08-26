# multi-claude-codex

Fazer o **Claude Code** parar de gastar o próprio contexto em leitura pesada e
passar esse trabalho para o **Codex CLI** — que roda em outra assinatura, com
outra cota.

Sem plugin, sem dependência nova, sem servidor. São quatro arquivos de texto e um
script de medição, todos apoiados no que os dois CLIs já fazem nativo.

## Por que

Os dois lados custam de formas diferentes, e normalmente um está no talo enquanto
o outro está parado:

| | |
|---|---|
| **Claude Code** | janela de 5h que estoura no meio do dia; excedente cobrado se o plano permitir |
| **Codex CLI** | assinatura separada, janela semanal, quase sempre ociosa |

Na máquina onde isto foi montado, no mesmo período: Claude bateu **100% da janela
de 5h**, o Codex estava em **1% da janela semanal**. Toda leitura pesada que sai de
um e entra no outro é ganho direto — e o `status.py` mede isso em vez de estimar.

## O padrão

**Claude planeja, orquestra e revisa. Codex lê e executa.**

O ponto não é que o Codex seja melhor: é que ler 40 arquivos para responder uma
pergunta é justamente o tipo de trabalho que não precisa acontecer dentro do
contexto de quem está conduzindo. O condutor recebe o resumo, não os 40 arquivos.

Na prática, três regras:

1. **Antes de ler >2 arquivos ou um diff grande, delegar.** A regra tem que morar
   no `CLAUDE.md` (sempre em contexto), não só numa skill — quando o modelo
   *decide* invocar uma skill, ele já leu os arquivos.
2. **Declarar `modelo / effort / sandbox` antes de rodar.** E conferir depois:
   todo rollout do Codex grava `turn_context.model`. Auditoria, não promessa.
3. **Delegação pesada vai em background.** O `codex exec` não deve travar o turno.

## Instalar

Requisitos: [Claude Code](https://claude.com/claude-code) e
[Codex CLI](https://github.com/openai/codex) instalados e autenticados
(`codex login status` deve dizer "Logged in using ChatGPT").

```bash
git clone https://github.com/<voce>/multi-claude-codex
cd multi-claude-codex

# a regra: sempre em contexto
cat CLAUDE.md >> ~/.claude/CLAUDE.md

# os comandos: carregados sob demanda
mkdir -p ~/.claude/skills/codex
cp skills/codex/SKILL.md skills/codex/status.py ~/.claude/skills/codex/
```

Opcional, para pergunta curta e síncrona sem sair do Claude:

```bash
claude mcp add --scope user codex -- codex mcp-server
```

O MCP bloqueia o turno até o Codex terminar — por isso o trabalho pesado vai por
`codex exec` em background, não por ele.

## Modelos

Catálogo vivo em `$CODEX_HOME/models_cache.json` (`~/.codex` por padrão). Se a
lista parecer curta, é cache velho: `codex update`.

| Apelido | Modelo | Quando |
|---|---|---|
| `rapido` | `gpt-5.6-luna` | mecânico, repetitivo, leitura de volume |
| `normal` | `gpt-5.6-terra` | default geral |
| `pesado` | `gpt-5.6-sol` | difícil ou ambíguo; `model_reasoning_effort` até `ultra` |

```bash
# review do diff atual
# `codex review` NÃO aceita -m. A chave é `review_model`, não `model`.
codex review --uncommitted -c review_model="gpt-5.6-terra"

# leitura pesada, em background, gravando a resposta em arquivo
codex exec -m gpt-5.6-luna -s read-only -o codex-out.md "<prompt>"
```

## Medir

```bash
python ~/.claude/skills/codex/status.py
```

```
CODEX   modelo=gpt-5.6-luna effort=high sandbox=read-only
        tokens=90.000  quota=3.0% (janela 7d)
CLAUDE  tokens=41.203 (sessao inteira)

DELTA desde a ultima rodada:
        Codex          90.000  (+2.0% de quota)
        Claude            100
        -> 900.0x token(s) no Codex por token no Claude
```

Rode **antes e depois** de delegar. O total do transcript é o custo da sessão
inteira; só o delta responde "quanto essa delegação economizou". Tudo sai de
arquivo local (`$CODEX_HOME/sessions/`, `$CLAUDE_CONFIG_DIR/projects/`) — nenhuma
chamada de rede, nenhuma estimativa.

`python status.py --selftest` roda os asserts.

### Na statusline

Para ver os números sem digitar nada, o Claude Code tem statusline nativa — uma
linha no rodapé de todo prompt, alimentada por um comando à sua escolha. Em
`~/.claude/settings.json`:

```json
"statusLine": {
  "type": "command",
  "command": "python \"/caminho/para/status.py\" --line",
  "padding": 0
}
```

```
codex luna 5h0.0% 7d0.0% | ctx 211.8k
```

O modo `--line` existe porque o modo completo não serve para isso, por dois
motivos que valem para qualquer script de statusline:

- **Ele roda a cada render do prompt.** O modo completo varre o transcript
  inteiro, que aqui passa de 30 MB. O `--line` lê só o rabo do arquivo e mostra o
  contexto vivo (tokens da última requisição), não o acumulado. ~0,19 s.
- **Ele não grava o marco.** Se gravasse, cada render viraria o novo ponto zero e
  o delta nunca sairia de ~0.

## Vários perfis do Claude

Um processo do Claude Code = uma credencial. Subagentes herdam a auth e não existe
switcher nativo. A separação é por `CLAUDE_CONFIG_DIR`:

```powershell
.\profiles\new-profile.ps1 -Nome b
claude-b              # /login com a outra conta
claude-b --continue   # retoma a conversa da pasta atual
```

Cada perfil tem `.credentials.json` própria, mas compartilha `projects` e `plugins`
por junction — trocar de perfil não perde histórico.

⚠️ **Nunca rode dois perfis ao mesmo tempo no mesmo projeto**: pelas junctions os
dois escrevem no mesmo `<uuid>.jsonl` e a conversa corrompe. E o primeiro turno do
perfil novo reenvia a conversa inteira (caro) — troque em ponto natural.

Isto é para quem tem contas legitimamente separadas (trabalho e pessoal, seats
distintos) e quer alternar sem reconfigurar a máquina. Não é para multiplicar
conta em cima de uma assinatura só.

## Outros provedores

O Codex aceita qualquer endpoint compatível com OpenAI via `model_providers` —
então **não se escreve uma ponte nova por provedor**. Um runtime só, N backends.
Exemplos prontos (NVIDIA NIM, Gemini, Ollama) em
[`providers.example.toml`](providers.example.toml), com as ressalvas: você troca
OAuth por API key no disco, e `wire_api = "chat"` não é o caminho feliz do loop de
agente do Codex — trate como one-shot de leitura, não como agente que edita.

## O que este repo deliberadamente não é

Não é um wrapper do Codex, e não instala o plugin oficial
([`openai/codex-plugin-cc`](https://github.com/openai/codex-plugin-cc)). O plugin
é bom e é da OpenAI; o diferencial dele sobre o CLI puro é gestão de jobs em
background — que, no Windows, é justamente a parte com as issues abertas
(`transfer` falhando, Stop hook pendurando até o timeout, `taskkill` não matando o
job). `codex review` nativo mais o background do próprio harness cobrem o caso com
menos peça móvel. Se você está em Linux ou macOS e quer os comandos prontos,
o plugin é uma escolha razoável.

## Licença

MIT.
