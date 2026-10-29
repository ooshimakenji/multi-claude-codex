# Fixture `tarefas.json`

1. `tarefas`: lista de tarefas; cada uma tem `nome`, `worktree` e `agentes`.
2. `agentes`: lista de registros de agentes.
3. `id`: string estável; `tipo`: `codex`, `claude`, `gemini` ou `processo`.
4. `modelo`, `effort`, `papel` e `conta`: strings; podem ser `null` quando não se aplicam.
5. `inicio` e `fim`: timestamps ISO 8601; `fim` pode ser `null` enquanto roda.
6. `segundos`, `tokens` e `cotaAprox`: números; podem ser `null` se desconhecidos.
7. `estado`: `rodando`, `concluido`, `falhou`, `pergunta` ou `travado`.
8. `progresso` e `resultado`: strings; `resultado` pode ser `null` durante a execução.
9. `veredito`: `voltou`, `relatorio_falso` ou `null`; pode incluir `achados` (lista de strings).
10. Um papel ou nome `Sem tarefa` representa agente sem tarefa atribuída.
11. `resumo.rodando`, `feitosHoje`, `voltaram` e `falharam` são inteiros.
12. `cotas`: lista de `{conta, janela, pct, resetaEm}`; `resetaEm` é timestamp ISO 8601.
13. Percentuais (`pct`, `cotaAprox`) são números de 0 a 100.
