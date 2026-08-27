#!/usr/bin/env python3
"""Populate the local Pokemon sprite cache used by the offline panel."""

import argparse
import base64
import binascii
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

POKE_API = "https://pokeapi.co/api/v2/pokemon/{number}"
SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{path}.png"
MIN_SPECIES = 1
MAX_SPECIES = 1025
SPECIES_NUMBERS = tuple(range(MIN_SPECIES, MAX_SPECIES + 1))


def random_species(available):
    """Choose a species with system randomness, independently of the e-mail."""
    choices = tuple(sorted(available))
    return choices[secrets.randbelow(len(choices))]


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
        raise RuntimeError("cswap list falhou")
    return json.loads(result.stdout).get("accounts", [])


def claude_accounts():
    try:
        accounts = run_cswap()
    except Exception as error:
        print(f"aviso: contas Claude: {error}", file=sys.stderr)
        return []
    result = []
    for account in accounts:
        email = account.get("email") if isinstance(account, dict) else None
        if isinstance(email, str) and email.strip():
            result.append({"email": email.strip().lower(), "provedor": "claude"})
    return result


def decode_jwt_payload(token):
    if not isinstance(token, str):
        raise ValueError("id_token ausente")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("id_token nao e JWT")
    encoded = parts[1]
    encoded += "=" * (-len(encoded) % 4)
    try:
        payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
        value = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error) as error:
        raise ValueError("payload JWT invalido") from error
    if not isinstance(value, dict):
        raise ValueError("payload JWT nao e objeto")
    return value


def codex_profile_dirs():
    home = Path.home()
    configured = Path(os.environ["CODEX_HOME"]).expanduser() if os.environ.get("CODEX_HOME") else home / ".codex"
    candidates = [home / ".codex", configured]
    candidates.extend(sorted(home.glob(".codex-*"), key=lambda path: path.name.lower()))
    result = []
    seen = set()
    for directory in candidates:
        resolved = str(directory)
        if resolved not in seen and directory.is_dir():
            seen.add(resolved)
            result.append(directory)
    return result


def codex_accounts():
    result = []
    for directory in codex_profile_dirs():
        path = directory / "auth.json"
        try:
            auth = json.loads(path.read_text(encoding="utf-8"))
            token = (auth.get("tokens") or {}).get("id_token")
            claims = decode_jwt_payload(token)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            print(f"aviso: Codex {directory.name}: JWT nao decodificavel; conta ignorada", file=sys.stderr)
            continue

        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            print(f"aviso: Codex {directory.name}: JWT sem e-mail; conta ignorada", file=sys.stderr)
            continue
        auth_claims = claims.get("https://api.openai.com/auth")
        plan = auth_claims.get("chatgpt_plan_type") if isinstance(auth_claims, dict) else None
        account = {"email": email.strip().lower(), "provedor": "codex"}
        if isinstance(plan, str) and plan.strip():
            account["plano"] = plan.strip()
        result.append(account)
    return result


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


def qualified_key(provider, email):
    return f"{provider}:{email}"


def map_identity(map_key, item):
    """Return the provider/e-mail identity, including for legacy raw keys."""
    source_key = str(map_key)
    key_provider, separator, key_email = source_key.partition(":")
    provider = item.get("provedor")
    if provider not in ("claude", "codex"):
        provider = key_provider if key_provider in ("claude", "codex") else "claude"

    email = item.get("email")
    if not isinstance(email, str) or not email.strip():
        email = key_email if separator and key_provider in ("claude", "codex") else source_key
    return provider, email.strip().lower()


