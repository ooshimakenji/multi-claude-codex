#!/usr/bin/env python3
"""Escolhe modelo e effort para uma delegação, usando o Laya local."""
import argparse
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone

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
        "probabilidades": probabilidades,
        "ms": round((time.perf_counter() - inicio) * 1000, 2),
    }
    return resultado


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
    args = parser.parse_args()
    if args.selfcheck:
        selfcheck()
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
