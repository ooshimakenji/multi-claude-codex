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
      python status.py --reset    esquece o marco anterior
      python status.py --selftest roda os asserts
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


def codex_side():
    path = newest(os.path.join(CODEX_HOME, "sessions", "**", "*.jsonl"))
    out = {"arquivo": os.path.basename(path) if path else None}
    if not path:
        return out
    for ev in _loads(path):
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if ev.get("type") == "turn_context" and payload.get("model"):
            out["modelo"] = payload["model"]
            out["effort"] = payload.get("effort") or payload.get("reasoning_effort")
            sandbox = payload.get("sandbox_policy") or {}
            out["sandbox"] = sandbox.get("type") if isinstance(sandbox, dict) else sandbox
        if payload.get("type") == "token_count":
            info = payload.get("info") or {}
            total = info.get("total_token_usage") or {}
            if total:
                out["tokens"] = total.get("total_tokens", 0)
            limits = (payload.get("rate_limits") or {}).get("primary") or {}
            if "used_percent" in limits:
                out["quota_pct"] = limits["used_percent"]
                out["janela_min"] = limits.get("window_minutes")
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


def fmt(n):
    return "{:,}".format(n).replace(",", ".")


def report(now, before):
    lines = []
    cx, cl = now["codex"], now["claude"]
    lines.append("CODEX   modelo={m} effort={e} sandbox={s}".format(
        m=cx.get("modelo", "?"), e=cx.get("effort", "?"), s=cx.get("sandbox", "?")))
    janela = cx.get("janela_min")
    janela = "{}d".format(janela // 1440) if janela else "?"
    lines.append("        tokens={t}  quota={q}% (janela {w})".format(
        t=fmt(cx.get("tokens", 0)), q=cx.get("quota_pct", "?"), w=janela))
    lines.append("CLAUDE  tokens={t} (sessao inteira)".format(t=fmt(cl["tokens"])))

    if before:
        d_cx = cx.get("tokens", 0) - before["codex"].get("tokens", 0)
        d_cl = cl["tokens"] - before["claude"].get("tokens", 0)
        d_q = round(cx.get("quota_pct", 0) - before["codex"].get("quota_pct", 0), 2)
        lines.append("")
        lines.append("DELTA desde a ultima rodada:")
        lines.append("        Codex  {t:>14}  ({q:+}% de quota)".format(t=fmt(d_cx), q=d_q))
        lines.append("        Claude {t:>14}".format(t=fmt(d_cl)))
        if d_cx > 0 and d_cl >= 0:
            razao = "infinita" if d_cl == 0 else "{:.1f}x".format(d_cx / d_cl)
            lines.append("        -> {} token(s) no Codex por token no Claude".format(razao))
    else:
        lines.append("")
        lines.append("(sem marco anterior -- rode de novo depois de delegar p/ ver o delta)")
    return "\n".join(lines)


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
            "rate_limits": {"primary": {"used_percent": 3.0, "window_minutes": 10080}}}}) + "\n")
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

    antes = {"codex": {"tokens": 0, "quota_pct": 1.0}, "claude": {"tokens": 50}}
    saida = report(snap, antes)
    assert "900.0x" in saida, saida       # 90000 Codex / 100 Claude
    assert "+2.0%" in saida, saida
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
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
