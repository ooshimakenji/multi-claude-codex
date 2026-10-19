#!/usr/bin/env python3
"""Populate the local Pokemon sprite cache used by the offline panel."""

import argparse
import base64
import binascii
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
import zlib

POKE_API = "https://pokeapi.co/api/v2/pokemon/{number}"
SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{path}.png"
MIN_SPECIES = 1
MAX_SPECIES = 151  # so 1a geracao
SPECIES_NUMBERS = tuple(range(MIN_SPECIES, MAX_SPECIES + 1))
SELVAGENS = (16, 19, 21, 10, 13, 41, 74, 129, 23, 27, 46, 48, 50, 52, 54, 56, 72, 77, 81, 100)
AGY_ACCOUNT = {"email": "antigravity", "provedor": "agy"}
AGY_NUMBER = 92
TRAINER_URLS = (
    "https://play.pokemonshowdown.com/sprites/trainers/red.png",
    "https://play.pokemonshowdown.com/sprites/trainers/red-gen1.png",
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_COLOR = 0x01000000
TERMINAL_LIMITS = {"p": 14, "g": 24}
SPRITE_NAME = re.compile(r"^(\d+)-(frente|costas)\.png$", re.IGNORECASE)


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


def _paeth(left, above, upper_left):
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def decode_png(data):
    """Decode the supported non-interlaced 8-bit PNG color types."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("arquivo nao e PNG")
    position = len(PNG_SIGNATURE)
    header = None
    compressed = bytearray()
    palette = None
    transparency = None
    while position < len(data):
        if position + 12 > len(data):
            raise ValueError("PNG truncado")
        length = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4:position + 8]
        end = position + 12 + length
        if end > len(data):
            raise ValueError("chunk PNG truncado")
        payload = data[position + 8:position + 8 + length]
        checksum = struct.unpack_from(">I", data, position + 8 + length)[0]
        if (binascii.crc32(kind + payload) & 0xffffffff) != checksum:
            raise ValueError("CRC PNG invalido")
        if kind == b"IHDR":
            if len(payload) != 13:
                raise ValueError("IHDR PNG invalido")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"PLTE":
            if len(payload) % 3 or not payload:
                raise ValueError("PLTE PNG invalido")
            palette = [tuple(payload[index:index + 3]) for index in range(0, len(payload), 3)]
        elif kind == b"tRNS":
            transparency = payload
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
        position = end
    if header is None:
        raise ValueError("IHDR PNG ausente")
    width, height, bit_depth, color_type, _compression, _filter, interlace = header
    if not width or not height:
        raise ValueError("dimensao PNG invalida")
    if color_type not in (2, 3, 6) or bit_depth not in (8,):
        if color_type != 3 or bit_depth not in (1, 2, 4):
            raise ValueError("PNG requer RGB/RGBA 8 bits ou paleta 1/2/4/8 bits")
    if interlace:
        raise ValueError("PNG Adam7 nao suportado")
    if color_type == 3 and palette is None:
        raise ValueError("PLTE PNG ausente")
    if color_type == 2 and transparency is not None and len(transparency) != 6:
        raise ValueError("tRNS RGB PNG invalido")
    if color_type == 3 and transparency is not None and len(transparency) > len(palette):
        raise ValueError("tRNS paleta PNG invalido")
    channels = {2: 3, 3: 1, 6: 4}[color_type]
    row_size = (width * bit_depth + 7) // 8 if color_type == 3 else width * channels
    raw = zlib.decompress(bytes(compressed))
    expected_size = height * (row_size + 1)
    if len(raw) != expected_size:
        raise ValueError("dados PNG truncados ou excedentes")
    rows = []
    previous = bytearray(row_size)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        filtered = raw[offset + 1:offset + 1 + row_size]
        offset += row_size + 1
        row = bytearray(row_size)
        for index, value in enumerate(filtered):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                result = value
            elif filter_type == 1:
                result = value + left
            elif filter_type == 2:
                result = value + above
            elif filter_type == 3:
                result = value + ((left + above) // 2)
            elif filter_type == 4:
                result = value + _paeth(left, above, upper_left)
            else:
                raise ValueError(f"filtro PNG desconhecido: {filter_type}")
            row[index] = result & 0xff
        rows.append(row)
        previous = row

    pixels = []
    transparent_rgb = struct.unpack(">HHH", transparency) if color_type == 2 and transparency else None
    for row in rows:
        decoded = []
        for index in range(width):
            start = index * channels
            if color_type == 6:
                red, green, blue, alpha = row[start:start + 4]
            elif color_type == 2:
                red, green, blue = row[start:start + 3]
                alpha = 0 if transparent_rgb == (red * 257, green * 257, blue * 257) else 255
            else:
                if bit_depth == 8:
                    palette_index = row[index]
                else:
                    per_byte = 8 // bit_depth
                    packed = row[index // per_byte]
                    shift = 8 - bit_depth * (index % per_byte + 1)
                    palette_index = (packed >> shift) & ((1 << bit_depth) - 1)
                if palette_index >= len(palette):
                    raise ValueError("indice de paleta PNG invalido")
                red, green, blue = palette[palette_index]
                alpha = transparency[palette_index] if transparency and palette_index < len(transparency) else 255
            decoded.append((red, green, blue, alpha))
        pixels.append(decoded)
    return width, height, pixels


def trim_transparent(width, height, pixels):
    visible = [
        (x, y)
        for y, row in enumerate(pixels)
        for x, pixel in enumerate(row)
        if pixel[3] > 0
    ]
    if not visible:
        return 1, 1, [[(0, 0, 0, 0)]]
    left = min(point[0] for point in visible)
    right = max(point[0] for point in visible)
    top = min(point[1] for point in visible)
    bottom = max(point[1] for point in visible)
    return (
        right - left + 1,
        bottom - top + 1,
        [row[left:right + 1] for row in pixels[top:bottom + 1]],
    )


def resize_nearest(width, height, pixels, limit):
    scale = min(1.0, limit / width)
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * new_width / width)))
    resized = []
    for y in range(new_height):
        source_y = min(height - 1, y * height // new_height)
        resized.append([
            pixels[source_y][min(width - 1, x * width // new_width)]
            for x in range(new_width)
        ])
    return new_width, new_height, resized


def _terminal_color(pixel):
    red, green, blue, alpha = pixel
    return DEFAULT_COLOR if alpha < 128 else (red << 16) | (green << 8) | blue


def _color_distance(first, second):
    total = 0
    for left, right in zip(first, second):
        if left == DEFAULT_COLOR or right == DEFAULT_COLOR:
            total += 0 if left == right else 0x1000000
        else:
            total += sum(((left >> shift & 0xff) - (right >> shift & 0xff)) ** 2 for shift in (16, 8, 0))
    return total


def raster_cells(pixels):
    height = len(pixels)
    width = len(pixels[0])
    pairs = []
    for y in range(0, height, 2):
        for x in range(width):
            top = _terminal_color(pixels[y][x])
            bottom = _terminal_color(pixels[y + 1][x]) if y + 1 < height else DEFAULT_COLOR
            pairs.append((top, bottom))
    distinct = set(pairs)
    quantized = len(distinct) > 1024
    if quantized:
        frequencies = {}
        for pair in pairs:
            frequencies[pair] = frequencies.get(pair, 0) + 1
        palette = sorted(frequencies, key=lambda pair: (-frequencies[pair], pair))[:1024]
        pairs = [min(palette, key=lambda candidate: _color_distance(pair, candidate)) for pair in pairs]
    cells = bytearray()
    for top, bottom in pairs:
        code_point = 0x20 if top == DEFAULT_COLOR and bottom == DEFAULT_COLOR else 0x2580
        cells.extend(struct.pack("<III", code_point, top, bottom))
    return width, (height + 1) // 2, base64.b64encode(bytes(cells)).decode("ascii"), quantized


def terminal_json(source):
    width, height, pixels = decode_png(source.read_bytes())
    width, height, pixels = trim_transparent(width, height, pixels)
    results = {}
    for mode, limit in TERMINAL_LIMITS.items():
        resized = resize_nearest(width, height, pixels, limit)
        results[mode] = raster_cells(resized[2])
    return results


def populate_terminal():
    source_dir = cache_dir()
    target = source_dir / "terminal"
    target.mkdir(parents=True, exist_ok=True)
    sources = []
    for path in sorted(source_dir.glob("*.png"), key=lambda item: item.name.lower()):
        match = SPRITE_NAME.match(path.name)
        if match:
            sources.append((path, f"{match.group(1)}-{match.group(2).lower()}"))
        elif path.name.lower() == "treinador.png":
            sources.append((path, "treinador"))
    generated = 0
    quantized = 0
    sizes = {}
    for source, stem in sources:
        converted = terminal_json(source)
        for mode in TERMINAL_LIMITS:
            destination = target / f"{stem}-{mode}.json"
            if destination.is_file() and destination.stat().st_mtime >= source.stat().st_mtime:
                continue
            values = converted[mode]
            cols, rows, cells, was_quantized = values
            payload = json.dumps({"cols": cols, "rows": rows, "cells": cells}, separators=(",", ":"))
            atomic_write(destination, payload.encode("utf-8"))
            generated += 1
            quantized += int(was_quantized)
            sizes.setdefault(stem, {})[mode] = f"{cols}x{rows}"
    print(f"terminal: {generated} JSON gerados; {quantized} sprites quantizados")
    for stem in sorted(sizes):
        print(f"terminal: {stem}: " + ", ".join(f"{mode}={sizes[stem][mode]}" for mode in sizes[stem]))


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
    accepted = ("claude", "codex", "agy")
    if provider not in accepted:
        if separator and key_provider in accepted:
            provider = key_provider
        elif separator:
            return None
        else:
            provider = "claude"

    email = item.get("email")
    if not isinstance(email, str) or not email.strip():
        if separator:
            if key_provider not in accepted:
                return None
            email = key_email
        else:
            email = source_key
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
        identity = map_identity(source_key, normalized)
        if identity is None:
            continue
        provider, email = identity
        if provider == "agy":
            email = "antigravity"
            number = AGY_NUMBER
        identity = (provider, email)
        normalized["email"] = email
        normalized["provedor"] = provider
        map_key = qualified_key(provider, email)
        # If both forms exist, preserve the already-migrated entry first.  The
        # ordering also makes conflicting legacy duplicates deterministic.
        candidates.append((source_key == map_key, map_key, source_key, number, normalized, identity))

    for is_qualified, map_key, _source_key, number, normalized, identity in sorted(
        candidates, key=lambda candidate: (
            candidate[5] != ("agy", "antigravity"),
            not candidate[0],
            candidate[1].lower(),
            str(candidate[2]).lower(),
        )
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
        if provider not in ("claude", "codex", "agy") or not isinstance(email, str) or not email.strip():
            continue
        email = email.strip().lower()
        if provider == "agy":
            email = "antigravity"
        account["provedor"] = provider
        account["email"] = email
        by_identity[(provider, email)] = account

    available = set(SPECIES_NUMBERS) - set(owners)
    if ("agy", "antigravity") in by_identity:
        available.discard(AGY_NUMBER)
    reuse = []
    for identity in sorted(by_identity):
        provider, email = identity
        account = by_identity[identity]
        map_key = identity_keys.get(identity)
        item = retained.get(map_key) if map_key is not None else None
        if item is None:
            if provider == "agy":
                number = AGY_NUMBER
            elif available:
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
        if provider == "agy":
            item["nome"] = "gastly"
        if account.get("plano"):
            item["plano"] = account["plano"]
        else:
            item.pop("plano", None)
    return retained, reuse


def populate(reset=False):
    accounts = claude_accounts() + codex_accounts() + [dict(AGY_ACCOUNT)]
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

    selvagens_path = target / "selvagens.json"
    selvagens_existentes = {}
    try:
        valor = json.loads(selvagens_path.read_text(encoding="utf-8"))
        if isinstance(valor, list):
            selvagens_existentes = {
                item["numero"]: item["nome"]
                for item in valor
                if isinstance(item, dict)
                and isinstance(item.get("numero"), int)
                and isinstance(item.get("nome"), str)
            }
    except (OSError, ValueError):
        pass
    selvagens = []
    for number in SELVAGENS:
        destination = target / f"{number}-frente.png"
        if not destination.is_file():
            try:
                atomic_write(destination, download(SPRITE_URL.format(path=str(number))))
            except Exception as error:
                print(f"aviso: sprite selvagem {number}: {error}", file=sys.stderr)
        name = selvagens_existentes.get(number)
        if name is None:
            try:
                name = pokemon_name(number)
            except Exception as error:
                print(f"aviso: nome Pokemon selvagem {number}: {error}", file=sys.stderr)
        if name is not None:
            selvagens.append({"numero": number, "nome": name})
    selvagens_data = json.dumps(selvagens, ensure_ascii=False, indent=2).encode("utf-8")
    if not selvagens_path.is_file() or selvagens_path.read_bytes() != selvagens_data:
        atomic_write(selvagens_path, selvagens_data)

    trainer_path = target / "treinador.png"
    if not trainer_path.is_file():
        for trainer_url in TRAINER_URLS:
            try:
                trainer_data = download(trainer_url)
                if not trainer_data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("resposta nao e PNG")
                atomic_write(trainer_path, trainer_data)
                print(f"treinador Red: {trainer_url}")
                break
            except Exception as error:
                print(f"aviso: treinador Red {trainer_url}: {error}", file=sys.stderr)

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
    old = {"old@example.com": {"numero": 25, "nome": "pikachu"}}
    allocated, reuse = assign_numbers(
        [{"email": "old@example.com", "provedor": "claude"},
         {"email": "new@example.com", "provedor": "codex", "plano": "plus"}], old)
    assert allocated["claude:old@example.com"]["numero"] == 25
    assert 1 <= allocated["codex:new@example.com"]["numero"] <= MAX_SPECIES
    assert allocated["codex:new@example.com"]["numero"] != 25
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
    agy, agy_reuse = assign_numbers(
        [{"email": "antigravity", "provedor": "agy"}], old, reset=True)
    assert agy["agy:antigravity"]["numero"] == AGY_NUMBER
    assert agy["agy:antigravity"]["provedor"] == "agy"
    assert not agy_reuse

    def png_chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
            ">I", binascii.crc32(kind + payload) & 0xffffffff
        )

    def synthetic_png(width, height, color_type, rows, palette=b"", transparency=b"", filters=None):
        channels = {2: 3, 3: 1, 6: 4}[color_type]
        encoded = bytearray()
        previous = bytearray(width * channels)
        filters = tuple(range(len(rows))) if filters is None else filters
        for filter_type, row in zip(filters, rows):
            filtered = bytearray()
            for index, value in enumerate(row):
                left = row[index - channels] if index >= channels else 0
                above = previous[index]
                upper_left = previous[index - channels] if index >= channels else 0
                if filter_type == 0:
                    predictor = 0
                elif filter_type == 1:
                    predictor = left
                elif filter_type == 2:
                    predictor = above
                elif filter_type == 3:
                    predictor = (left + above) // 2
                else:
                    predictor = _paeth(left, above, upper_left)
                filtered.append((value - predictor) & 0xff)
            encoded.extend(bytes((filter_type,)) + filtered)
            previous = bytearray(row)
        header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
        result = bytearray(PNG_SIGNATURE + png_chunk(b"IHDR", header))
        if palette:
            result.extend(png_chunk(b"PLTE", palette))
        if transparency:
            result.extend(png_chunk(b"tRNS", transparency))
        result.extend(png_chunk(b"IDAT", zlib.compress(bytes(encoded))))
        result.extend(png_chunk(b"IEND", b""))
        return bytes(result)

    rgba = synthetic_png(2, 2, 6, [
        bytes((0, 0, 0, 0, 255, 0, 0, 255)),
        bytes((0, 0, 255, 255, 0, 0, 0, 0)),
    ])
    rgba_image = decode_png(rgba)
    assert rgba_image[:2] == (2, 2)
    rgba_trim = trim_transparent(*rgba_image)
    rgba_raster = raster_cells(resize_nearest(*rgba_trim, 14)[2])
    expected_rgba = [
        (0x2580, DEFAULT_COLOR, 0x0000ff),
        (0x2580, 0xff0000, DEFAULT_COLOR),
    ]
    words = struct.unpack("<" + "I" * (len(expected_rgba) * 3), base64.b64decode(rgba_raster[2]))
    assert list(zip(words[0::3], words[1::3], words[2::3])) == expected_rgba

    palette = synthetic_png(3, 4, 3, [
        bytes((0, 1, 0)), bytes((0, 2, 1)), bytes((0, 0, 0)), bytes((0, 0, 0)),
    ], palette=bytes((255, 0, 0, 0, 255, 0, 0, 0, 255)), transparency=bytes((0, 255, 255)))
    palette_image = decode_png(palette)
    palette_trim = trim_transparent(*palette_image)
    assert palette_trim[:2] == (2, 2)
    palette_raster = raster_cells(palette_trim[2])
    palette_words = struct.unpack("<" + "I" * 6, base64.b64decode(palette_raster[2]))
    assert list(zip(palette_words[0::3], palette_words[1::3], palette_words[2::3])) == [
        (0x2580, 0x00ff00, 0x0000ff),
        (0x2580, DEFAULT_COLOR, 0x00ff00),
    ]

    filtered = synthetic_png(1, 5, 2, [
        bytes((10, 20, 30)), bytes((11, 21, 31)), bytes((12, 22, 32)),
        bytes((13, 23, 33)), bytes((14, 24, 34)),
    ], filters=(0, 1, 2, 3, 4))
    assert decode_png(filtered)[2][-1][0] == (14, 24, 34, 255)

    swapped = [(code, background, foreground) for code, foreground, background in expected_rgba]
    assert swapped != expected_rgba
    untrimmed = resize_nearest(*palette_image, 14)
    assert (untrimmed[0], untrimmed[1]) != (palette_trim[0], palette_trim[1])
    print("sprites.py selftest: ok (PNG RGBA/paleta, filtros, mutacoes detectadas)")


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
    for number in SELVAGENS:
        referenced.update((f"{number}-frente.png", f"{number}-costas.png"))
    referenced.update((f"{AGY_NUMBER}-frente.png", f"{AGY_NUMBER}-costas.png", "treinador.png"))

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
    parser.add_argument("--terminal", action="store_true")
    parser.add_argument("--limpar", action="store_true")
    parser.add_argument("--resetar", action="store_true")
    args = parser.parse_args()
    try:
        if args.selftest:
            selftest()
        elif args.terminal:
            populate_terminal()
        elif args.limpar:
            clean_orphans()
        else:
            populate(reset=args.resetar)
    except Exception as error:
        print(f"erro: {error}", file=sys.stderr)
        raise SystemExit(1)
