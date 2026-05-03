# Auto-generated from coolgirl_builder.py
# Do not edit by hand unless you know what you are doing.

import sys
from config import *

# ============================================================
# Таблица кастомных символов (кириллица/доп. знаки)
# Заполняется на этапе сборки из multi_alt.
#
# V5.23 Шаг 1: переход на ВИРТУАЛЬНЫЕ индексы.
# Для совместимости поддерживаются 2 режима:
#   "phys"    — старый: словарь {символ → phys_tile $00..$FF}
#   "virtual" — новый:  словарь {символ → custom_idx},
#               encode_orig_menu_char вернёт $80 | custom_idx,
#               runtime ASM подменит через MA_MAP_RAM на phys_tile.
# ============================================================
CUSTOM_CHAR_MAP = {}
CUSTOM_CHAR_MODE = "phys"   # "phys" | "virtual"

def set_custom_char_map(m: dict):
    """
    Старый интерфейс — для совместимости.
    m: dict вида {'А':0xDD, '#':0xC0, ...} — phys_tile.
    """
    global CUSTOM_CHAR_MAP, CUSTOM_CHAR_MODE
    CUSTOM_CHAR_MAP = dict(m or {})
    CUSTOM_CHAR_MODE = "phys"

def set_custom_char_map_virtual(m: dict):
    """
    V5.23 Шаг 1: новый интерфейс — виртуальные индексы.
    m: dict вида {'А':0, 'Б':1, '#':40, ...} — custom_idx.
    encode_orig_menu_char вернёт $80 | custom_idx (значения $80..$FE).
    """
    global CUSTOM_CHAR_MAP, CUSTOM_CHAR_MODE
    CUSTOM_CHAR_MAP = dict(m or {})
    CUSTOM_CHAR_MODE = "virtual"

def next_pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p

def align_up(v, a):
    r = v % a
    return v + (a - r) if r else v

def log(msg=""):
    try:
        print(msg)
    except UnicodeEncodeError:
        # Игнорировать ошибки кодировки, заменяя проблемные символы
        print(msg.encode('cp1251', errors='replace').decode('cp1251'))

def log_space(name, used, total):
    free = total - used
    pct  = (used * 100.0 / total) if total else 0.0
    log(f"  {name:26s}: used={used:6d}  free={free:6d}  total={total:6d}  ({pct:5.1f}%)")

def parse_build_stage():
    stage = BUILD_STAGE
    for a in sys.argv[1:]:
        if a.startswith("--stage="):
            stage = int(a.split("=", 1)[1])
    if stage not in (1, 2):
        raise ValueError("BUILD_STAGE must be 1 or 2")
    return stage

import re

_UNICODE_ESCAPE_RE = re.compile(r"\{U\+([0-9A-Fa-f]{1,6})\}")

def expand_unicode_escapes(name: str) -> str:
    """V5.24: разворачивает escape-последовательности `{U+XXXX}` в реальные
    Unicode-символы ПЕРЕД per-char-кодированием.

    Это нужно потому что CSV-файлы могут быть сохранены в ANSI/CP1251 (Excel),
    где спецсимволы типа ■/♥/★ ломаются. ASCII-безопасный `{U+25A0}` живёт
    в любой кодировке.

    Примеры:
        "MARIO {U+25A0}"   → "MARIO ■"
        "FIRE {U+2660}"    → "FIRE ♠"
        "{U+2665} HEART"   → "♥ HEART"
        "{U+25A0}{U+2665}" → "■♥"

    Не-найденные паттерны (например `{U+XXXX}` где XXXX не hex) — оставляются
    как есть, никаких исключений.
    """
    if not name or "{U+" not in name:
        return name
    def _sub(m):
        try:
            cp = int(m.group(1), 16)
            if 0 <= cp <= 0x10FFFF:
                return chr(cp)
        except ValueError:
            pass
        return m.group(0)
    return _UNICODE_ESCAPE_RE.sub(_sub, name)


# ============================================================
# V5.25: Тэг-тайлы T+{...}.png
#
# Регистрация произвольных имён-токенов через файлы вида T+{1}.png,
# T+{flag}.png. Тэги маппятся на свободные код-поинты PUA (U+E000..U+F8FF).
# В CSV пишется тэг как есть ({1}), expand_tags() заменяет его на PUA-символ
# ДО per-char-кодирования.
# ============================================================
_TAG_TO_PUA = {}
_PUA_TO_TAG = {}
_NEXT_PUA_CP = 0xE000

