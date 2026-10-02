#!/usr/bin/env python3
"""Escolhe modelo e effort para uma delegação, usando o Laya local."""
import argparse
import json
import os
import re
import sys
import time
import warnings
from datetime import date, datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import status

warnings.filterwarnings("ignore")

MODELOS = {
    "luna": "gpt-5.6-luna",
    "terra": "gpt-5.6-terra",
    "sol": "gpt-5.6-sol",
}
EFFORTS = ("low", "medium", "high")
LIMIAR_CONFIANCA = 0.55  # ponytail: limiar chutado, recalibrar com o log
REGRA_DURA = re.compile(
    r"\b(segurança|security|migration|migração|produção|prod|dinheiro|"
    r"pagamento|concorrência|race)\b",
    re.IGNORECASE,
)
_router = None


def ler_briefing(valor):
    if valor == "-":
        return sys.stdin.read()
    return valor


def estado_laya(briefing):
    # O contexto do Laya é curto; preserva o começo, onde fica o objetivo.
    return briefing[:1500]


def router():
    global _router
    if _router is None:
        from laya import Router

        _router = Router(default="multilingual")
    return _router


def decidir(briefing):
    inicio = time.perf_counter()
    regra = bool(REGRA_DURA.search(briefing))
    respostas = None
    if not regra:
        respostas = router().predict(estado_laya(briefing), {
            "modelo": {
                "type": "choice",
                "instructions": "Escolha o modelo mais adequado ao briefing.",
                "criteria": {
                    "luna": "mecânico, repetitivo, leitura de volume (resumir arquivo grande, varrer repo, renomear, aplicar padrão conhecido)",
                    "terra": "default geral, implementação comum com escopo claro",
                    "sol": "difícil ou ambíguo, bug sem causa conhecida, arquitetura, concorrência, segurança, dinheiro",
                },
            },
            "effort": {
                "type": "score",
                "instructions": "Avalie o esforço necessário para executar o briefing.",
                "criteria": [
                    "low: trivial/mecânico",
                    "medium: normal",
                    "high: difícil, muitas partes, risco",
                ],
            },
        })["answers"]

    if regra:
        modelo, motivo = "sol", "regra"
    elif respostas["modelo"].get("answer_confidence", 0) < LIMIAR_CONFIANCA:
        modelo, motivo = "terra", "baixa_confianca"
    else:
        modelo, motivo = respostas["modelo"]["choice"], "laya"

    if regra:
        effort = "high"
    else:
        score = round(float(respostas["effort"]["score"]))
        effort = EFFORTS[min(2, max(0, score))]

    probabilidades = {
        "modelo": respostas["modelo"].get("probabilities", {}) if respostas else {},
        "effort": respostas["effort"].get("probabilities", {}) if respostas else {},
    }
    resultado = {
        "modelo": MODELOS[modelo],
        "effort": effort,
        "motivo": motivo,
        "confianca": (respostas["modelo"].get("answer_confidence")
                      if respostas and motivo != "regra" else None),
        "probabilidades": probabilidades,
        "ms": round((time.perf_counter() - inicio) * 1000, 2),
    }
    return resultado


SOMBRA = os.path.expanduser("~/.claude/rotear-sombra.jsonl")
NIVEIS = ("luna", "terra", "sol")


def rotulo_desfecho(real, desfecho):
    indice = NIVEIS.index(real)
    if desfecho == "refez":
        indice = min(len(NIVEIS) - 1, indice + 1)
    elif desfecho == "exagero":
        indice = max(0, indice - 1)
    return NIVEIS[indice]


def acerto_regra_terra(briefing, alvo):
    escolhido = "sol" if REGRA_DURA.search(briefing) else "terra"
    return escolhido == alvo


def _data_rollout(caminho):
    partes = os.path.normpath(caminho).split(os.sep)
    try:
        indice = next(i for i, parte in enumerate(partes)
                      if re.fullmatch(r"\d{4}", parte))
        return date(int(partes[indice]), int(partes[indice + 1]),
                    int(partes[indice + 2]))
    except (StopIteration, IndexError, ValueError):
        return None


def _texto_usuario(evento):
    payload = evento.get("payload") or {}
    if evento.get("type") != "response_item" or payload.get("type") != "message":
        return None
    if payload.get("role") != "user":
        return None
    textos = []
    for item in payload.get("content") or []:
        if isinstance(item, dict) and item.get("type") in ("input_text", "text"):
            textos.append(item.get("text", ""))
    return "".join(textos) or None


def _eh_injecao(texto):
    inicio = texto.lstrip()
    return inicio.startswith("# AGENTS.md instructions") or inicio.startswith("<")


