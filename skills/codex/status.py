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
      python status.py --json      snapshot para consumidores de maquina
      python status.py --reset     esquece o marco anterior
      python status.py --selftest  roda os asserts
"""
import base64
import binascii
import glob
import json
import os
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
CLAUDE_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
MARK = os.path.join(CLAUDE_HOME, "codex-status-mark.json")

# Limite de free tier muda; numero velho SEM DATA vira mentira silenciosa.
# Por isso a data anda junto do numero e precisa ser atualizada quando conferido.
LIMITES = {
    "nvidia": (40, "conferido em 2026-08-26"),
    "gemini": (15, "conferido em 2026-08-26"),
}


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


def codex_profile_homes():
    """Diretorios dos perfis Codex que devem entrar na medicao.

    CODEX_HOME continua aceito para testes e instalacoes fora do padrao. Para
    os perfis normais, porem, um comando iniciado com CODEX_HOME apontando para
    qualquer perfil ainda precisa enxergar a familia inteira.
    """
    configurado = os.path.abspath(CODEX_HOME)
    diretorio_home = os.path.abspath(HOME)
    padrao = os.path.join(diretorio_home, ".codex")
    if os.path.dirname(configurado) == diretorio_home and (
            configurado == padrao or os.path.basename(configurado).startswith(".codex-")):
        candidatos = [padrao] + glob.glob(os.path.join(diretorio_home, ".codex-*"))
    else:
        candidatos = [configurado]

    vistos = set()
    perfis = []
    for caminho in candidatos:
        caminho = os.path.abspath(caminho)
        chave = os.path.normcase(caminho)
        if chave in vistos or not os.path.isdir(caminho):
            continue
        vistos.add(chave)
        perfis.append(caminho)
    return perfis


def _rollouts(home):
    return glob.glob(os.path.join(home, "sessions", "**", "*.jsonl"), recursive=True)


def codex_tokens_total(home=None):
    """Soma dos acumulados de TODOS os rollouts.

    Ler so o rollout mais novo quebra o delta: a rodada seguinte pode cair em
    outro arquivo, e a subtracao vira lixo -- as vezes negativa. Cada rollout
    guarda o proprio acumulado, entao a soma deles e estavel entre rodadas.
    Rollout antigo nao muda mais, e le-se so o rabo de cada um.
    """
    total = 0
    for arq in _rollouts(home or CODEX_HOME):
        try:
            uso = _ultimo_token_count(tail(arq, 65536))
        except OSError:
            continue
        if uso:
            total += uso.get("total_tokens", 0)
    return total


def codex_janela(minutos, home=None):
    """Tokens gastos no Codex dentro da janela viva.

    E este numero que prova que a delegacao aconteceu: `used_percent` so vem em
    inteiro (conferido em ~1900 amostras: 0.0, 1.0, ... 100.0, nunca fracionario),
    entao um job de 90k fica em 0% e parece que nada rodou.
    """
    if not minutos:
        return 0
    corte = time.time() - minutos * 60
    total = 0
    for arq in _rollouts(home or CODEX_HOME):
        try:
            if os.path.getmtime(arq) < corte:
                continue
            uso = _ultimo_token_count(tail(arq, 65536))
        except OSError:
            continue
        if uso:
            total += uso.get("total_tokens", 0)
    return total


def _codex_side_profile(home):
    """Tokens = soma de todos os rollouts. Modelo/quota = rollout mais recente que
    os tenha: um job curto pode nao gravar turn_context, e ficar sem modelo no
    display so porque foi o ultimo a rodar seria pior que olhar um pouco atras."""
    arqs = sorted(_rollouts(home), key=os.path.getmtime, reverse=True)
    out = {"arquivo": os.path.basename(arqs[0]) if arqs else None,
           "tokens": codex_tokens_total(home)}
    if arqs:
        out["idade_s"] = max(0, time.time() - os.path.getmtime(arqs[0]))
    for arq in arqs[:10]:
        modelo = quotas = None
        # O statusline nao pode varrer rollouts inteiros; o rabo contem as
        # leituras mais recentes de modelo e quota.
        for linha in tail(arq):
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            # dentro de um rollout, vale sempre a ULTIMA leitura: a primeira e o
            # estado ANTES da sessao gastar qualquer coisa.
            if ev.get("type") == "turn_context" and payload.get("model"):
                sandbox = payload.get("sandbox_policy") or {}
                modelo = (payload["model"],
                          payload.get("effort") or payload.get("reasoning_effort"),
                          sandbox.get("type") if isinstance(sandbox, dict) else sandbox)
            if payload.get("type") == "token_count":
                rl = payload.get("rate_limits") or {}
                # a API devolve primary (5h) e secondary (semanal); as vezes so uma
                janelas = [{"janela": j.get("window_minutes"),
                            "pct": j.get("used_percent"),
                            "reseta_em": j.get("resets_at")}
                           for j in (rl.get("primary"), rl.get("secondary"))
                           if isinstance(j, dict) and "used_percent" in j]
                if janelas:
                    quotas = janelas
        if modelo and "modelo" not in out:
            out["modelo"], out["effort"], out["sandbox"] = modelo
        if quotas and "quotas" not in out:
            out["quotas"] = quotas
            out["quota_pct"] = quotas[0]["pct"]  # delta acompanha a mais curta
            out["janela_tokens"] = codex_janela(quotas[0]["janela"], home)
        if "modelo" in out and "quotas" in out:
            break
    return out


def _codex_auth(home):
    """Extrai identidade, plano e validade; nunca retorna nem registra tokens."""
    caminho = os.path.join(home, "auth.json")
    try:
        with open(caminho, encoding="utf-8") as fh:
            auth = json.load(fh)
        token = ((auth.get("tokens") or {}).get("id_token"))
        partes = token.split(".") if isinstance(token, str) else []
        if len(partes) != 3:
            raise ValueError("JWT sem tres segmentos")
        segmento = partes[1] + ("=" * (-len(partes[1]) % 4))
        payload = json.loads(base64.urlsafe_b64decode(segmento))
        if not isinstance(payload, dict):
            raise ValueError("payload JWT invalido")
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            return {"email": None, "plano": None, "status": "sem_login"}
        print("aviso: nao foi possivel decodificar auth do perfil {} ({})".format(
            os.path.basename(home), type(exc).__name__), file=sys.stderr)
        return {"email": None, "plano": None, "status": "erro"}
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError,
            binascii.Error) as exc:
        # O aviso identifica apenas o perfil e o tipo do erro, nunca o JWT.
        print("aviso: nao foi possivel decodificar auth do perfil {} ({})".format(
            os.path.basename(home), type(exc).__name__), file=sys.stderr)
        return {"email": None, "plano": None, "status": "erro"}

    claim = payload.get("https://api.openai.com/auth") or {}
    tokens = auth.get("tokens") or {}
    if "access_token" in tokens:
        try:
            access_token = tokens.get("access_token")
            partes = access_token.split(".") if isinstance(access_token, str) else []
            if len(partes) != 3:
                raise ValueError("JWT sem tres segmentos")
            segmento = partes[1] + ("=" * (-len(partes[1]) % 4))
            access_payload = json.loads(base64.urlsafe_b64decode(segmento))
            if not isinstance(access_payload, dict):
                raise ValueError("payload JWT invalido")
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeError,
                binascii.Error) as exc:
            # O aviso identifica apenas o perfil e o tipo do erro, nunca o JWT.
            print("aviso: nao foi possivel decodificar auth do perfil {} ({})".format(
                os.path.basename(home), type(exc).__name__), file=sys.stderr)
            status = "erro"
        else:
            exp = access_payload.get("exp")
            if not isinstance(exp, int) or isinstance(exp, bool):
                status = "erro"
            elif exp > time.time():
                status = "ok"
            else:
                status = "expirado"
    else:
        exp = payload.get("exp")
        if not isinstance(exp, int) or isinstance(exp, bool):
            status = "erro"
        elif exp > time.time():
            status = "ok"
        else:
            status = "expirado"
    return {
        "email": payload.get("email"),
        "plano": claim.get("chatgpt_plan_type") if isinstance(claim, dict) else None,
        "status": status,
    }


def codex_side(contas=True):
    """Mantem o resumo antigo e acrescenta a medicao isolada por perfil."""
    # --line so precisa do resumo compativel; evita I/O de auth.json e dos
    # demais perfis a cada render do prompt.
    if not contas:
        return _codex_side_profile(CODEX_HOME)
    homes = codex_profile_homes()
    perfis = []
    sides = []
    for home in homes:
        side = _codex_side_profile(home)
        sides.append(side)
        identidade = _codex_auth(home)
        perfis.append({
            "perfil": os.path.basename(home),
            "email": identidade["email"],
            "plano": identidade["plano"],
            "status": identidade["status"],
            "tokens": side.get("tokens", 0),
            "quotas": side.get("quotas", []),
            "quota_pct": side.get("quota_pct"),
            "janela_tokens": side.get("janela_tokens", 0),
        })

    if not perfis:
        out = _codex_side_profile(CODEX_HOME)
        out["contas"] = []
        return out

    configurado = os.path.normcase(os.path.abspath(CODEX_HOME))
    indice = next((i for i, home in enumerate(homes)
                   if os.path.normcase(os.path.abspath(home)) == configurado), 0)
    out = dict(sides[indice])
    out["contas"] = perfis
    return out


def _epoch(valor):
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor)
        except ValueError:
            try:
                return datetime.fromisoformat(valor.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return None


def jobs_ativos():
    """Lista rollouts que ainda estao escrevendo e nao terminaram."""
    agora = time.time()
    corte = agora - 90
    encontrados = []
    arqs = glob.glob(os.path.join(CODEX_HOME, "sessions", "**", "*.jsonl"),
                     recursive=True)
    recentes = []
    for arq in arqs:
        try:
            mtime = os.path.getmtime(arq)
        except OSError:
            continue
        if mtime < corte:
            continue
        recentes.append((mtime, arq))

    # O filtro vem antes de abrir: rollouts antigos nao podem custar I/O aqui.
    for mtime, arq in sorted(recentes, reverse=True):
        try:
            linhas = tail(arq, 65536)
            primeiro = contexto = None
            # O cabecalho basta para inicio e contexto; limita a leitura de casos
            # quebrados que nunca gravaram turn_context.
            for indice, evento in enumerate(_loads(arq)):
                if not isinstance(evento, dict):
                    continue
                if primeiro is None:
                    primeiro = evento
                payload = evento.get("payload") or {}
                if evento.get("type") == "turn_context" and isinstance(payload, dict):
                    contexto = payload
                if indice >= 127:
                    break
        except (OSError, ValueError):
            continue

        ultimo = None
        for linha in reversed(linhas):
            try:
                evento = json.loads(linha)
            except ValueError:
                continue
            if isinstance(evento, dict):
                ultimo = evento
                break
        if not ultimo:
            continue
        payload = ultimo.get("payload") or {}
        if ultimo.get("type") == "task_complete" or (
                isinstance(payload, dict) and payload.get("type") == "task_complete"):
            continue

        uso = _ultimo_token_count(linhas)
        total = uso.get("total_tokens", 0) if isinstance(uso, dict) else 0
        cwd = contexto.get("cwd") if isinstance(contexto, dict) else None
        inicio = _epoch(primeiro.get("timestamp")) if isinstance(primeiro, dict) else None
        encontrados.append({
            "modelo": contexto.get("model") if isinstance(contexto, dict) else None,
            "cwd": cwd,
            "projeto": os.path.basename(os.path.normpath(cwd)) if cwd else None,
            "tokens": total,
            "segundos": max(0, agora - (inicio if inicio is not None else mtime)),
            "arquivo": os.path.basename(arq),
        })
    return encontrados


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


def free_tier_side():
    """Conta chamadas recentes e preserva a ultima leitura do servidor."""
    out = {provedor: {"usadas": 0, "limite": limite,
                      "restante_servidor": None}
           for provedor, (limite, _data) in LIMITES.items()}
    caminho = os.path.join(CLAUDE_HOME, "free-tier.jsonl")
    try:
        linhas = tail(caminho)
    except OSError:
        return out
    agora = time.time()
    corte = agora - 60
    for linha in linhas:
        try:
            evento = json.loads(linha)
        except ValueError:
            continue
        provedor = evento.get("provedor")
        if provedor not in out:
            continue
        try:
            ts = float(evento.get("ts"))
        except (TypeError, ValueError):
            continue
        if corte <= ts <= agora:
            out[provedor]["usadas"] += 1
    # O servidor conhece chamadas feitas fora deste processo; sua leitura vale mais
    # que a contagem local, mesmo que os dois numeros discordem.
    encontrados = set()
    for linha in reversed(linhas):
        try:
            evento = json.loads(linha)
        except ValueError:
            continue
        provedor = evento.get("provedor")
        if provedor in out and provedor not in encontrados:
            out[provedor]["restante_servidor"] = evento.get("restante")
            encontrados.add(provedor)
            if len(encontrados) == len(out):
                break
    return out


def snapshot():
    return {"codex": codex_side(), "claude": claude_side(),
            "free_tier": free_tier_side()}


def previous():
    try:
        with open(MARK, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def idade_curta(segundos):
    if segundos < 86400:
        return "·{}h".format(int(segundos // 3600))
    return "·{}d".format(int(segundos // 86400))


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
    quotas = " ".join("{}={}%".format(janela(q["janela"]), q["pct"])
                       for q in cx.get("quotas", [])) or "?"
    lines.append("        tokens={t}  quota {q}".format(t=fmt(cx.get("tokens", 0)), q=quotas))
    if cx.get("janela_tokens"):
        curta = (cx.get("quotas") or [{"janela": None}])[0]["janela"]
        lines.append("        na janela de {j}: {t} tokens".format(
            j=janela(curta), t=fmt(cx["janela_tokens"])))
    lines.append("CLAUDE  tokens={t} (sessao inteira)".format(t=fmt(cl["tokens"])))
    free = now.get("free_tier", {})
    if free:
        itens = []
        for provedor, dados in free.items():
            usado = (dados.get("restante_servidor")
                     if dados.get("restante_servidor") is not None
                     else dados.get("usadas", 0))
            itens.append("{}={}/{}min".format(provedor, usado, dados.get("limite", "?")))
        lines.append("FREE    " + " ".join(itens))

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
        cx = codex_side(contas=False)
        if cx.get("modelo"):
            nome = cx["modelo"].replace("gpt-5.6-", "")
            idade = cx.get("idade_s") or 0
            if idade > 7200:   # nao deixar "codex luna" parecer que roda agora
                nome += idade_curta(idade)
            campos = [nome]
            if cx.get("janela_tokens"):
                campos.append(humano(cx["janela_tokens"]))
            campos += ["{}{}%".format(janela(q["janela"]), q["pct"])
                       for q in cx.get("quotas", [])]
            identidade = _codex_auth(CODEX_HOME)
            marcador = " [relogar]" if identidade.get("status") != "ok" else ""
            partes.append("codex " + " ".join(campos) + marcador)
    except OSError:
        pass
    try:
        path = (stdin_json or {}).get("transcript_path") or newest(
            os.path.join(CLAUDE_HOME, "projects", "**", "*.jsonl"))
        if path and os.path.exists(path):
            partes.append("ctx " + humano(contexto_atual(path)))
    except OSError:
        pass
    try:
        for provedor, dados in free_tier_side().items():
            if dados.get("usadas", 0):
                usado = (dados.get("restante_servidor")
                         if dados.get("restante_servidor") is not None
                         else dados["usadas"])
                partes.append("{} {}/{}min".format(provedor, usado, dados["limite"]))
    except OSError:
        pass
    return " | ".join(partes)


def selftest():
    import tempfile

    def grava_auth(home, dados, access_dados=None):
        access_dados = dados if access_dados is None else access_dados
        segmento = base64.urlsafe_b64encode(
            json.dumps(dados).encode("utf-8")).decode("ascii").rstrip("=")
        access_segmento = base64.urlsafe_b64encode(
            json.dumps(access_dados).encode("utf-8")).decode("ascii").rstrip("=")
        with open(os.path.join(home, "auth.json"), "w", encoding="utf-8") as fh:
            json.dump({"tokens": {
                "id_token": "header." + segmento + ".sig",
                "access_token": "header." + access_segmento + ".sig",
            }}, fh)

    d = tempfile.mkdtemp()
    roll = os.path.join(d, "sessions", "2026", "01", "01")
    proj = os.path.join(d, "projects", "algum-projeto")
    os.makedirs(roll)
    os.makedirs(proj)

    assert _codex_auth(d)["status"] == "sem_login"
    auth_teste = tempfile.mkdtemp()
    grava_auth(auth_teste, {"exp": 1, "email": "x@y.com"})
    assert _codex_auth(auth_teste)["status"] == "expirado"
    grava_auth(auth_teste, {"exp": int(time.time()) + 3600, "email": "x@y.com"})
    assert _codex_auth(auth_teste)["status"] == "ok"
    grava_auth(auth_teste, {"email": "x@y.com"})
    assert _codex_auth(auth_teste)["status"] == "erro"
    grava_auth(auth_teste, {"exp": 1, "email": "x@y.com"},
               {"exp": int(time.time()) + 3600})
    assert _codex_auth(auth_teste)["status"] == "ok"

    with open(os.path.join(roll, "rollout-x.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "turn_context", "payload": {
            "model": "gpt-5.6-luna", "sandbox_policy": {"type": "read-only"}}}) + "\n")
        fh.write("{ linha corrompida\n")
        # duas leituras no mesmo rollout: a 1a e o estado ANTES de gastar.
        fh.write(json.dumps({"type": "event_msg", "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": 10}},
            "rate_limits": {"primary": {"used_percent": 0.0, "window_minutes": 300,
                                          "resets_at": 1787759175},
                            "secondary": {"used_percent": 0.0, "window_minutes": 10080,
                                           "resets_at": 1788345975}}}}) + "\n")
        fh.write(json.dumps({"type": "event_msg", "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": 90000}},
            "rate_limits": {"primary": {"used_percent": 3.0, "window_minutes": 300,
                                          "resets_at": 1787759175},
                            "secondary": {"used_percent": 8.0, "window_minutes": 10080,
                                           "resets_at": 1788345975}}}}) + "\n")
    with open(os.path.join(proj, "s.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"message": {"usage": {
            "input_tokens": 100, "output_tokens": 50, "service_tier": "x"}}}) + "\n")

    global CODEX_HOME, CLAUDE_HOME
    CODEX_HOME = CLAUDE_HOME = d
    snap = snapshot()
    assert snap["codex"]["modelo"] == "gpt-5.6-luna", snap
    assert snap["codex"]["sandbox"] == "read-only", snap
    assert snap["codex"]["contas"][0]["status"] == "sem_login", snap
    assert snap["codex"]["tokens"] == 90000, snap
    # REGRESSAO: ler a 1a amostra daria 0.0 e a statusline ficaria travada em zero
    assert snap["codex"]["quota_pct"] == 3.0, snap
    assert snap["codex"]["quotas"] == [
        {"janela": 300, "pct": 3.0, "reseta_em": 1787759175},
        {"janela": 10080, "pct": 8.0, "reseta_em": 1788345975},
    ], snap
    # o numero que prova que rodou
    assert snap["codex"]["janela_tokens"] == 90000, snap
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

    grava_auth(d, {"exp": int(time.time()) + 3600, "email": "x@y.com"})
    uma = line({"transcript_path": transcript})
    assert "codex luna" in uma, uma        # prefixo gpt-5.6- some
    assert uma == "codex luna 90.5k 5h3.0% 7d8.0% | ctx 2.0k", uma

    grava_auth(d, {"exp": 1, "email": "x@y.com"})
    uma = line({"transcript_path": transcript})
    assert "[relogar]" in uma, uma

    # --- free tier: janela local e leitura do limite do servidor ---
    free_log = os.path.join(d, "free-tier.jsonl")
    agora = time.time()
    with open(free_log, "w", encoding="utf-8") as fh:
        for evento in (
            {"ts": agora - 61, "provedor": "nvidia", "restante": "39"},
            {"ts": agora - 10, "provedor": "nvidia", "restante": None},
            {"ts": agora - 5, "provedor": "nvidia", "restante": "37"},
            {"ts": agora - 5, "provedor": "gemini", "restante": None},
        ):
            fh.write(json.dumps(evento) + "\n")
    free = free_tier_side()
    assert free["nvidia"]["usadas"] == 2, free
    assert free["gemini"]["usadas"] == 1, free
    assert free["nvidia"]["restante_servidor"] == "37", free
    snap = snapshot()
    assert "nvidia=37/40min" in report(snap, None), report(snap, None)
    uma = line({"transcript_path": transcript})
    assert "nvidia 37/40min" in uma and "gemini 1/15min" in uma, uma

    # --- jobs ativos: janela, evento final e inicio sao criterios separados ---
    agora = time.time()
    ativo = os.path.join(roll, "rollout-active.jsonl")
    concluido = os.path.join(roll, "rollout-done.jsonl")
    antigo = os.path.join(roll, "rollout-old.jsonl")
    for caminho, eventos in (
        (ativo, [
            {"timestamp": agora - 12, "type": "turn_context", "payload": {
                "model": "gpt-5.6-luna", "cwd": proj}},
            {"timestamp": agora - 1, "type": "event_msg", "payload": {
                "type": "token_count", "info": {"total_token_usage": {
                    "total_tokens": 1234}}}},
        ]),
        (concluido, [
            {"timestamp": agora - 8, "type": "turn_context", "payload": {
                "model": "gpt-5.6-luna", "cwd": proj}},
            {"timestamp": agora - 1, "type": "event_msg", "payload": {
                "type": "task_complete"}},
        ]),
        (antigo, [
            {"timestamp": agora - 120, "type": "turn_context", "payload": {
                "model": "gpt-5.6-luna", "cwd": proj}},
        ]),
    ):
        with open(caminho, "w", encoding="utf-8") as fh:
            for evento in eventos:
                fh.write(json.dumps(evento) + "\n")
    os.utime(antigo, (agora - 100, agora - 100))
    jobs = jobs_ativos()
    por_arquivo = {job["arquivo"]: job for job in jobs}
    assert "rollout-active.jsonl" in por_arquivo, jobs
    assert "rollout-done.jsonl" not in por_arquivo, jobs
    assert "rollout-old.jsonl" not in por_arquivo, jobs
    job = por_arquivo["rollout-active.jsonl"]
    assert job["modelo"] == "gpt-5.6-luna" and job["cwd"] == proj, job
    assert job["projeto"] == "algum-projeto" and job["tokens"] == 1234, job
    assert 10 <= job["segundos"] <= 20, job

    # o contrato que importa: --line NAO pode gravar o marco
    global MARK
    MARK = os.path.join(d, "mark.json")
    line({"transcript_path": transcript})
    assert not os.path.exists(MARK), "--line gravou o marco e zerou o delta"

    # --json e leitura: um app consulta varias vezes sem mover o marco.
    import subprocess
    with open(MARK, "w", encoding="utf-8") as fh:
        fh.write("marco original")
    marca_antes = (open(MARK, "rb").read(), os.stat(MARK).st_mtime_ns)
    ambiente = os.environ.copy()
    ambiente["CODEX_HOME"] = d
    ambiente["CLAUDE_CONFIG_DIR"] = d
    processo = subprocess.run([sys.executable, __file__, "--json"],
                              env=ambiente, capture_output=True, text=True,
                              check=True)
    json_saida = json.loads(processo.stdout)
    assert set(json_saida) == {"codex", "claude", "free_tier", "jobs", "ts"}, json_saida
    marca_depois = (open(MARK, "rb").read(), os.stat(MARK).st_mtime_ns)
    assert marca_depois == marca_antes, "--json alterou o marco"

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
    elif "--json" in sys.argv:
        now = snapshot()
        now["jobs"] = jobs_ativos()
        now["ts"] = time.time()
        print(json.dumps(now))
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
