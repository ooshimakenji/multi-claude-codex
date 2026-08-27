#!/usr/bin/env python3
"""Populate the local Pokemon sprite cache used by the offline panel."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

POKE_API = "https://pokeapi.co/api/v2/pokemon/{number}"
SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{path}.png"


def species_number(email):
    digest = hashlib.sha256(email.lower().encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % 1025 + 1


def cache_dir():
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return Path(root).expanduser() / "poke-cache"


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def download(url):
    request = Request(url, headers={"User-Agent": "multi-claude-codex/poke-cache"})
    with urlopen(request, timeout=20) as response:
        return response.read()


def run_cswap():
    command = shutil.which("cswap") or shutil.which("cswap.exe")
    if not command:
        fallback = Path.home() / ".local" / "bin" / "cswap.exe"
        command = str(fallback) if fallback.is_file() else None
    if not command:
        raise RuntimeError("cswap nao encontrado no PATH")
    result = subprocess.run([command, "list", "--json"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "cswap list falhou")
    return json.loads(result.stdout).get("accounts", [])


def read_map(path):
    if not path.is_file():
        return {}, True
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return (value if isinstance(value, dict) else {}), isinstance(value, dict)
    except (OSError, ValueError) as error:
        print(f"aviso: mapa.json invalido: {error}", file=sys.stderr)
        return {}, False


def pokemon_name(number):
    raw = download(POKE_API.format(number=number))
    return json.loads(raw.decode("utf-8"))["name"]


def populate():
    accounts = run_cswap()
    target = cache_dir()
    mapping, map_ok = read_map(target / "mapa.json")
    known_names = {
        item.get("numero"): item.get("nome")
        for item in mapping.values()
        if isinstance(item, dict) and item.get("numero") and item.get("nome")
    }
    numbers = {}
    for account in accounts:
        email = account.get("email") if isinstance(account, dict) else None
        if isinstance(email, str) and email:
            numbers[email] = species_number(email)

    for number in sorted(set(numbers.values())):
        for side, path in (("frente", str(number)), ("costas", f"back/{number}")):
            destination = target / f"{number}-{side}.png"
            if destination.is_file():
                continue
            try:
                atomic_write(destination, download(SPRITE_URL.format(path=path)))
            except Exception as error:
                print(f"aviso: sprite {number} {side}: {error}", file=sys.stderr)
        if number not in known_names:
            try:
                known_names[number] = pokemon_name(number)
            except Exception as error:
                print(f"aviso: nome Pokemon {number}: {error}", file=sys.stderr)

    if map_ok:
        for email, number in numbers.items():
            name = known_names.get(number)
            if name:
                mapping[email] = {"numero": number, "nome": name}
        try:
            atomic_write(target / "mapa.json", json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        except OSError as error:
            print(f"aviso: mapa.json: {error}", file=sys.stderr)


def selftest():
    email = "Trainer@Example.COM"
    assert species_number(email) == species_number(email)
    assert species_number(email) == species_number(email.lower())
    number = species_number(email)
    assert 1 <= number <= 1025
    mapping = {email: {"numero": number, "nome": "pikachu"}}
    assert mapping[email]["numero"] == number and mapping[email]["nome"] == "pikachu"
    print("sprites.py selftest: ok")


def clean_orphans():
    target = cache_dir()
    mapping, map_ok = read_map(target / "mapa.json")
    if not map_ok:
        print("sprites.py limpar: mapa.json invalido; 0 PNG removidos")
        return

    referenced = set()
    for item in mapping.values():
        if not isinstance(item, dict):
            continue
        number = item.get("numero")
        if isinstance(number, int) and 1 <= number <= 1025:
            referenced.update((f"{number}-frente.png", f"{number}-costas.png"))

    removed = 0
    if target.is_dir():
        for path in target.iterdir():
            if path.is_file() and path.suffix.lower() == ".png" and path.name not in referenced:
                try:
                    path.unlink()
                except OSError as error:
                    print(f"aviso: nao foi possivel remover {path.name}: {error}", file=sys.stderr)
                else:
                    removed += 1
    print(f"sprites.py limpar: {removed} PNG(s) removidos")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--limpar", action="store_true")
    args = parser.parse_args()
    try:
        selftest() if args.selftest else clean_orphans() if args.limpar else populate()
    except Exception as error:
        print(f"erro: {error}", file=sys.stderr)
        raise SystemExit(1)
