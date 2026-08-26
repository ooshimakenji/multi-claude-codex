#!/usr/bin/env python3
"""Pergunta curta a provedores compativeis com OpenAI.

Chaves: use a variavel de ambiente do provedor ou
$CLAUDE_CONFIG_DIR/keys/<provedor>.txt (~/.claude se a variavel nao existir).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


PROVEDORES = {
    "nvidia": {
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "modelos": [
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        ],
        "env": "NVIDIA_API_KEY",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "modelos": ["gemini-flash-lite-latest"],
        "env": "GEMINI_API_KEY",
    },
}
TENTATIVAS = 3
TIMEOUT = 60
RETRY_STATUS = {429, 500, 502, 503, 504}
RATE_HEADERS = ("x-ratelimit-remaining", "x-ratelimit-remaining-requests",
                "ratelimit-remaining", "retry-after")


def default_key_path(provedor):
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return os.path.join(os.path.expanduser(config_dir), "keys", provedor + ".txt")


def resolve_key(provedor, arquivo=None):
    cfg = PROVEDORES[provedor]
    chave = os.environ.get(cfg["env"], "").strip()
    if chave:
        return chave
    caminhos = [arquivo] if arquivo else []
    caminhos.append(default_key_path(provedor))
    for caminho in caminhos:
        try:
            with open(caminho, encoding="utf-8") as fh:
                chave = fh.read().strip()
        except OSError:
            continue
        if chave:
            return chave
    return None


def build_payload(modelo, pergunta):
    return {"model": modelo, "messages": [{"role": "user", "content": pergunta}]}


def rate_limit_remaining(headers):
    if not headers:
        return None
    for desejado in RATE_HEADERS:
        for nome, valor in headers.items():
            if str(nome).lower() == desejado:
                return valor
    return None


def parse_response(data):
    if isinstance(data, (bytes, bytearray)):
        data = json.loads(data.decode("utf-8"))
    if isinstance(data, str):
        data = json.loads(data)
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("resposta sem texto")
    return content


def _call(provedor, modelo, chave, pergunta):
    cfg = PROVEDORES[provedor]
    body = json.dumps(build_payload(modelo, pergunta)).encode("utf-8")
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            req = urllib.request.Request(
                cfg["url"], data=body,
                headers={"Authorization": "Bearer " + chave,
                         "Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status = getattr(resp, "status", 200)
                restante = rate_limit_remaining(resp.headers)
                if status in RETRY_STATUS and tentativa < TENTATIVAS:
                    time.sleep(tentativa)
                    continue
                if not 200 <= status < 300:
                    return None, status, restante, "erro HTTP {}".format(status)
                try:
                    return parse_response(resp.read()), status, restante, None
                except (ValueError, KeyError, IndexError, TypeError):
                    return None, status, restante, "resposta invalida do provedor"
        except urllib.error.HTTPError as exc:
            restante = rate_limit_remaining(exc.headers)
            if exc.code in RETRY_STATUS and tentativa < TENTATIVAS:
                time.sleep(tentativa)
                continue
            return None, exc.code, restante, "erro HTTP {}".format(exc.code)
        except (urllib.error.URLError, TimeoutError, OSError):
            return None, None, None, "erro de rede"
    return None, None, None, "erro de rede"


def _call_modelos(provedor, modelos, chave, pergunta):
    """Tenta o proximo modelo so quando o anterior esta morto ou ausente."""
    ultimo = (None, None, None, "nenhum modelo configurado")
    modelo_usado = None
    for modelo in modelos:
        modelo_usado = modelo
        ultimo = _call(provedor, modelo, chave, pergunta)
        if ultimo[3] is None or ultimo[1] not in {404, 410}:
            return (*ultimo, modelo_usado)
    return (*ultimo, modelo_usado)


def _log(provedor, modelo, ok, status, restante):
    registro = {"ts": time.time(), "provedor": provedor, "modelo": modelo,
                "ok": bool(ok), "http": status, "restante": restante}
    caminho = os.path.expanduser("~/.claude/free-tier.jsonl")
    try:
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError:
        pass


def selftest():
    antigo = os.environ.get("NVIDIA_API_KEY")
    os.environ["NVIDIA_API_KEY"] = "chave-falsa-do-selftest"
    try:
        assert resolve_key("nvidia") == "chave-falsa-do-selftest"
    finally:
        if antigo is None:
            os.environ.pop("NVIDIA_API_KEY", None)
        else:
            os.environ["NVIDIA_API_KEY"] = antigo
    payload = build_payload("modelo-teste", "renomeie este arquivo")
    assert payload["messages"][0]["content"] == "renomeie este arquivo"
    assert parse_response({"choices": [{"message": {"content": "feito"}}]}) == "feito"
    assert rate_limit_remaining({"X-RateLimit-Remaining-Requests": "7"}) == "7"
    assert rate_limit_remaining({"Retry-After": "3"}) == "3"
    assert rate_limit_remaining({"content-type": "application/json"}) is None
    chamadas = []
    urlopen_original = urllib.request.urlopen

    class Resposta:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return b'{"choices": [{"message": {"content": "fallback ok"}}]}'

    def urlopen_falso(req, timeout):
        modelo = json.loads(req.data.decode("utf-8"))["model"]
        chamadas.append(modelo)
        if modelo == "modelo-morto":
            raise urllib.error.HTTPError(req.full_url, 410, "Gone", {}, None)
        return Resposta()

    urllib.request.urlopen = urlopen_falso
    try:
        resultado = _call_modelos("nvidia", ["modelo-morto", "modelo-vivo"],
                                  "chave-falsa", "teste")
        assert resultado == ("fallback ok", 200, None, None, "modelo-vivo")
        assert chamadas == ["modelo-morto", "modelo-vivo"]
    finally:
        urllib.request.urlopen = urlopen_original
    print("selftest ok")


def main():
    parser = argparse.ArgumentParser(
        description=("Chaves: use a variavel de ambiente do provedor, --arquivo-chave, "
                     "ou $CLAUDE_CONFIG_DIR/keys/<provedor>.txt "
                     "(~/.claude se CLAUDE_CONFIG_DIR nao estiver definido)."))
    parser.add_argument("--provedor", choices=sorted(PROVEDORES))
    parser.add_argument("--modelo", help="usa somente este modelo, sem fallback")
    parser.add_argument("--arquivo-chave", help="arquivo de chave apos a variavel de ambiente")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("pergunta", nargs="?")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.provedor or args.pergunta is None:
        parser.error("--provedor e uma pergunta sao obrigatorios")
    pergunta = sys.stdin.read() if args.pergunta == "-" else args.pergunta
    modelos = [args.modelo] if args.modelo else PROVEDORES[args.provedor]["modelos"]
    chave = resolve_key(args.provedor, args.arquivo_chave)
    if not chave:
        cfg = PROVEDORES[args.provedor]
        print("chave ausente: {} ou {}".format(cfg["env"],
                                               default_key_path(args.provedor)), file=sys.stderr)
        return 1
    texto, status, restante, erro, modelo_usado = _call_modelos(
        args.provedor, modelos, chave, pergunta)
    _log(args.provedor, modelo_usado, erro is None, status, restante)
    if erro:
        print(erro, file=sys.stderr)
        return 1
    sys.stdout.write(texto)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("erro ao executar ask", file=sys.stderr)
        sys.exit(1)
