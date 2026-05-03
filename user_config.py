# user_config.py
# Версия: V5.22.6
#
# Слой над settings.ini. Читает INI-файл с пользовательскими настройками,
# отдаёт значения через typed-getters. Если файл отсутствует или ключ не
# задан — возвращает default.
#
# Используется в config.py для получения "пользовательских" параметров.
# Архитектурные константы (RAM-карта, банковая раскладка) остаются
# хардкодом в config.py.

import os

# ============================================================
# Парсер INI с поддержкой комментариев (;) и hex-чисел
# ============================================================

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "settings.ini")
_cache = None


def _parse_ini(path):
    """
    Возвращает dict {section -> {key -> value_str}}.
    Регистр в именах секций и ключей нормализуется в lower.
    Файл не обязан существовать — тогда возвращается пустой dict.
    """
    sections = {}
    if not os.path.exists(path):
        return sections

    current = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            stripped = line.strip()

            # пустые / комментарии
            if not stripped or stripped.startswith(";") or stripped.startswith("#"):
                continue

            # секция [name]
            if stripped.startswith("[") and "]" in stripped:
                name = stripped[1:stripped.index("]")].strip().lower()
                current = name
                if current not in sections:
                    sections[current] = {}
                continue

            # ключ = значение
            if current is None:
                continue
            if "=" not in line:
                continue
            eq = line.index("=")
            key = line[:eq].strip().lower()
            val = line[eq + 1:].strip()
            # обрезать inline-комментарий после ;
            if ";" in val:
                val = val[:val.index(";")].strip()
            if key:
                sections[current][key] = val

    return sections


def _ensure_loaded():
    global _cache
    if _cache is None:
        _cache = _parse_ini(_SETTINGS_PATH)
    return _cache


# ============================================================
# Helpers конвертации
# ============================================================

def _parse_int(s):
    """
    Поддержка: 15, $1F, 0x1F.
    """
    s = s.strip()
    if not s:
        raise ValueError("empty number")
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.lower().startswith("0x"):
        return int(s[2:], 16)
    return int(s, 10)


def _parse_bool(s):
    """
    1 / 0 / true / false / yes / no / on / off (регистр не важен)
    """
    s = s.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"bad bool: {s!r}")


# ============================================================
# Public API
# ============================================================

def get_str(section, key, default):
    cfg = _ensure_loaded()
    sec = cfg.get(section.lower(), {})
    return sec.get(key.lower(), default)


def get_int(section, key, default):
    s = get_str(section, key, None)
    if s is None:
        return default
    try:
        return _parse_int(s)
    except Exception:
        return default


def get_bool(section, key, default):
    s = get_str(section, key, None)
    if s is None:
        return default
    try:
        return _parse_bool(s)
    except Exception:
        return default


def settings_loaded():
    """True если settings.ini был найден и распарсен."""
    return os.path.exists(_SETTINGS_PATH)


def all_sections():
    """Имена всех секций из INI (для диагностики)."""
    return list(_ensure_loaded().keys())
