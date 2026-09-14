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

A cota do Codex **não é infinita**: nessa mesma máquina ela já chegou a 100% num dia
de sessões paralelas pesadas. O ponto do padrão não é que um lado seja de graça, é
que os dois esgotam em ritmos diferentes e vale gastar o que está sobrando.

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

Nota honesta: para medição de uso geral, existe alternativa pronta: TokenTracker (MIT,
31 ferramentas). Este repo mede outra coisa: a razão de delegação entre Claude e Codex,
que é o que decide quando delegar.

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
codex luna 495.6k 5h0.0% 7d0.0% | ctx 215.5k
```

- `495.6k` — tokens gastos no Codex dentro da janela viva. **É o número que se mexe:**
  rode uma delegação e ele sobe na hora.
- `5h0.0% 7d0.0%` — as duas janelas de quota. **`used_percent` só vem em inteiro** —
  conferido em ~1.900 amostras: `0.0, 1.0, … 100.0`, nunca fracionário. Um job de 90k
  fica em `0.0%` e parece que nada rodou; por isso a contagem de tokens ao lado.
- `ctx` — contexto vivo da sessão do Claude.

O modo `--line` existe porque o modo completo não serve para isso, por dois
motivos que valem para qualquer script de statusline:

- **Ele roda a cada render do prompt.** O modo completo varre o transcript
  inteiro, que aqui passa de 30 MB. O `--line` lê só o rabo do arquivo e mostra o
  contexto vivo (tokens da última requisição), não o acumulado. ~0,19 s.
- **Ele não grava o marco.** Se gravasse, cada render viraria o novo ponto zero e
  o delta nunca sairia de ~0.

## Várias contas, qualquer provedor

Um processo = uma credencial. Subagentes herdam a autenticação de quem os criou, e
os CLIs não fazem rotação automática de conta por padrão.

Para rotação de conta do Claude, a recomendação é o
[claude-swap](https://github.com/realiti4/claude-swap): ferramenta em Python, MIT e
compatível com Windows. `cswap auto` monitora a cota e troca de conta sozinho ao chegar
em ~90%, com cooldown e histerese.

### Fazer a rotação sobreviver ao reboot (Windows)

`cswap auto` é um loop em primeiro plano, então morre quando o terminal fecha. No
Windows, o jeito de manter é uma Tarefa Agendada disparada no logon.

Duas armadilhas encontradas montando isso, as duas silenciosas:

- Aspas aninhadas no campo Argument da tarefa **não sobrevivem**: a tarefa registra
  sem erro e morre com exit 1 ao rodar. A saída é um wrapper `.cmd` de 4 linhas que a
  tarefa chama; o `.cmd` precisa de CRLF (veja **O `.cmd` precisa de CRLF**, na seção
  "Quatro armadilhas do Windows").
- Não use `*>>` do PowerShell para o log (veja **`*>>` e `Out-File` do PowerShell 5.1
  gravam UTF-16**, na mesma seção). O redirect fica dentro do `.cmd`.

Wrapper:

```bat
@ECHO off
"<CAMINHO-DO-CSWAP>\cswap.exe" auto --interval 60 >> "<PASTA-DE-LOG>\auto.log" 2>&1
```

Configure a tarefa assim:

| Campo | Valor | Por que |
|---|---|---|
| Gatilho | logon, delay PT1M | não briga com o boot |
| MultipleInstances | IgnoreNew | evita dois loops ao mesmo tempo |
| ExecutionTimeLimit | PT0S | sem limite: o loop é infinito |
| RestartCount | 3, a cada 2 min | o Windows religa se o processo morrer |
| LogonType | Interactive | precisa de rede e do perfil do usuário |
| Bateria | não parar nem bloquear | funciona em notebook desconectado |

A tarefa deve chamar o wrapper por `powershell.exe -NoProfile -WindowStyle Hidden -Command
"& '<wrapper>'"`, para o console não ficar visível no desktop enquanto o loop roda.

Depois do boot, confira:

```powershell
Get-ScheduledTask cswap-auto | Select State
Get-Content <PASTA-DE-LOG>\auto.log -Tail 3
```

`--once` existe e é "cron-friendly", mas usado a cada minuto abriria um console 1.440
vezes por dia. O loop sobe uma vez por logon, então é preferível no desktop.

### Importar uma conta sem logout

Não rode `/logout` antes: o próprio README do claude-swap alerta que o Claude Code
atual pode revogar o refresh token. Perfis por `CLAUDE_CONFIG_DIR` também são a fonte
segura para importar contas no claude-swap, sem tocar na credencial principal:

```bash
CLAUDE_CONFIG_DIR=~/.claude-b cswap add
```

O `cswap` lê a credencial do perfil indicado e a importa sem logout. Neste fluxo
testado, `~/.claude/.credentials.json` permaneceu byte a byte idêntico (mesmo SHA-256).

Ressalvas: há 85 issues abertas; o backup de credencial é feito em JSON texto puro por
padrão (criptografia opcional); e a ferramenta não cobre o Codex.

O lado Codex continua sendo separado por variável de ambiente (`CODEX_HOME`), que é o
único equivalente aqui. Se preferir fazer a rotação do Claude à mão,
`CLAUDE_CONFIG_DIR` faz o mesmo para o Claude.

Cada CLI guarda a credencial numa pasta de configuração e aceita uma variável de
ambiente que aponta para outra pasta.

| Provedor | Variável | Pasta padrão | O que fica separado |
|---|---|---|---|
| Claude Code | `CLAUDE_CONFIG_DIR` | `~/.claude` | `.credentials.json` |
| Codex CLI | `CODEX_HOME` | `~/.codex` | `auth.json` |
| Gemini CLI | `GEMINI_CLI_HOME` | `~/.gemini` | settings + chave |

Nota honesta: `CLAUDE_CONFIG_DIR` e `CODEX_HOME` estão verificados em uso nesta
máquina. `CODEX_HOME` foi provado assim: `codex login status` diz "Logged in using
ChatGPT", mas `CODEX_HOME=<pasta vazia> codex login status` diz "Not logged in".
`GEMINI_CLI_HOME` aparece no bundle do CLI, mas não foi testado — sonde antes de
confiar.

As notas abaixo são apenas técnicas, para quem decidir montar o wrapper manualmente;
`claude-swap` é o caminho recomendado para a rotação do Claude.

Cada perfil tem sua própria credencial, mas compartilha histórico, skills e plugins
por junction — trocar de conta não perde contexto nem a configuração de delegação.
No Codex, `sessions/` é compartilhado de propósito porque o `status.py` soma os
rollouts de `$CODEX_HOME/sessions`; perfis separados fragmentariam essa medição.

⚠️ **Não retome a mesma conversa em dois perfis ao mesmo tempo.** Abrir uma sessão
nova em outro perfil é seguro — cada sessão cria o próprio `<uuid>.jsonl`. O que
corrompe é `--continue` em dois perfis sobre a mesma conversa: pelas junctions os
dois retomam o mesmo arquivo. E o primeiro turno do perfil novo reenvia a conversa
inteira (caro), então troque em ponto natural.

`/login` dentro de uma sessão só afeta o perfil daquela sessão, porque a variável é
fixada quando o processo sobe. Para logar outro perfil, abra um terminal novo e use
o atalho dele. Quem erra isso acaba trocando a conta do perfil principal sem
perceber.

### Quatro armadilhas do Windows

Todas silenciosas: não há erro, a coisa parece funcionar, e o estrago aparece depois
— nos itens 1 a 3, o atalho usa a conta errada; no item 4, o arquivo de log fica
corrompido.

1. **Não use `.ps1`.** Falha duas vezes: o Git Bash não executa `.ps1`, e a
   ExecutionPolicy padrão do Windows recusa script não assinado. Use `.cmd`
   (serve para PowerShell, cmd e Git Bash) mais um shim `sh` sem extensão.
2. **O `.cmd` precisa de CRLF.** Com quebras LF o `cmd.exe` ignora a linha do
   `SET` — sem reclamar — e o CLI sobe no perfil principal.
3. **Use `CALL`.** `SETLOCAL` + `<cli> %*` encadeia para o shim npm do CLI em vez
   de chamá-lo: o batch atual termina, o `ENDLOCAL` implícito dispara e a variável
   some **antes** do CLI subir. `CALL <cli> %*` preserva.
4. **`*>>` e `Out-File` do PowerShell 5.1 gravam UTF-16.** Redirecionar a saída de
   um programa com `*>>` num arquivo que era ASCII produz arquivo de encoding MISTO,
   e nada reclama. Para log de processo longo, redirecione por `cmd` (`>> arquivo 2>&1`),
   que escreve bytes crus.

```bat
@ECHO off
SETLOCAL
SET "CLAUDE_CONFIG_DIR=%USERPROFILE%\.claude-b"
CALL claude %*
```

Como conferir que o atalho isola de verdade: rode o mesmo comando pelo atalho e sem
ele, com o outro perfil **já logado**. Respostas diferentes (contas, ou limites de
cota distintos) provam o isolamento. Com o perfil novo ainda sem credencial o teste
é inútil — "não logado" sai de qualquer jeito, mesmo com o atalho quebrado.

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

## Login do Codex

Às vezes o Codex parece "pedir login do nada": o token OAuth expira, e o refresh nem
sempre se propaga quando mais de um processo mexe no mesmo `auth.json`. É uma
fragilidade conhecida do próprio Codex CLI, não deste repo.

Agora o `status.py` expõe um campo `status` por perfil e o `--line` acrescenta
`[relogar]` à parte `codex ...` quando o perfil ativo não está saudável. O pré-voo
de delegação em `CLAUDE.md` e `AGENTS.md` checa `codex login status` além de
`codex --version`, evitando que um `codex exec` em background fique preso esperando
um login que nunca vai vir.

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
