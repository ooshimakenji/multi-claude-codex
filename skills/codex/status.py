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
      python status.py --jobs      lista jobs ativos, sem varrer rollouts
      python status.py --json      snapshot para consumidores de maquina
      python status.py --reset     esquece o marco anterior
      python status.py --selftest  roda os asserts
"""
import base64
import binascii
import glob
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
CLAUDE_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
MARK = os.path.join(CLAUDE_HOME, "codex-status-mark.json")
CACHE_VERSION = 3
_CACHE = None
_CACHE_PATH = None
_CACHE_DIRTY = False

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


def _cache_path():
    return os.path.join(os.path.dirname(MARK), "codex-status-cache.json")


def _cache():
    global _CACHE, _CACHE_PATH, _CACHE_DIRTY
    caminho = _cache_path()
    if _CACHE_PATH == caminho and _CACHE is not None:
        return _CACHE
    try:
        with open(caminho, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError, TypeError):
        cache = {}
    if not isinstance(cache, dict) or cache.get("version") != CACHE_VERSION:
        cache = {}
    cache.setdefault("rollouts", {})
    cache.setdefault("claude", {})
    cache["version"] = CACHE_VERSION
    _CACHE_PATH = caminho
    _CACHE = cache
    _CACHE_DIRTY = False
    return cache


def _save_cache():
    global _CACHE_DIRTY
    if not _CACHE_DIRTY:
        return
    cache = _cache()
    caminho = _cache_path()
    temporario = caminho + ".tmp"
    try:
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(temporario, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, separators=(",", ":"))
        os.replace(temporario, caminho)
        _CACHE_DIRTY = False
    except OSError:
        try:
            os.remove(temporario)
        except OSError:
            pass


def _arquivo_assinatura(path):
    stat = os.stat(path)
    with open(path, "rb") as fh:
        prefixo = hashlib.sha256(fh.read(256)).hexdigest()
    return stat.st_size, stat.st_mtime_ns, prefixo


def _rollout_cached(path):
    """Extrai uma vez o acumulado e os metadados do rollout imutavel."""
    try:
        tamanho, mtime, prefixo = _arquivo_assinatura(path)
    except OSError:
        raise
    chave = os.path.abspath(path)
    registro = _cache()["rollouts"].get(chave)
    assinatura = (tamanho, mtime, prefixo)
    if isinstance(registro, dict) and tuple(registro.get("assinatura", ())) == assinatura:
        return registro

    modelo = None
    quotas = None
    source = None
    linhas = tail(path, 65536)
    uso = _ultimo_token_count(linhas)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            cabecalho = json.loads(fh.readline())
    except (OSError, ValueError, TypeError):
        cabecalho = None
    if (isinstance(cabecalho, dict) and
            cabecalho.get("type") == "session_meta"):
        payload = cabecalho.get("payload") or {}
        if isinstance(payload, dict) and "source" in payload:
            source = payload.get("source")
    for linha in linhas:
        try:
            ev = json.loads(linha)
        except ValueError:
            continue
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if ev.get("type") == "session_meta" and "source" in payload:
            source = payload.get("source")
        if ev.get("type") == "turn_context" and payload.get("model"):
            sandbox = payload.get("sandbox_policy") or {}
            modelo = [payload["model"],
                      payload.get("effort") or payload.get("reasoning_effort"),
                      sandbox.get("type") if isinstance(sandbox, dict) else sandbox]
        if payload.get("type") == "token_count":
            rate_limits = payload.get("rate_limits") or {}
            janelas = [{"janela": janela.get("window_minutes"),
                        "pct": janela.get("used_percent"),
                        "reseta_em": janela.get("resets_at")}
                       for janela in (rate_limits.get("primary"),
                                      rate_limits.get("secondary"))
                       if isinstance(janela, dict) and "used_percent" in janela]
            if janelas:
                quotas = janelas
    registro = {"assinatura": list(assinatura),
                "tokens": (uso or {}).get("total_tokens", 0),
                "modelo": modelo,
                "quotas": quotas,
                "source": source}
    _cache()["rollouts"][chave] = registro
    global _CACHE_DIRTY
    _CACHE_DIRTY = True
    return registro


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


def _rollout_guardian(registro):
    source = registro.get("source") if isinstance(registro, dict) else None
    return isinstance(source, dict) and "subagent" in source


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
            uso = _rollout_cached(arq)
        except OSError:
            continue
        total += uso.get("tokens", 0)
    _save_cache()
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
            uso = _rollout_cached(arq)
        except OSError:
            continue
        total += uso.get("tokens", 0)
    _save_cache()
    return total


def _codex_side_profile(home):
    """Tokens = soma de todos os rollouts. Modelo/quota = rollout mais recente que
    os tenha: um job curto pode nao gravar turn_context, e ficar sem modelo no
    display so porque foi o ultimo a rodar seria pior que olhar um pouco atras."""
    arqs = sorted(_rollouts(home), key=os.path.getmtime, reverse=True)
    validos = []
    for arq in arqs:
        try:
            if not _rollout_guardian(_rollout_cached(arq)):
                validos.append(arq)
        except OSError:
            continue
    out = {"arquivo": os.path.basename(validos[0]) if validos else None,
           "tokens": codex_tokens_total(home)}
    if validos:
        out["idade_s"] = max(0, time.time() - os.path.getmtime(validos[0]))
    for arq in validos[:10]:
        try:
            registro = _rollout_cached(arq)
        except OSError:
            continue
        modelo = registro.get("modelo")
        quotas = registro.get("quotas")
        if modelo and "modelo" not in out:
            out["modelo"], out["effort"], out["sandbox"] = modelo
        if quotas and "quotas" not in out:
            out["quotas"] = quotas
            out["quota_pct"] = quotas[0]["pct"]  # delta acompanha a mais curta
            out["janela_tokens"] = codex_janela(quotas[0]["janela"], home)
        if "modelo" in out and "quotas" in out:
            break
    _save_cache()
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
    encontrados = []
    hoje = datetime.now().date()

    # O painel consulta isto a cada poucos segundos: nao percorra o historico.
    # Candidatos vem SO das pastas de hoje/ontem, uma vez por perfil Codex
    # (~/.codex, ~/.codex-b, ...) -- sem isso um "codex exec" rodando num
    # perfil que nao e o CODEX_HOME do painel fica invisivel. Nao ha varredura
    # de mtime fora desse padrao (custaria stat() em centenas de arquivos a
    # cada poll); mtime so decide a ORDEM de leitura e serve de ultimo recurso
    # para "inicio" quando o rollout nunca chega a gravar um timestamp -- quem
    # decide ativo/inativo e o timestamp do ULTIMO EVENTO dentro do arquivo
    # (idade/pendente abaixo), porque o NTFS pode nao atualizar mtime enquanto
    # o arquivo esta aberto.
    candidatos = {}
    for home in codex_profile_homes():
        sessions = os.path.join(home, "sessions")
        for deslocamento in (0, 1):
            dia = hoje - timedelta(days=deslocamento)
            pasta = os.path.join(sessions, "{:04d}".format(dia.year),
                                 "{:02d}".format(dia.month),
                                 "{:02d}".format(dia.day))
            for arq in glob.glob(os.path.join(pasta, "*.jsonl")):
                candidatos[arq] = home
    recentes = []
    for arq, home in candidatos.items():
        try:
            mtime = os.path.getmtime(arq)
        except OSError:
            continue
        recentes.append((mtime, arq, home))

    for mtime, arq, home in sorted(recentes, reverse=True):
        try:
            linhas = tail(arq, 8192)
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
            ultimo_ts = _epoch(ultimo.get("timestamp"))
            idade = agora - ultimo_ts if ultimo_ts is not None else None
            pendente = (
                ultimo.get("type") == "response_item" and
                isinstance(payload, dict) and
                payload.get("type") in ("function_call", "custom_tool_call",
                                          "local_shell_call"))
            if not ((idade is not None and 0 <= idade <= 90) or
                    (pendente and idade is not None and 0 <= idade < 30 * 60)):
                continue
            try:
                if _rollout_guardian(_rollout_cached(arq)):
                    continue
            except OSError:
                continue
            # So agora vale a pena buscar cabecalho, contexto e tokens.
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

        uso = _ultimo_token_count(linhas)
        total = uso.get("total_tokens", 0) if isinstance(uso, dict) else 0
        cwd = contexto.get("cwd") if isinstance(contexto, dict) else None
        inicio = _epoch(primeiro.get("timestamp")) if isinstance(primeiro, dict) else None
        encontrados.append({
            "tipo": "codex",
            "modelo": contexto.get("model") if isinstance(contexto, dict) else None,
            "cwd": cwd,
            "projeto": os.path.basename(os.path.normpath(cwd)) if cwd else None,
            "tokens": total,
            "segundos": max(0, agora - (inicio if inicio is not None else mtime)),
            "arquivo": os.path.basename(arq),
            "perfil": os.path.basename(home),
        })

    subagentes = glob.glob(os.path.join(
        CLAUDE_HOME, "projects", "*", "*", "subagents", "agent-*.jsonl"))
    recentes = []
    for arq in subagentes:
        try:
            mtime = os.path.getmtime(arq)
        except OSError:
            continue
        if mtime < agora - 30 * 60:
            continue
        recentes.append((mtime, arq))

    # mtime so filtra candidatos (uma tool longa grava pouco por ate ~30min);
    # quem decide ativo e o timestamp do ULTIMO EVENTO, igual ao Codex acima.
    # _subagente_terminou pega marcador explicito de fim; sem ele, um bloco
    # pendente (ver abaixo) ainda conta como ativo por ate 30min. So texto
    # sem nenhum sinal pendente, com mais de 90s, e fim de verdade.
    for mtime, arq in sorted(recentes, reverse=True):
        try:
            linhas = tail(arq, 65536)
        except OSError:
            continue
        if _subagente_terminou(linhas):
            continue
        if _subagente_handback_encerrado(linhas):
            continue
        ultimo = ultimo_msg = None
        for linha in reversed(linhas):
            try:
                evento = json.loads(linha)
            except ValueError:
                continue
            if not isinstance(evento, dict):
                continue
            if ultimo is None:
                ultimo = evento
            if ultimo_msg is None and evento.get("type") in ("assistant", "user"):
                ultimo_msg = evento
            if ultimo is not None and ultimo_msg is not None:
                break
        if not ultimo:
            continue
        ultimo_ts = _epoch(ultimo.get("timestamp"))
        idade = agora - ultimo_ts if ultimo_ts is not None else None
        msg = ultimo_msg.get("message") if isinstance(ultimo_msg, dict) else None
        blocos = msg.get("content") if isinstance(msg, dict) else None
        blocos = blocos if isinstance(blocos, list) else []
        # tool_use/tool_result (cliente OU server-side: "server_tool_use",
        # "<nome>_tool_result", por isso substring) e thinking sempre
        # continuam para outro bloco -- so texto sozinho pode ser fim de
        # verdade. stop_reason="tool_use" e reforco (a API para so pra
        # chamar ferramenta); stop_reason None NAO entra aqui -- medido em
        # 241 transcripts encerrados, 27 terminam com texto e stop_reason
        # None sem nunca mais escrever nada, entao None nao prova pendencia.
        tipos = [b.get("type") for b in blocos
                 if isinstance(b, dict) and isinstance(b.get("type"), str)]
        pendente = any("tool_use" in t or "tool_result" in t or "thinking" in t
                       for t in tipos)
        if not pendente and isinstance(ultimo_msg, dict) and ultimo_msg.get("type") == "assistant":
            stop_reason = msg.get("stop_reason") if isinstance(msg, dict) else None
            pendente = stop_reason == "tool_use"
        if not ((idade is not None and 0 <= idade <= 90) or
                (pendente and idade is not None and 0 <= idade < 30 * 60)):
            continue

        try:
            primeiro = None
            for indice, evento in enumerate(_loads(arq)):
                if isinstance(evento, dict):
                    primeiro = evento
                    break
                if indice >= 127:
                    break
        except OSError:
            primeiro = None
        try:
            meta_path = os.path.splitext(arq)[0] + ".meta.json"
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError, TypeError):
            meta = {}

        inicio = _epoch(primeiro.get("timestamp")) if isinstance(primeiro, dict) else None
        if inicio is None:
            try:
                inicio = os.path.getctime(arq)
            except OSError:
                inicio = mtime
        encontrados.append({
            "tipo": "claude",
            "modelo": meta.get("model") if isinstance(meta.get("model"), str) else "—",
            "projeto": (
                meta.get("description") if isinstance(meta.get("description"), str)
                else meta.get("agentType") if isinstance(meta.get("agentType"), str)
                else "—"
            ),
            "segundos": max(0, agora - inicio),
            "arquivo": os.path.basename(arq),
        })
    return encontrados


def _subagente_terminou(linhas):
    """Aceita apenas marcadores de termino explicitos, nao uma mensagem qualquer."""
    for linha in reversed(linhas):
        try:
            evento = json.loads(linha)
        except ValueError:
            continue
        if not isinstance(evento, dict):
            continue
        if evento.get("type") in ("result", "subagent_stop", "task_complete"):
            return True
        payload = evento.get("payload") or {}
        if isinstance(payload, dict) and payload.get("type") in (
                "result", "subagent_stop", "task_complete"):
            return True
    return False


def _subagente_handback_encerrado(linhas):
    """Reconhece a entrega final quando ela fecha o transcrito."""
    eventos = []
    for linha in linhas:
        try:
            evento = json.loads(linha)
        except ValueError:
            continue
        if isinstance(evento, dict):
            eventos.append(evento)
    if not eventos:
        return False

    ultimo_tool_use = None
    for indice, evento in enumerate(eventos):
        mensagem = evento.get("message") or {}
        blocos = mensagem.get("content") if isinstance(mensagem, dict) else None
        if not isinstance(blocos, list):
            continue
        for bloco in blocos:
            if (isinstance(bloco, dict) and bloco.get("type") == "tool_use" and
                    isinstance(bloco.get("name"), str)):
                ultimo_tool_use = (indice, bloco)

    if ultimo_tool_use is None:
        return False
    indice, handback = ultimo_tool_use
    nome = handback["name"]
    if nome != "SubagentHandback" and not (
            nome.startswith("mcp__") and nome.endswith("__SubagentHandback")):
        return False

    if indice == len(eventos) - 1:
        return True

    tool_id = handback.get("id")
    ultimo = eventos[-1]
    mensagem = ultimo.get("message") or {}
    blocos = mensagem.get("content") if isinstance(mensagem, dict) else None
    if not isinstance(blocos, list):
        return False
    for bloco in blocos:
        if not isinstance(bloco, dict) or bloco.get("type") != "tool_result":
            continue
        if tool_id is None or bloco.get("tool_use_id") == tool_id:
            return True
    return False


def claude_side():
    path = newest(os.path.join(CLAUDE_HOME, "projects", "**", "*.jsonl"))
    out = {"arquivo": os.path.basename(path) if path else None, "tokens": 0}
    if not path:
        return out
    try:
        tamanho, mtime, prefixo = _arquivo_assinatura(path)
    except OSError:
        return out
    chave = os.path.abspath(path)
    cache = _cache()["claude"]
    registro = cache.get(chave)
    reiniciar = not isinstance(registro, dict)
    if not reiniciar:
        reiniciar = (tamanho < registro.get("tamanho", 0) or
                     (tamanho == registro.get("tamanho") and
                      mtime != registro.get("mtime")) or
                     prefixo != registro.get("prefixo"))
    inicio = 0 if reiniciar else min(registro.get("offset", 0), tamanho)
    total = 0 if reiniciar else registro.get("tokens", 0)
    if inicio < tamanho:
        with open(path, "rb") as fh:
            fh.seek(inicio)
            dados = fh.read()
        consumido = 0
        for linha in dados.splitlines(keepends=True):
            if not linha.endswith((b"\n", b"\r")):
                break
            consumido += len(linha)
            try:
                ev = json.loads(linha.decode("utf-8", "replace"))
            except ValueError:
                continue
            usage = (ev.get("message") or {}).get("usage")
            if isinstance(usage, dict):
                # cache_read e barato mas nao e gratis: conta tudo que entrou/saiu.
                total += sum(v for v in usage.values() if isinstance(v, int))
        inicio += consumido
    cache[chave] = {"tamanho": tamanho, "mtime": mtime, "prefixo": prefixo,
                    "offset": inicio, "tokens": total}
    global _CACHE_DIRTY
    _CACHE_DIRTY = True
    out["tokens"] = total
    _save_cache()
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
            ativos = len(jobs_ativos())
            jobs = " · codex {} rodando".format(ativos) if ativos else ""
            partes.append("codex " + " ".join(campos) + marcador + jobs)
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
    hoje = datetime.now().date()
    roll = os.path.join(d, "sessions", "{:04d}".format(hoje.year),
                        "{:02d}".format(hoje.month), "{:02d}".format(hoje.day))
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

    global CODEX_HOME, CLAUDE_HOME, MARK
    CODEX_HOME = CLAUDE_HOME = d
    MARK = os.path.join(d, "mark.json")
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
    guardian = os.path.join(roll, "rollout-guardian.jsonl")
    concluido = os.path.join(roll, "rollout-done.jsonl")
    antigo = os.path.join(roll, "rollout-old.jsonl")
    mtime_old = os.path.join(roll, "rollout-old-mtime.jsonl")
    pendente_10 = os.path.join(roll, "rollout-pending-10m.jsonl")
    pendente_40 = os.path.join(roll, "rollout-pending-40m.jsonl")
    stale_5 = os.path.join(roll, "rollout-stale-5m.jsonl")
    for caminho, eventos in (
        (ativo, [
            {"timestamp": agora - 12, "type": "turn_context", "payload": {
                "model": "gpt-5.6-luna", "cwd": proj}},
            {"timestamp": agora - 1, "type": "event_msg", "payload": {
                "type": "token_count", "info": {"total_token_usage": {
                    "total_tokens": 1234}}}},
        ]),
        (guardian, [
            {"timestamp": agora - 2, "type": "session_meta", "payload": {
                "source": {"subagent": {"other": "guardian"}}}},
            {"timestamp": agora - 2, "type": "turn_context", "payload": {
                "model": "codex-auto-review", "cwd": proj}},
            {"timestamp": agora - 1, "type": "event_msg", "payload": {
                "type": "token_count", "info": {"total_token_usage": {
                    "total_tokens": 2345}}}},
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
        (mtime_old, [
            {"timestamp": agora - 1, "type": "turn_context", "payload": {
                "model": "gpt-5.6-luna", "cwd": proj}},
        ]),
        (pendente_10, [
            {"timestamp": agora - 600, "type": "turn_context", "payload": {
                "model": "gpt-5.6-luna", "cwd": proj}},
            {"timestamp": agora - 600, "type": "response_item", "payload": {
                "type": "function_call", "name": "exec"}},
        ]),
        (pendente_40, [
            {"timestamp": agora - 2400, "type": "turn_context", "payload": {
                "model": "gpt-5.6-luna", "cwd": proj}},
            {"timestamp": agora - 2400, "type": "response_item", "payload": {
                "type": "function_call", "name": "exec"}},
        ]),
        (stale_5, [
            {"timestamp": agora - 300, "type": "turn_context", "payload": {
                "model": "gpt-5.6-luna", "cwd": proj}},
            {"timestamp": agora - 300, "type": "response_item", "payload": {
                "type": "reasoning"}},
        ]),
    ):
        with open(caminho, "w", encoding="utf-8") as fh:
            for evento in eventos:
                fh.write(json.dumps(evento) + "\n")
    os.utime(antigo, (agora - 100, agora - 100))
    for caminho in (mtime_old, pendente_10, pendente_40, stale_5):
        os.utime(caminho, (agora - 2 * 3600, agora - 2 * 3600))
    jobs = jobs_ativos()
    por_arquivo = {job["arquivo"]: job for job in jobs}
    assert "rollout-active.jsonl" in por_arquivo, jobs
    assert "rollout-guardian.jsonl" not in por_arquivo, jobs
    assert "rollout-done.jsonl" not in por_arquivo, jobs
    assert "rollout-old.jsonl" not in por_arquivo, jobs
    assert "rollout-old-mtime.jsonl" in por_arquivo, jobs
    assert "rollout-pending-10m.jsonl" in por_arquivo, jobs
    assert "rollout-pending-40m.jsonl" not in por_arquivo, jobs
    assert "rollout-stale-5m.jsonl" not in por_arquivo, jobs
    job = por_arquivo["rollout-active.jsonl"]
    assert job["modelo"] == "gpt-5.6-luna" and job["cwd"] == proj, job
    assert job["projeto"] == "algum-projeto" and job["tokens"] == 1234, job
    assert 10 <= job["segundos"] <= 20, job
    side = codex_side(contas=False)
    assert side["modelo"] == "gpt-5.6-luna", side
    subagents = os.path.join(d, "projects", "claude-projeto", "sessao", "subagents")
    os.makedirs(subagents)
    claude_active = os.path.join(subagents, "agent-active.jsonl")
    claude_stopped = os.path.join(subagents, "agent-stopped.jsonl")
    for caminho, timestamp in ((claude_active, agora - 6), (claude_stopped, agora - 120)):
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": timestamp, "type": "user"}) + "\n")
    with open(os.path.splitext(claude_active)[0] + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump({"agentType": "general-purpose", "description": "confere asfalto", "model": "opus"}, fh)
    with open(os.path.splitext(claude_stopped)[0] + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump({"agentType": "Explore", "description": "parado", "model": "sonnet"}, fh)
    os.utime(claude_stopped, (agora - 120, agora - 120))
    jobs = jobs_ativos()
    por_arquivo = {job["arquivo"]: job for job in jobs}
    assert por_arquivo["agent-active.jsonl"]["tipo"] == "claude", jobs
    assert por_arquivo["agent-active.jsonl"]["modelo"] == "opus", jobs
    assert por_arquivo["agent-active.jsonl"]["projeto"] == "confere asfalto", jobs
    assert "agent-stopped.jsonl" not in por_arquivo, jobs
    assert all("tipo" in job for job in jobs), jobs

    # Handback concluido encerra o subagente; atividade posterior reabre-o.
    handback = os.path.join(subagents, "agent-handback.jsonl")
    tool_recente = os.path.join(subagents, "agent-tool-recente.jsonl")
    retomado = os.path.join(subagents, "agent-retomado.jsonl")
    agora_h = time.time()
    transcritos_handback = {
        handback: [
            {"timestamp": agora_h - 20, "type": "assistant", "message": {
                "content": [{"type": "tool_use", "name": "SubagentHandback",
                              "id": "handback-1"}]}},
            {"timestamp": agora_h - 19, "type": "user", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "handback-1",
                              "content": "entregue"}]}}],
        tool_recente: [
            {"timestamp": agora_h - 20, "type": "assistant", "message": {
                "content": [{"type": "tool_use", "name": "Bash"}]} }],
        retomado: [
            {"timestamp": agora_h - 20, "type": "assistant", "message": {
                "content": [{"type": "tool_use", "name": "mcp__hermes__SubagentHandback",
                              "id": "handback-2"}]}},
            {"timestamp": agora_h - 19, "type": "user", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "handback-2",
                              "content": "entregue"}]}},
            {"timestamp": agora_h - 2, "type": "assistant", "message": {
                "content": [{"type": "tool_use", "name": "SendMessage"}]}}],
    }
    for caminho, eventos in transcritos_handback.items():
        with open(caminho, "w", encoding="utf-8") as fh:
            for evento in eventos:
                fh.write(json.dumps(evento) + "\n")
    jobs = jobs_ativos()
    por_arquivo = {job["arquivo"]: job for job in jobs}
    assert "agent-handback.jsonl" not in por_arquivo, jobs
    assert "agent-tool-recente.jsonl" in por_arquivo, jobs
    assert "agent-retomado.jsonl" in por_arquivo, jobs

    # P2 (bug real, medido em transcript ao vivo): gap de tool call longa
    # (bash, build, thinking estendido) nao pode derrubar o job do painel --
    # so o EVENTO final decide, nao o corte fixo de 90s no candidato.
    tool_use = os.path.join(subagents, "agent-tooluse-3min.jsonl")
    tool_result = os.path.join(subagents, "agent-toolresult-2min.jsonl")
    texto_so = os.path.join(subagents, "agent-textonly-3min.jsonl")
    tool_use_40 = os.path.join(subagents, "agent-tooluse-40min.jsonl")
    qualquer_30s = os.path.join(subagents, "agent-any-30s.jsonl")
    gerando_3min = os.path.join(subagents, "agent-gerando-3min.jsonl")
    attach_depois = os.path.join(subagents, "agent-attach-depois.jsonl")
    server_tool = os.path.join(subagents, "agent-server-tool.jsonl")
    agora_p = time.time()
    for caminho, eventos in (
        (tool_use, [{"timestamp": agora_p - 180, "type": "assistant",
                     "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}]),
        (tool_result, [{"timestamp": agora_p - 120, "type": "user",
                        "message": {"content": [{"type": "tool_result", "content": "ok"}]}}]),
        (texto_so, [{"timestamp": agora_p - 180, "type": "assistant",
                     "message": {"content": [{"type": "text", "text": "pronto"}]}}]),
        (tool_use_40, [{"timestamp": agora_p - 2400, "type": "assistant",
                        "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}]),
        (qualquer_30s, [{"timestamp": agora_p - 30, "type": "attachment"}]),
        # thinking nunca e o ultimo bloco de um turno -- cobre o gap de
        # "thinking estendido" citado no bug, que nao tem tool_use/tool_result.
        (gerando_3min, [{"timestamp": agora_p - 180, "type": "assistant",
                         "message": {"content": [{"type": "thinking", "thinking": "..."}]}}]),
        # tool_result seguido de attachment (o proprio harness grava um logo
        # apos) -- o ULTIMO evento e o attachment, tem que andar pra tras.
        (attach_depois, [
            {"timestamp": agora_p - 120, "type": "user",
             "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            {"timestamp": agora_p - 119, "type": "attachment"},
        ]),
        # ferramenta server-side (advisor, ...): tudo dentro de mensagens
        # assistant, sem par user/tool_result.
        (server_tool, [{"timestamp": agora_p - 150, "type": "assistant",
                        "message": {"content": [{"type": "server_tool_use",
                                                  "name": "advisor"}]}}]),
    ):
        with open(caminho, "w", encoding="utf-8") as fh:
            for evento in eventos:
                fh.write(json.dumps(evento) + "\n")
    # grava recente (entra como candidato) com evento antigo dentro -- e o
    # corte de 30min pelo EVENTO, nao a selecao por mtime, que tem que excluir.
    os.utime(tool_use_40, (agora_p - 60, agora_p - 60))

    jobs = jobs_ativos()
    por_arquivo = {job["arquivo"]: job for job in jobs}
    assert "agent-tooluse-3min.jsonl" in por_arquivo, jobs
    assert "agent-toolresult-2min.jsonl" in por_arquivo, jobs
    assert "agent-textonly-3min.jsonl" not in por_arquivo, jobs
    assert "agent-tooluse-40min.jsonl" not in por_arquivo, jobs
    assert "agent-any-30s.jsonl" in por_arquivo, jobs
    assert "agent-gerando-3min.jsonl" in por_arquivo, jobs
    assert "agent-attach-depois.jsonl" in por_arquivo, jobs
    assert "agent-server-tool.jsonl" in por_arquivo, jobs

    # o contrato que importa: --line NAO pode gravar o marco
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

    # P1 (bug real: so um perfil Codex era varrido): rollout que existe SO num
    # segundo perfil (~/.codex-b) tem que aparecer, com o "perfil" certo.
    familia = tempfile.mkdtemp()
    perfil_a = os.path.join(familia, ".codex")
    perfil_b = os.path.join(familia, ".codex-b")
    pasta_b = os.path.join(perfil_b, "sessions", "{:04d}".format(hoje.year),
                           "{:02d}".format(hoje.month), "{:02d}".format(hoje.day))
    os.makedirs(perfil_a)
    os.makedirs(pasta_b)
    agora_b = time.time()
    with open(os.path.join(pasta_b, "rollout-b.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": agora_b - 5, "type": "turn_context", "payload": {
            "model": "gpt-5.6-sol", "cwd": proj}}) + "\n")
        fh.write(json.dumps({"timestamp": agora_b - 1, "type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": {"total_tokens": 42}}}}) + "\n")

    global HOME
    home_original, codex_home_original = HOME, CODEX_HOME
    HOME, CODEX_HOME = familia, perfil_a
    try:
        perfis_achados = {os.path.normcase(p) for p in codex_profile_homes()}
        assert perfis_achados == {os.path.normcase(perfil_a),
                                   os.path.normcase(perfil_b)}, perfis_achados
        jobs_b = jobs_ativos()
    finally:
        HOME, CODEX_HOME = home_original, codex_home_original

    por_arquivo_b = {job["arquivo"]: job for job in jobs_b}
    assert "rollout-b.jsonl" in por_arquivo_b, jobs_b
    assert por_arquivo_b["rollout-b.jsonl"]["perfil"] == ".codex-b", jobs_b

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
    elif "--jobs" in sys.argv:
        print(json.dumps({"jobs": jobs_ativos()}))
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
