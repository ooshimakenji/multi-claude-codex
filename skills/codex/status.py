#!/usr/bin/env python3
"""Quanto custou o que, dos dois lados da ponte Claude <-> Codex.

Cada execucao imprime o estado atual E o delta desde a execucao anterior --
delegue no meio de duas rodadas e o delta e a economia real, nao uma estimativa.

Fontes (tudo local, tudo read-only):
  Codex : $CODEX_HOME/sessions/**/rollout-*.jsonl
          -> turn_context.model      (qual modelo REALMENTE rodou)
          -> token_count.info        (tokens do job)
          -> token_count.rate_limits (% da janela de quota)
  Claude: $CLAUDE_CONFIG_DIR/projects/**/<uuid>.jsonl
          -> message.usage           (tokens da sessao)

Uso:  python status.py            imprime estado + delta
      python status.py --line      uma linha, para statusline (leve, read-only)
      python status.py --reset     esquece o marco anterior
      python status.py --selftest  roda os asserts
"""
import glob
import json
import os
import sys

HOME = os.path.expanduser("~")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
CLAUDE_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
MARK = os.path.join(CLAUDE_HOME, "codex-status-mark.json")


def _loads(path):
    """Cada linha e um JSON independente; linha corrompida nao derruba a leitura."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def newest(pattern):
    hits = glob.glob(pattern, recursive=True)
    return max(hits, key=os.path.getmtime) if hits else None


def _ultimo_token_count(linhas):
    """Ultimo total_token_usage de uma lista de linhas jsonl, ou None."""
    for linha in reversed(linhas):
        try:
            payload = json.loads(linha).get("payload") or {}
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "token_count":
            return (payload.get("info") or {}).get("total_token_usage")
    return None


def codex_tokens_total():
    """Soma dos acumulados de TODOS os rollouts.

    Ler so o rollout mais novo quebra o delta: a rodada seguinte pode cair em
    outro arquivo, e a subtracao vira lixo -- as vezes negativa. Cada rollout
    guarda o proprio acumulado, entao a soma deles e estavel entre rodadas.
    Rollout antigo nao muda mais, e le-se so o rabo de cada um.
    """
    total = 0
    for arq in glob.glob(os.path.join(CODEX_HOME, "sessions", "**", "*.jsonl"), recursive=True):
        try:
            uso = _ultimo_token_count(tail(arq, 65536))
        except OSError:
            continue
        if uso:
            total += uso.get("total_tokens", 0)
    return total


def codex_side():
    """Tokens = soma de todos os rollouts. Modelo/quota = rollout mais recente
    que os tenha: um job curto pode nao gravar turn_context, e ficar sem modelo
    no display so porque foi o ultimo a rodar seria pior que olhar um pouco atras."""
    arqs = sorted(glob.glob(os.path.join(CODEX_HOME, "sessions", "**", "*.jsonl"),
                            recursive=True), key=os.path.getmtime, reverse=True)
    out = {"arquivo": os.path.basename(arqs[0]) if arqs else None,
           "tokens": codex_tokens_total()}
    for arq in arqs[:10]:
        for ev in _loads(arq):
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if ev.get("type") == "turn_context" and payload.get("model") and "modelo" not in out:
                out["modelo"] = payload["model"]
                out["effort"] = payload.get("effort") or payload.get("reasoning_effort")
                sandbox = payload.get("sandbox_policy") or {}
                out["sandbox"] = sandbox.get("type") if isinstance(sandbox, dict) else sandbox
            if payload.get("type") == "token_count" and "quotas" not in out:
                rl = payload.get("rate_limits") or {}
                # a API devolve primary (5h) e secondary (semanal); as vezes so uma
                janelas = [(j.get("window_minutes"), j.get("used_percent"))
                           for j in (rl.get("primary"), rl.get("secondary"))
                           if isinstance(j, dict) and "used_percent" in j]
                if janelas:
                    out["quotas"] = janelas
                    out["quota_pct"] = janelas[0][1]   # delta acompanha a mais curta
        if "modelo" in out and "quotas" in out:
            break
    return out


def claude_side():
    path = newest(os.path.join(CLAUDE_HOME, "projects", "**", "*.jsonl"))
    out = {"arquivo": os.path.basename(path) if path else None, "tokens": 0}
    if not path:
        return out
    for ev in _loads(path):
        usage = (ev.get("message") or {}).get("usage")
        if not isinstance(usage, dict):
            continue
        # cache_read e barato mas nao e gratis: conta tudo que entrou/saiu.
        out["tokens"] += sum(v for v in usage.values() if isinstance(v, int))
    return out


def snapshot():
    return {"codex": codex_side(), "claude": claude_side()}


def previous():
    try:
        with open(MARK, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def janela(minutos):
    if not minutos:
        return "?"
    if minutos < 60:
        return "{}min".format(minutos)
    if minutos < 1440:
        return "{}h".format(minutos // 60)
    return "{}d".format(minutos // 1440)


def fmt(n):
    return "{:,}".format(n).replace(",", ".")


def report(now, before):
    lines = []
    cx, cl = now["codex"], now["claude"]
    lines.append("CODEX   modelo={m} effort={e} sandbox={s}".format(
        m=cx.get("modelo", "?"), e=cx.get("effort", "?"), s=cx.get("sandbox", "?")))
    quotas = " ".join("{}={}%".format(janela(w), p) for w, p in cx.get("quotas", [])) or "?"
    lines.append("        tokens={t}  quota {q}".format(t=fmt(cx.get("tokens", 0)), q=quotas))
    lines.append("CLAUDE  tokens={t} (sessao inteira)".format(t=fmt(cl["tokens"])))

    if before:
        d_cx = cx.get("tokens", 0) - before["codex"].get("tokens", 0)
        d_cl = cl["tokens"] - before["claude"].get("tokens", 0)
        q_now, q_before = cx.get("quota_pct"), before["codex"].get("quota_pct")
        d_q = round(q_now - q_before, 2) if None not in (q_now, q_before) else None
        lines.append("")
        lines.append("DELTA desde a ultima rodada:")
        quota = "  ({:+}% de quota)".format(d_q) if d_q is not None else ""
        lines.append("        Codex  {t:>14}{q}".format(t=fmt(d_cx), q=quota))
        lines.append("        Claude {t:>14}".format(t=fmt(d_cl)))
        if d_cx > 0 and d_cl >= 0:
            razao = "infinita" if d_cl == 0 else "{:.1f}x".format(d_cx / d_cl)
            lines.append("        -> {} token(s) no Codex por token no Claude".format(razao))
    else:
        lines.append("")
        lines.append("(sem marco anterior -- rode de novo depois de delegar p/ ver o delta)")
    return "\n".join(lines)


def tail(path, nbytes=262144):
    """Ultimos bytes do arquivo, em linhas inteiras.

    Transcript do Claude passa de 30 MB; a statusline roda a cada render do
    prompt, entao ler o arquivo todo esta fora de questao.
    """
    TETO = 8 << 20
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        while True:
            fh.seek(max(0, size - nbytes))
            data = fh.read()
            if size > nbytes:
                # a primeira linha da janela veio cortada ao meio: descarta
                data = data.partition(b"\n")[2]
            linhas = [l for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
            # uma unica linha (tool result enorme) pode ser maior que a janela e
            # zerar tudo. Cresce ate achar linha inteira, o arquivo acabar, ou o teto.
            if linhas or nbytes >= size or nbytes >= TETO:
                return linhas
            nbytes = min(nbytes * 8, TETO)


def contexto_atual(path):
    """Tamanho do contexto vivo = tokens de entrada da ULTIMA requisicao.

    Nao e o acumulado da sessao (isso exigiria varrer o arquivo inteiro). Para
    uma statusline o numero util e este: o quanto a proxima chamada ja carrega.
    """
    for linha in reversed(tail(path)):
        try:
            usage = (json.loads(linha).get("message") or {}).get("usage")
        except ValueError:
            continue
        if isinstance(usage, dict):
            return sum(usage.get(k, 0) or 0 for k in
                       ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return 0


def humano(n):
    for limite, sufixo in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if n >= limite:
            return "{:.1f}{}".format(n / limite, sufixo)
    return str(n)


def line(stdin_json=None):
    """Uma linha para statusline. NUNCA grava o marco -- gravar a cada render
    zeraria o delta e o numero nunca sairia de ~0."""
    partes = []
    try:
        cx = codex_side()
        if cx.get("modelo"):
            q = " ".join("{}{}%".format(janela(w), p) for w, p in cx.get("quotas", []))
            partes.append(("codex " + cx["modelo"].replace("gpt-5.6-", "") + " " + q).strip())
    except OSError:
        pass
    try:
        path = (stdin_json or {}).get("transcript_path") or newest(
            os.path.join(CLAUDE_HOME, "projects", "**", "*.jsonl"))
        if path and os.path.exists(path):
            partes.append("ctx " + humano(contexto_atual(path)))
    except OSError:
        pass
    return " | ".join(partes)


def selftest():
    import tempfile

    d = tempfile.mkdtemp()
    roll = os.path.join(d, "sessions", "2026", "01", "01")
    proj = os.path.join(d, "projects", "algum-projeto")
    os.makedirs(roll)
    os.makedirs(proj)
    with open(os.path.join(roll, "rollout-x.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "turn_context", "payload": {
            "model": "gpt-5.6-luna", "sandbox_policy": {"type": "read-only"}}}) + "\n")
        fh.write("{ linha corrompida\n")
        fh.write(json.dumps({"type": "event_msg", "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": 90000}},
            "rate_limits": {"primary": {"used_percent": 3.0, "window_minutes": 300},
                            "secondary": {"used_percent": 8.0, "window_minutes": 10080}}}}) + "\n")
    with open(os.path.join(proj, "s.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"message": {"usage": {
            "input_tokens": 100, "output_tokens": 50, "service_tier": "x"}}}) + "\n")

    global CODEX_HOME, CLAUDE_HOME
    CODEX_HOME = CLAUDE_HOME = d
    snap = snapshot()
    assert snap["codex"]["modelo"] == "gpt-5.6-luna", snap
    assert snap["codex"]["sandbox"] == "read-only", snap
    assert snap["codex"]["tokens"] == 90000, snap
    assert snap["codex"]["quota_pct"] == 3.0, snap
    # 'service_tier' e str: nao pode entrar na soma
    assert snap["claude"]["tokens"] == 150, snap

    # P1 (achado pelo proprio codex review): somar TODOS os rollouts, nao so o novo
    roll2 = os.path.join(d, "sessions", "2026", "01", "02")
    os.makedirs(roll2)
    with open(os.path.join(roll2, "rollout-y.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "event_msg", "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": 500}}}}) + chr(10))
    assert codex_tokens_total() == 90500, codex_tokens_total()
    snap = snapshot()   # re-mede: o rollout novo tem que entrar na conta
    assert snap["codex"]["tokens"] == 90500, snap
    # rollout-y nao tem turn_context: o modelo tem que vir do anterior, nao sumir
    assert snap["codex"]["modelo"] == "gpt-5.6-luna", snap
    assert snap["codex"]["quota_pct"] == 3.0, snap

    antes = {"codex": {"tokens": 0, "quota_pct": 1.0}, "claude": {"tokens": 50}}
    saida = report(snap, antes)
    assert "905.0x" in saida, saida       # 90500 Codex / 100 Claude
    assert "+2.0%" in saida, saida

    # --- modo statusline ---
    transcript = os.path.join(proj, "s.jsonl")
    # tail() tem que descartar a linha cortada ao meio, nao estourar
    assert tail(transcript, nbytes=10) != [], "janela pequena tem que crescer, nao voltar vazia"
    # contexto = ultima requisicao (100 + 0 + 0), nao o acumulado (150)
    assert contexto_atual(transcript) == 100, contexto_atual(transcript)
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"message": {"usage": {
            "input_tokens": 7, "cache_read_input_tokens": 2000, "output_tokens": 999}}}) + "\n")
    assert contexto_atual(transcript) == 2007, contexto_atual(transcript)  # output nao conta

    uma = line({"transcript_path": transcript})
    assert "codex luna" in uma, uma        # prefixo gpt-5.6- some
    assert uma == "codex luna 5h3.0% 7d8.0% | ctx 2.0k", uma

    # o contrato que importa: --line NAO pode gravar o marco
    global MARK
    MARK = os.path.join(d, "mark.json")
    line({"transcript_path": transcript})
    assert not os.path.exists(MARK), "--line gravou o marco e zerou o delta"

    assert humano(999) == "999" and humano(20455411) == "20.5M", humano(20455411)
    print("selftest ok")


if __name__ == "__main__":
    if "--line" in sys.argv:
        # statusline: recebe JSON da sessao pelo stdin, e nao pode nunca quebrar
        # o prompt do usuario -- na duvida, imprime nada.
        dados = None
        try:
            if not sys.stdin.isatty():
                dados = json.loads(sys.stdin.read() or "{}")
        except (ValueError, OSError):
            dados = None
        try:
            print(line(dados))
        except Exception:
            pass
    elif "--selftest" in sys.argv:
        selftest()
    elif "--reset" in sys.argv:
        if os.path.exists(MARK):
            os.remove(MARK)
        print("marco apagado")
    else:
        now = snapshot()
        print(report(now, previous()))
        with open(MARK, "w", encoding="utf-8") as fh:
            json.dump(now, fh)
