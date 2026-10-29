// Cópia literal de fixtures/tarefas.json, a fixture única do contrato. O runner do mod não lê arquivo fora da
// pasta do plugin nem importa JSON. Ao mudar a fixture, regere este arquivo com o mesmo conteúdo.
import type { Tarefa, TarefaCota, TarefasResumo } from '../types'

const fixture: { tarefas: Tarefa[]; resumo: TarefasResumo; cotas: TarefaCota[] } = {
  "tarefas": [
    {
      "nome": "Presença · turma com minutos",
      "worktree": "C:/repos/centro-agentes",
      "agentes": [
        {
          "id": "codex-presenca-01",
          "tipo": "codex",
          "modelo": "gpt-5.6-luna",
          "effort": "medium",
          "papel": "ler dados de presença",
          "inicio": "2026-10-29T09:10:00-03:00",
          "fim": null,
          "segundos": 842,
          "estado": "rodando",
          "progresso": "Conferindo os minutos por aluno",
          "resultado": null,
          "veredito": null,
          "tokens": 18420,
          "cotaAprox": 1.8,
          "conta": ".codex"
        },
        {
          "id": "claude-presenca-02",
          "tipo": "claude",
          "modelo": "claude-sonnet-4-5",
          "effort": "high",
          "papel": "revisar regra de arredondamento",
          "inicio": "2026-10-29T08:42:00-03:00",
          "fim": "2026-10-29T09:02:18-03:00",
          "segundos": 1218,
          "estado": "concluido",
          "progresso": "Revisão encerrada",
          "resultado": "Regra validada para 3 turmas",
          "veredito": {
            "tipo": "voltou",
            "achados": ["arredondamento usa minutos inteiros", "não há perda na virada do mês"]
          },
          "tokens": 12600,
          "cotaAprox": 2.4,
          "conta": "claude-trabalho"
        },
        {
          "id": "processo-export-03",
          "tipo": "processo",
          "modelo": null,
          "effort": null,
          "papel": "exportar planilha",
          "inicio": "2026-10-29T08:55:00-03:00",
          "fim": "2026-10-29T08:55:07-03:00",
          "segundos": 7,
          "estado": "falhou",
          "progresso": "Processo encerrou com código 1",
          "resultado": "arquivo de origem não encontrado",
          "veredito": null,
          "tokens": null,
          "cotaAprox": null,
          "conta": "local"
        }
      ]
    },
    {
      "nome": "Migração de matrículas · lote outubro",
      "worktree": "C:/repos/migracao-matriculas",
      "agentes": [
        {
          "id": "gemini-matriculas-04",
          "tipo": "gemini",
          "modelo": "gemini-2.5-pro",
          "effort": "high",
          "papel": "comparar contratos de importação",
          "inicio": "2026-10-29T07:30:00-03:00",
          "fim": null,
          "segundos": 6540,
          "estado": "travado",
          "progresso": "Sem nova saída há mais de 10 minutos",
          "resultado": null,
          "veredito": null,
          "tokens": 30110,
          "cotaAprox": 8.2,
          "conta": "gemini-equipe"
        },
        {
          "id": "codex-matriculas-05",
          "tipo": "codex",
          "modelo": "gpt-5.6-terra",
          "effort": "high",
          "papel": "validar diff do importador",
          "inicio": "2026-10-29T08:15:00-03:00",
          "fim": "2026-10-29T08:44:32-03:00",
          "segundos": 1772,
          "estado": "concluido",
          "progresso": "Validação concluída",
          "resultado": "12 campos compatíveis; 1 campo requer migração",
          "veredito": {
            "tipo": "relatorio_falso",
            "achados": ["o relatório dizia 0 divergências", "há divergência em codigo_inep"]
          },
          "tokens": 24800,
          "cotaAprox": 3.1,
          "conta": ".codex-b"
        },
        {
          "id": "claude-matriculas-06",
          "tipo": "claude",
          "modelo": "claude-opus-4-1",
          "effort": "medium",
          "papel": "responder bloqueio do lote",
          "inicio": "2026-10-29T09:01:00-03:00",
          "fim": null,
          "segundos": 366,
          "estado": "pergunta",
          "progresso": "Aguardando confirmação do schema legado",
          "resultado": null,
          "veredito": null,
          "tokens": 6400,
          "cotaAprox": 1.1,
          "conta": "claude-trabalho"
        }
      ]
    },
    {
      "nome": "Sem tarefa",
      "worktree": "C:/repos/centro-agentes",
      "agentes": [
        {
          "id": "processo-apoio-07",
          "tipo": "processo",
          "modelo": null,
          "effort": null,
          "papel": "Sem tarefa",
          "inicio": "2026-10-29T09:15:00-03:00",
          "fim": null,
          "segundos": 72,
          "estado": "rodando",
          "progresso": "Servidor local aguardando conexão",
          "resultado": null,
          "veredito": null,
          "tokens": null,
          "cotaAprox": null,
          "conta": "local"
        },
        {
          "id": "processo-apoio-08",
          "tipo": "processo",
          "modelo": null,
          "effort": null,
          "papel": "coletar logs sem tarefa",
          "inicio": "2026-10-29T08:20:00-03:00",
          "fim": "2026-10-29T08:22:11-03:00",
          "segundos": 131,
          "estado": "concluido",
          "progresso": "Coleta encerrada",
          "resultado": "18 linhas de log salvas",
          "veredito": null,
          "tokens": null,
          "cotaAprox": null,
          "conta": "local"
        }
      ]
    }
  ],
  "resumo": {
    "rodando": 2,
    "feitosHoje": 3,
    "voltaram": 1,
    "falharam": 1
  },
  "cotas": [
    {
      "conta": ".codex",
      "janela": 300,
      "pct": 3.0,
      "resetaEm": "2026-10-29T13:00:00-03:00"
    },
    {
      "conta": ".codex",
      "janela": 10080,
      "pct": 8.0,
      "resetaEm": "2026-11-05T09:00:00-03:00"
    },
    {
      "conta": ".codex-b",
      "janela": 300,
      "pct": 11.0,
      "resetaEm": "2026-10-29T14:00:00-03:00"
    }
  ]
}

export default fixture