def _ler_rollout(caminho):
    eventos = list(status._loads(caminho))
    if not eventos:
        raise ValueError("rollout vazio")
    sessao = next((e for e in eventos
                   if e.get("type") == "session_meta"), {})
    meta = sessao.get("payload") or {}
    inicio = meta.get("timestamp") or sessao.get("timestamp")
    rollout_id = meta.get("id") or meta.get("session_id") or os.path.basename(caminho)
    contexto = None
    for evento in eventos:
        if evento.get("type") == "turn_context":
            payload = evento.get("payload") or {}
            if payload.get("model"):
                contexto = payload
    if not contexto or contexto.get("model") not in MODELOS.values():
        return None
    briefing = next((texto for evento in eventos
                     if (texto := _texto_usuario(evento)) is not None
                     and not _eh_injecao(texto)), None)
    if not briefing or not contexto:
        return None
    return {
        "id": rollout_id,
        "inicio": inicio,
        "briefing": briefing,
        "real": {"modelo": contexto.get("model"),
                 "effort": contexto.get("effort") or contexto.get("reasoning_effort")},
    }


def _ler_sombra():
    registros = []
    try:
        with open(SOMBRA, encoding="utf-8") as arquivo:
            for linha in arquivo:
                try:
                    registro = json.loads(linha)
                except (TypeError, ValueError):
                    continue
                if isinstance(registro, dict) and registro.get("id"):
                    registros.append(registro)
    except OSError:
        pass
    return registros


def coletar(dias):
    existentes = {registro["id"] for registro in _ler_sombra()}
    corte = date.today() - timedelta(days=max(0, dias - 1))
    caminhos = []
    for home in status.codex_profile_homes():
        for caminho in status._rollouts(home):
            if _data_rollout(caminho) is None or _data_rollout(caminho) < corte:
                continue
            caminhos.append(caminho)
    total = len(caminhos)
    novos, falhas, processados = 0, 0, 0
    arquivo_sombra = None
    try:
        for caminho in caminhos:
            processados += 1
            if processados % 25 == 0:
                print("{}/{}".format(processados, total), file=sys.stderr)
            try:
                rollout = _ler_rollout(caminho)
                if not rollout or rollout["id"] in existentes:
                    continue
                briefing = rollout["briefing"]
                resultado = decidir(briefing)
                rollout["briefing"] = briefing[:200]
                rollout["laya"] = {"modelo": resultado["modelo"],
                                   "effort": resultado["effort"],
                                   "motivo": resultado["motivo"],
                                   "confianca": resultado["confianca"],
                                   "probabilidades": resultado["probabilidades"]}
                rollout["desfecho"] = None
                if arquivo_sombra is None:
                    os.makedirs(os.path.dirname(SOMBRA), exist_ok=True)
                    arquivo_sombra = open(SOMBRA, "a", encoding="utf-8")
                arquivo_sombra.write(json.dumps(rollout, ensure_ascii=False) + "\n")
                arquivo_sombra.flush()
                novos += 1
                existentes.add(rollout["id"])
            except Exception:
                falhas += 1
    finally:
        if arquivo_sombra is not None:
            arquivo_sombra.close()
    print("coletadas: {} (falhas: {})".format(novos, falhas))


def _nivel_modelo(modelo):
    return next((nivel for nivel, nome in MODELOS.items() if nome == modelo), modelo)


def desfecho(alvo, valor):
    coletar(7)
    registros = _ler_sombra()
    if alvo == "ultimo":
        candidatos = [max(registros,
                          key=lambda r: (r.get("inicio") is not None,
                                          r.get("inicio") or ""))] if registros else []
    else:
        candidatos = [r for r in registros if r["id"].startswith(alvo)]
    if not candidatos:
        raise ValueError("id inexistente: {}".format(alvo))
    if len(candidatos) > 1:
        raise ValueError("prefixo ambiguo: {}".format(alvo))
    candidatos[0]["desfecho"] = valor
    temporario = SOMBRA + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        for registro in registros:
            arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
    os.replace(temporario, SOMBRA)


def _percentual(acertos, total):
    return "{}%".format(round(100 * acertos / total)) if total else "n/a"


def relatorio():
    registros = _ler_sombra()
    rotulados = [r for r in registros if r.get("desfecho")]
    print("total: {}; com desfecho: {}".format(len(registros), len(rotulados)))
    estrategias = {"laya": 0, "regra+terra": 0, "sempre terra": 0}
    for registro in rotulados:
        real = _nivel_modelo(registro["real"].get("modelo"))
        alvo = rotulo_desfecho(real, registro["desfecho"])
        laya = _nivel_modelo(registro["laya"].get("modelo"))
        estrategias["laya"] += laya == alvo
        estrategias["regra+terra"] += acerto_regra_terra(registro["briefing"], alvo)
        estrategias["sempre terra"] += alvo == "terra"
    for nome, acertos in estrategias.items():
        print("{}: {}/{} ({})".format(nome, acertos, len(rotulados),
                                      _percentual(acertos, len(rotulados))))
    faixas = (("<0.55", lambda v: v < .55), ("0.55-0.8", lambda v: .55 <= v < .8),
              (">=0.8", lambda v: v >= .8))
    for nome, teste in faixas:
        faixa = [r for r in rotulados
                 if isinstance(r.get("laya", {}).get("confianca"), (int, float))
                 and teste(r["laya"]["confianca"])]
        acertos = sum(_nivel_modelo(r["laya"].get("modelo")) ==
                      rotulo_desfecho(_nivel_modelo(r["real"].get("modelo")), r["desfecho"])
                      for r in faixa)
        print("laya confidence {}: {}/{} ({})".format(nome, acertos, len(faixa),
                                                       _percentual(acertos, len(faixa))))
    regra = [r for r in rotulados if r.get("laya", {}).get("motivo") == "regra"]
    print("laya regra: {}".format(len(regra)))
    sem_desfecho = [r for r in registros if not r.get("desfecho")]
    concordantes = sum(_nivel_modelo(r["laya"].get("modelo")) ==
                       _nivel_modelo(r["real"].get("modelo")) for r in sem_desfecho)
    print("concordancia laya x real (sem desfecho): {}/{} ({})".format(
        concordantes, len(sem_desfecho), _percentual(concordantes, len(sem_desfecho))))


