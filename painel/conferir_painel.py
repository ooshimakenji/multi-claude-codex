# -*- coding: utf-8 -*-
"""Confere o que o painel DEVE desenhar, lendo as mesmas fontes que ele le.

Uso: python conferir_painel.py
Nao chama o painel - so mostra o que as fontes oferecem, para comparar com a tela.
"""
import json
import subprocess
import pathlib

CSWAP = pathlib.Path.home() / ".local" / "bin" / "cswap.exe"
CACHE = pathlib.Path.home() / ".claude" / "poke-cache"


def idade(seg):
    if seg is None:
        return ""
    m = seg / 60
    if m < 60:
        return f" ·{int(m)}m"
    h = m / 60
    return f" ·{int(h)}h" if h < 24 else f" ·{int(h/24)}d"


def main():
    mapa = json.loads((CACHE / "mapa.json").read_text(encoding="utf-8"))
    por_chave = {k: v for k, v in mapa.items()}

    d = json.loads(subprocess.run([str(CSWAP), "list", "--json"],
                                  capture_output=True, text=True, timeout=90).stdout)
    print("=== CLAUDE (o que o painel deve desenhar) ===")
    for a in d["accounts"]:
        email = a.get("email", "")
        u, cache = a.get("usage"), False
        if not u:
            u, cache = a.get("lastGoodUsage"), True
        marca = idade(a.get("lastGoodAgeSeconds")) if cache else ""
        fh = (u or {}).get("fiveHour", {}).get("pct")
        sd = (u or {}).get("sevenDay", {}).get("pct")
        info = por_chave.get(f"claude:{email.lower()}")
        sprite = info["nome"] if info else "SEM SPRITE"
        flag = " [relogar]" if a.get("usageStatus") == "relogin_required" else ""
        c5 = f"{fh:.0f}%{marca}" if fh is not None else "—"
        c7 = f"{sd:.0f}%{marca}" if sd is not None else "—"
        print(f"  #{a['number']} {email[:30]:32} 5h={c5:12} 7d={c7:12} sprite={sprite}{flag}")

    print("\n=== CODEX ===")
    for chave, info in mapa.items():
        if chave.startswith("codex:"):
            print(f"  {info['email'][:30]:32} sprite={info['nome']}")

    faltando = [i["nome"] for i in mapa.values()
                if not (CACHE / f"{i['numero']}-frente.png").exists()]
    print(f"\nPNGs faltando: {faltando or 'nenhum'}")
    print(f"Total de contas mapeadas: {len(mapa)}")


if __name__ == "__main__":
    main()