def register_tag(tag):
    """Регистрирует тэг (вида '{1}', '{flag}') и возвращает PUA-символ.
    Идемпотентно: повторный вызов с тем же тэгом возвращает тот же символ.
    """
    global _NEXT_PUA_CP
    if not (tag.startswith("{") and tag.endswith("}") and len(tag) >= 3):
        raise ValueError("тэг должен быть в виде {{...}}: " + repr(tag))
    if tag in _TAG_TO_PUA:
        return _TAG_TO_PUA[tag]
    if _NEXT_PUA_CP > 0xF8FF:
        raise RuntimeError("исчерпан диапазон PUA для тэгов")
    ch = chr(_NEXT_PUA_CP)
    _NEXT_PUA_CP += 1
    _TAG_TO_PUA[tag] = ch
    _PUA_TO_TAG[ch] = tag
    return ch

def get_tag_for_char(ch):
    """Возвращает тэг по PUA-символу, или None."""
    return _PUA_TO_TAG.get(ch)

def list_tags():
    """Возвращает [(tag, pua_char, codepoint), ...] для логирования."""
    return [(t, c, ord(c)) for t, c in sorted(_TAG_TO_PUA.items())]

_TAG_RE = re.compile(r"\{[^{}]+\}")

def expand_tags(name):
    """Заменяет зарегистрированные тэги на PUA-символы.
    Вызывается ПОСЛЕ expand_unicode_escapes (так {U+XXXX} уже развёрнуты).
    Незарегистрированные тэги остаются как есть.
    """
    if not _TAG_TO_PUA or "{" not in name:
        return name
    def _sub(m):
        return _TAG_TO_PUA.get(m.group(0), m.group(0))
    return _TAG_RE.sub(_sub, name)


def encode_orig_menu_char(ch: str) -> int:
    """
    Кодировка символа в тайл-индекс меню.
    
    1) Кастомные символы из multi_alt:
       - режим "phys"    → phys_tile из CUSTOM_CHAR_MAP
       - режим "virtual" → $80 | custom_idx (runtime ASM подменит)
    2) Базовая кодировка оригинального меню ($01..$26)
    3) Иначе пробел ($26)
    """
    ch = ch.upper()

    # кастомные символы
    if ch in CUSTOM_CHAR_MAP:
        v = CUSTOM_CHAR_MAP[ch] & 0xFF
        if CUSTOM_CHAR_MODE == "virtual":
            # custom_idx должен быть < 128, чтобы $80 | idx был < $FF
            if v >= 0x80:
                # уже виртуальный — вернём как есть (защита от двойного OR)
                return v
            return 0x80 | v
        return v

    # базовая кодировка оригинального меню 9999 in 1
    if "0" <= ch <= "9":
        return ord(ch) - ord("0") + 0x01
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 0x0B
    if ch == ".":
        return 0x25
    if ch == " ":
        return 0x26

    return 0x26

def encode_orig_menu_name_fixed(name: str, length: int = 11) -> bytes:
    # V5.24: разворачиваем {U+XXXX} escape ДО per-char-кодирования.
    # V5.25: затем зарег. тэги ({1}, {flag}, ...).
    name = expand_unicode_escapes(name)
    name = expand_tags(name)
    data = [encode_orig_menu_char(c) for c in name.upper()][:length]
    if len(data) < length:
        data += [0x26] * (length - len(data))
    return bytes(data)

def encode_orig_menu_name_terminated(name: str, maxlen: int) -> bytes:
    # V5.24: разворачиваем {U+XXXX} escape ДО per-char-кодирования.
    # V5.25: затем зарег. тэги ({1}, {flag}, ...).
    name = expand_unicode_escapes(name)
    name = expand_tags(name)
    data = [encode_orig_menu_char(c) for c in name.upper()][:maxlen]
    if len(data) < maxlen:
        data.append(0x00)
    data = data[:maxlen]
    while len(data) < maxlen:
        data.append(0x00)
    return bytes(data)

def asm_byte_list(bs: bytes) -> str:
    return ",".join(f"${b:02X}" for b in bs)