def gravar_log(briefing, resultado):
    registro = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "briefing": briefing[:200],
        "decisao": {"modelo": resultado["modelo"], "effort": resultado["effort"]},
        "motivo": resultado["motivo"],
        "probabilidades": resultado["probabilidades"],
    }
    try:
        caminho = os.path.expanduser("~/.claude/rotear-log.jsonl")
        with open(caminho, "a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError:
        pass


def selfcheck():
    assert _eh_injecao("  # AGENTS.md instructions for repo")
    assert _eh_injecao("<environment_context>")
    assert _eh_injecao("\n<user_instructions>")
    assert not _eh_injecao("resuma o briefing")
    assert rotulo_desfecho("luna", "ok") == "luna"
    assert rotulo_desfecho("luna", "refez") == "terra"
    assert rotulo_desfecho("terra", "refez") == "sol"
    assert rotulo_desfecho("sol", "refez") == "sol"
    assert rotulo_desfecho("sol", "exagero") == "terra"
    assert rotulo_desfecho("luna", "exagero") == "luna"
    assert acerto_regra_terra("revisar pagamento", "sol")
    assert acerto_regra_terra("implementar endpoint comum", "terra")
    casos = [
        ("resuma todos os arquivos JSON grandes deste repositório", "luna"),
        ("renomeie a variável antiga em todos os módulos", "luna"),
        ("implemente o endpoint descrito, com escopo claro", "terra"),
        ("adicione a validação comum solicitada no formulário", "terra"),
        ("investigue um bug intermitente sem causa conhecida", "sol"),
        ("revise a segurança do fluxo de pagamento", "sol"),
    ]
    assert REGRA_DURA.search("revise a segurança")
    assert REGRA_DURA.search("rodar migration em prod")
    assert REGRA_DURA.search("race condition")
    assert not REGRA_DURA.search("listar produtos")
    assert not REGRA_DURA.search("produzir relatório")
    assert not REGRA_DURA.search("ler o stack trace")
    acertos = 0
    for briefing, esperado in casos:
        try:
            resultado = decidir(briefing)
            atual = next(chave for chave, valor in MODELOS.items()
                         if valor == resultado["modelo"])
            acertos += atual == esperado
            print("{}: {} (esperado {})".format(
                "acerto" if atual == esperado else "erro", atual, esperado))
        except Exception as erro:
            print("erro: {} ({})".format(esperado, erro))
    print("selfcheck: {}/{} acertos".format(acertos, len(casos)))


def main():
    parser = argparse.ArgumentParser(description="Roteia um briefing para modelo e effort")
    parser.add_argument("briefing", nargs="?", help="briefing ou - para stdin")
    parser.add_argument("--json", action="store_true", dest="em_json")
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--coletar", action="store_true")
    parser.add_argument("--dias", type=int, default=7)
    parser.add_argument("--desfecho", nargs=2, metavar=("ALVO", "VALOR"))
    parser.add_argument("--relatorio", action="store_true")
    args = parser.parse_args()
    if args.selfcheck:
        selfcheck()
        return 0
    if args.coletar:
        if args.dias < 1:
            parser.error("--dias deve ser positivo")
        router()
        coletar(args.dias)
        return 0
    if args.desfecho:
        if args.desfecho[1] not in ("ok", "refez", "exagero"):
            parser.error("VALOR deve ser ok, refez ou exagero")
        try:
            desfecho(*args.desfecho)
        except ValueError as erro:
            print("erro: {}".format(erro), file=sys.stderr)
            return 1
        return 0
    if args.relatorio:
        relatorio()
        return 0
    if args.briefing is None:
        parser.error("informe o briefing ou use - para stdin")
    briefing = ler_briefing(args.briefing)
    resultado = decidir(briefing)
    gravar_log(briefing, resultado)
    if args.em_json:
        print(json.dumps(resultado, ensure_ascii=False))
    else:
        print(resultado["modelo"], resultado["effort"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