def assign_numbers(accounts, mapping, reset=False):
    """Migrate keys, retain assignments, then allocate new e-mails randomly."""
    retained = {}
    owners = {}
    identity_keys = {}
    candidates = []
    source_mapping = {} if reset else mapping
    for source_key in sorted(source_mapping, key=lambda value: str(value).lower()):
        item = source_mapping[source_key]
        if not isinstance(item, dict):
            continue
        number = item.get("numero")
        if not isinstance(number, int) or not MIN_SPECIES <= number <= MAX_SPECIES:
            continue

        normalized = dict(item)
        provider, email = map_identity(source_key, normalized)
        identity = (provider, email)
        normalized["email"] = email
        normalized["provedor"] = provider
        map_key = qualified_key(provider, email)
        # If both forms exist, preserve the already-migrated entry first.  The
        # ordering also makes conflicting legacy duplicates deterministic.
        candidates.append((source_key == map_key, map_key, source_key, number, normalized, identity))

    for is_qualified, map_key, _source_key, number, normalized, identity in sorted(
        candidates, key=lambda candidate: (not candidate[0], candidate[1].lower(), str(candidate[2]).lower())
    ):
        if map_key in retained or number in owners:
            continue
        retained[map_key] = normalized
        identity_keys[identity] = map_key
        owners[number] = identity

    by_identity = {}
    for account in accounts:
        provider = account.get("provedor")
        email = account.get("email")
        if provider not in ("claude", "codex") or not isinstance(email, str) or not email.strip():
            continue
        email = email.strip().lower()
        account["provedor"] = provider
        account["email"] = email
        by_identity[(provider, email)] = account

    available = set(SPECIES_NUMBERS) - set(owners)
    reuse = []
    for identity in sorted(by_identity):
        provider, email = identity
        account = by_identity[identity]
        map_key = identity_keys.get(identity)
        item = retained.get(map_key) if map_key is not None else None
        if item is None:
            if available:
                number = random_species(available)
                available.remove(number)
            else:
                number = random_species(SPECIES_NUMBERS)
                reuse.append((email, number))
            map_key = qualified_key(provider, email)
            item = {"numero": number, "email": email}
            retained[map_key] = item
            identity_keys[identity] = map_key
            owners.setdefault(number, identity)
        account["map_key"] = map_key
        item["email"] = email
        item["provedor"] = provider
        if account.get("plano"):
            item["plano"] = account["plano"]
        else:
            item.pop("plano", None)
    return retained, reuse


def populate(reset=False):
    accounts = claude_accounts() + codex_accounts()
    target = cache_dir()
    mapping, map_ok = read_map(target / "mapa.json")
    mapping, reuse = assign_numbers(accounts, mapping, reset=reset)
    if reuse:
        for email, number in reuse:
            print(f"aviso: reuso do Pokemon {number} para {email}", file=sys.stderr)

    numbers = {
        item["numero"]
        for item in mapping.values()
        if isinstance(item, dict)
        and isinstance(item.get("numero"), int)
        and MIN_SPECIES <= item["numero"] <= MAX_SPECIES
    }
    for number in sorted(numbers):
        for side, path in (("frente", str(number)), ("costas", f"back/{number}")):
            destination = target / f"{number}-{side}.png"
            if destination.is_file():
                continue
            try:
                atomic_write(destination, download(SPRITE_URL.format(path=path)))
            except Exception as error:
                print(f"aviso: sprite {number} {side}: {error}", file=sys.stderr)
        for item in mapping.values():
            if not isinstance(item, dict) or item.get("numero") != number or item.get("nome"):
                continue
            try:
                item["nome"] = pokemon_name(number)
            except Exception as error:
                print(f"aviso: nome Pokemon {number}: {error}", file=sys.stderr)

    if map_ok or reset:
        try:
            map_path = target / "mapa.json"
            map_data = json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            if not map_path.is_file() or map_path.read_bytes() != map_data:
                atomic_write(map_path, map_data)
        except OSError as error:
            print(f"aviso: mapa.json: {error}", file=sys.stderr)

    if reset:
        clean_orphans()

    print("mapa final:")
    for map_key in sorted(mapping, key=str.lower):
        item = mapping[map_key]
        print(f"{map_key} -> {item.get('nome', 'ausente')} ({item.get('numero', 'ausente')})")


def selftest():
    old = {"old@example.com": {"numero": 503, "nome": "samurott"}}
    allocated, reuse = assign_numbers(
        [{"email": "old@example.com", "provedor": "claude"},
         {"email": "new@example.com", "provedor": "codex", "plano": "plus"}], old)
    assert allocated["claude:old@example.com"]["numero"] == 503
    assert 1 <= allocated["codex:new@example.com"]["numero"] <= MAX_SPECIES
    assert allocated["codex:new@example.com"]["numero"] != 503
    assert allocated["codex:new@example.com"]["provedor"] == "codex"
    assert not reuse
    allocated_again, reuse_again = assign_numbers(
        [{"email": "old@example.com", "provedor": "claude"},
         {"email": "new@example.com", "provedor": "codex", "plano": "plus"}], allocated)
    assert allocated_again == allocated
    assert not reuse_again
    reset, reset_reuse = assign_numbers(
        [{"email": "old@example.com", "provedor": "claude"},
         {"email": "new@example.com", "provedor": "codex", "plano": "plus"}], old, reset=True)
    assert set(reset) == {"claude:old@example.com", "codex:new@example.com"}
    assert len({item["numero"] for item in reset.values()}) == 2
    assert not reset_reuse
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
        if isinstance(number, int) and MIN_SPECIES <= number <= MAX_SPECIES:
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
    parser.add_argument("--resetar", action="store_true")
    args = parser.parse_args()
    try:
        selftest() if args.selftest else clean_orphans() if args.limpar else populate(reset=args.resetar)
    except Exception as error:
        print(f"erro: {error}", file=sys.stderr)
        raise SystemExit(1)
