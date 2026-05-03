# font_patch.py
# Версия: 1.6
# V1.6: добавлен skip_chars в apply_font_patch — символы обработанные
#       multi_alt пропускаются (не дублируется работа, не конфликтует пул).
#       Также есть extract_chr_for_chars для использования в multi_alt.
# V1.5: ручной парсер для [chars] и [atlas]
# чтобы символы : # и другие спецсимволы configparser работали корректно

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional


PNG_SIG = b"\x89PNG\r\n\x1a\n"

def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c

def _unfilter(filter_type, scan, prev, bpp):
    out = bytearray(scan)
    if filter_type == 0:
        return bytes(out)
    if prev is None:
        prev = b"\x00" * len(out)
    if filter_type == 1:
        for i in range(len(out)):
            left = out[i - bpp] if i >= bpp else 0
            out[i] = (out[i] + left) & 0xFF
        return bytes(out)
    if filter_type == 2:
        for i in range(len(out)):
            out[i] = (out[i] + prev[i]) & 0xFF
        return bytes(out)
    if filter_type == 3:
        for i in range(len(out)):
            left = out[i - bpp] if i >= bpp else 0
            up   = prev[i]
            out[i] = (out[i] + ((left + up) >> 1)) & 0xFF
        return bytes(out)
    if filter_type == 4:
        for i in range(len(out)):
            left    = out[i - bpp] if i >= bpp else 0
            up      = prev[i]
            up_left = prev[i - bpp] if i >= bpp else 0
            out[i]  = (out[i] + _paeth(left, up, up_left)) & 0xFF
        return bytes(out)
    raise ValueError(f"PNG: неподдерживаемый filter type: {filter_type}")

def read_png_rgba(path):
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(PNG_SIG):
        raise ValueError("PNG: плохая сигнатура")
    p = len(PNG_SIG)
    width = height = None
    bit_depth = color_type = interlace = None
    palette = []
    idat = bytearray()
    while p < len(data):
        if p + 8 > len(data):
            break
        length = struct.unpack(">I", data[p:p+4])[0]; p += 4
        ctype  = data[p:p+4]; p += 4
        chunk  = data[p:p+length]; p += length
        p += 4
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = \
                struct.unpack(">IIBBBBB", chunk)
            if comp != 0 or filt != 0:
                raise ValueError("PNG: неподдерживаемый compression/filter method")
            if interlace != 0:
                raise ValueError("PNG: interlaced не поддерживается")
            if bit_depth != 8:
                raise ValueError("PNG: поддерживается только 8-bit")
        elif ctype == b"PLTE":
            if len(chunk) % 3 != 0:
                raise ValueError("PNG: плохой PLTE")
            palette = [(chunk[i], chunk[i+1], chunk[i+2])
                       for i in range(0, len(chunk), 3)]
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
    if width is None:
        raise ValueError("PNG: нет IHDR")
    if not idat:
        raise ValueError("PNG: нет IDAT")
    raw = zlib.decompress(bytes(idat))
    if color_type == 2:
        bpp = 3
    elif color_type == 6:
        bpp = 4
    elif color_type == 3:
        bpp = 1
        if not palette:
            raise ValueError("PNG: indexed, но нет PLTE")
    else:
        raise ValueError(f"PNG: неподдерживаемый color_type={color_type}")
    stride   = width * bpp
    rowbytes = 1 + stride
    expected = rowbytes * height
    if len(raw) != expected:
        raise ValueError(f"PNG: неожиданный размер: {len(raw)} != {expected}")
    pixels = []
    prev = None
    off  = 0
    for _y in range(height):
        ftype = raw[off]
        scan  = raw[off+1:off+1+stride]
        off  += rowbytes
        scan  = _unfilter(ftype, scan, prev, bpp)
        prev  = scan
        row   = []
        if color_type == 2:
            for x in range(width):
                i = x*3
                row.append((scan[i], scan[i+1], scan[i+2], 255))
        elif color_type == 6:
            for x in range(width):
                i = x*4
                row.append((scan[i], scan[i+1], scan[i+2], scan[i+3]))
        else:
            for x in range(width):
                r,g,b = palette[scan[x]]
                row.append((r,g,b,255))
        pixels.append(row)
    return width, height, pixels


def _parse_hex_color_one(s):
    s = s.strip()
    if s.lower() == "auto":
        return (-1,-1,-1)
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        raise ValueError(f"цвет должен быть #RRGGBB или auto: {s}")
    return (int(s[0:2],16), int(s[2:4],16), int(s[4:6],16))

def _parse_color_list(s):
    s = s.strip()
    if not s:
        return []
    return [_parse_hex_color_one(p.strip()) for p in s.split(",") if p.strip()]

def _color_dist(c1, c2):
    return abs(c1[0]-c2[0]) + abs(c1[1]-c2[1]) + abs(c1[2]-c2[2])

def tile8_to_chr(tile_idx):
    plane0 = bytearray()
    plane1 = bytearray()
    for y in range(8):
        b0 = b1 = 0
        for x in range(8):
            v   = tile_idx[y][x] & 3
            b0 |= (v & 1) << (7 - x)
            b1 |= ((v >> 1) & 1) << (7 - x)
        plane0.append(b0)
        plane1.append(b1)
    return bytes(plane0 + plane1)

def _parse_int_hex(s):
    s = s.strip()
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.lower().startswith("0x"):
        return int(s[2:], 16)
    if all(ch in "0123456789abcdefABCDEF" for ch in s) and len(s) <= 4:
        return int(s, 16)
    return int(s, 10)

def _base_encode_char(ch):
    ch = ch.upper()
    if "0" <= ch <= "9":
        return ord(ch) - ord("0") + 0x01
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 0x0B
    if ch == ".":
        return 0x25
    if ch == " ":
        return 0x26
    return None


# =============================================================================
# РУЧНОЙ ПАРСЕР INI
# Не использует configparser — корректно обрабатывает : # и другие спецсимволы
# =============================================================================

def _parse_ini_manual(path: str) -> Dict[str, Dict[str, str]]:
    """
    Ручной парсер INI файла.
    Правила:
      - Секция: строка вида [name]
      - Комментарий: строка начинается с ; (точка с запятой)
        ТОЛЬКО ; является комментарием — # это обычный символ!
      - Ключ=значение: первый = или : разделяет ключ и значение
        НО для секций [chars] и [atlas] разделитель ТОЛЬКО первый =
        потому что ключ может быть : или #
    Возвращает dict секций → dict ключ→значение (всё строки)
    """
    sections: Dict[str, Dict[str, str]] = {}
    current_section = None

    # Секции где ключ может содержать спецсимволы : #
    SPECIAL_SECTIONS = {"chars", "atlas"}

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for lineno, raw_line in enumerate(lines, 1):
        # Убрать перенос строки
        line = raw_line.rstrip("\r\n")

        # Пропустить пустые строки
        stripped = line.strip()
        if not stripped:
            continue

        # Комментарий — ТОЛЬКО если начинается с ;
        if stripped.startswith(";"):
            continue

        # Секция [name]
        if stripped.startswith("[") and "]" in stripped:
            sec_name = stripped[1:stripped.index("]")].strip().lower()
            current_section = sec_name
            if current_section not in sections:
                sections[current_section] = {}
            continue

        if current_section is None:
            continue

        # Ключ = значение
        if current_section in SPECIAL_SECTIONS:
            # Для спецсекций: разделитель ТОЛЬКО первый =
            # Ключ может быть любым символом включая : и #
            if "=" not in line:
                continue
            eq_pos = line.index("=")
            key   = line[:eq_pos].strip()
            value = line[eq_pos+1:].strip()
            # Убрать inline комментарий после ;
            if ";" in value:
                value = value[:value.index(";")].strip()
        else:
            # Для обычных секций: разделитель = или :
            # но не первый символ строки (чтобы не путать с ключом-двоеточием)
            sep_pos = -1
            for i, ch in enumerate(stripped):
                if ch in ("=", ":"):
                    sep_pos = i
                    break
            if sep_pos < 0:
                continue
            key   = stripped[:sep_pos].strip()
            value = stripped[sep_pos+1:].strip()
            # Убрать inline комментарий после ;
            if ";" in value:
                value = value[:value.index(";")].strip()

        if key:
            sections[current_section][key] = value

    return sections


@dataclass
class FontPatchConfig:
    file:       str
    width:      int
    height:     int
    chr_table:  int
    empty_colors: List[Tuple[int,int,int]]
    white:      Tuple[int,int,int]
    black:      Tuple[int,int,int]
    empty_tol:  int
    white_tol:  int
    black_tol:  int
    unknown_as_empty: bool
    strict:     bool
    auto_assign: bool
    auto_start: int
    atlas_rows: List[List[Optional[str]]]
    chars_map:  Dict[str, int]


def load_font_patch_ini(path: str) -> FontPatchConfig:
    secs = _parse_ini_manual(path)

    def get(sec, key, fallback=""):
        return secs.get(sec, {}).get(key, fallback)

    # [general]
    strict      = get("general", "strict",      "1") not in ("0","false","no")
    auto_assign = get("general", "auto_assign", "1") not in ("0","false","no")
    auto_start  = _parse_int_hex(get("general", "auto_start", "DD"))

    # [image]
    img_file  = get("image", "file",      "font.png").strip()
    size_s    = get("image", "size",      "128x48").strip().lower()
    chr_table = int(get("image", "chr_table", "0").strip())
    w, h      = [int(x) for x in size_s.split("x", 1)]
    if w % 8 or h % 8:
        raise ValueError("Размер PNG должен делиться на 8")

    # [colors]
    empty_s = get("colors", "empty", "").strip()
    if not empty_s:
        empty_s = get("colors", "bg", "auto").strip()
    empty_colors = _parse_color_list(empty_s)
    if not empty_colors:
        empty_colors = [(-1,-1,-1)]

    white = _parse_hex_color_one(get("colors", "white", "#FFFFFF"))
    black = _parse_hex_color_one(get("colors", "black", "#000000"))

    common_tol = int(get("colors", "tolerance",       "0").strip() or "0")
    empty_tol  = int(get("colors", "empty_tolerance", str(common_tol)).strip() or "0")
    white_tol  = int(get("colors", "white_tolerance", str(common_tol)).strip() or "0")
    black_tol  = int(get("colors", "black_tolerance", str(common_tol)).strip() or "0")

    unknown_as_empty = get("colors", "unknown_as_empty", "1") not in ("0","false","no")

    # [atlas]
    if "atlas" not in secs:
        raise ValueError("Нет секции [atlas]")

    cols = w // 8
    rows = h // 8

    atlas_sec  = secs["atlas"]
    rows_kv    = []
    for k, v in atlas_sec.items():
        if k.lower().startswith("row"):
            try:
                n = int(k[3:])
                rows_kv.append((n, v))
            except ValueError:
                pass
    rows_kv.sort(key=lambda t: t[0])
    if len(rows_kv) != rows:
        raise ValueError(f"[atlas] строк {len(rows_kv)}, нужно {rows}")

    atlas_rows: List[List[Optional[str]]] = []
    for _n, line in rows_kv:
        toks = [t for t in line.split(" ") if t != ""]
        if len(toks) != cols:
            raise ValueError(
                f"[atlas] нужно {cols} токенов, получено {len(toks)}: '{line}'")
        row: List[Optional[str]] = []
        for t in toks:
            row.append(None if t == "__" else t)
        atlas_rows.append(row)

    # [chars]
    chars_map: Dict[str, int] = {}
    if "chars" in secs:
        for k, v in secs["chars"].items():
            if k and v:
                chars_map[k.upper()] = _parse_int_hex(v) & 0xFF

    return FontPatchConfig(
        file=img_file, width=w, height=h, chr_table=chr_table,
        empty_colors=empty_colors,
        white=white, black=black,
        empty_tol=empty_tol, white_tol=white_tol, black_tol=black_tol,
        unknown_as_empty=unknown_as_empty,
        strict=strict, auto_assign=auto_assign, auto_start=auto_start,
        atlas_rows=atlas_rows,
        chars_map=chars_map,
    )


def _most_common_rgb(pixels):
    freq = {}
    for row in pixels:
        for r,g,b,a in row:
            if a == 0:
                continue
            rgb = (r,g,b)
            freq[rgb] = freq.get(rgb, 0) + 1
    if not freq:
        return (0,0,0)
    return max(freq.items(), key=lambda kv: kv[1])[0]


def extract_src_tile_rgba(pixels, tile_x, tile_y):
    base_x = tile_x * 8
    base_y = tile_y * 8
    return [[pixels[base_y+yy][base_x+xx] for xx in range(8)]
            for yy in range(8)]


def rgba_tile_to_indexed(tile_rgba, empty_colors_resolved,
                          white_rgb, black_rgb,
                          empty_tol, white_tol, black_tol,
                          unknown_as_empty):
    out = [[0]*8 for _ in range(8)]
    for y in range(8):
        for x in range(8):
            r,g,b,a = tile_rgba[y][x]
            if a == 0:
                out[y][x] = 0
                continue
            rgb = (r,g,b)
            for ec in empty_colors_resolved:
                if _color_dist(rgb, ec) <= empty_tol:
                    out[y][x] = 2
                    break
            else:
                if _color_dist(rgb, white_rgb) <= white_tol:
                    out[y][x] = 3
                elif _color_dist(rgb, black_rgb) <= black_tol:
                    out[y][x] = 0
                else:
                    if unknown_as_empty:
                        out[y][x] = 0
                    else:
                        raise ValueError(f"неизвестный цвет {rgb}")
    return out


def apply_font_patch(chr_menu_8k: bytearray,
                     ini_path: str,
                     log=print,
                     skip_chars=None) -> Dict[str, int]:
    cfg = load_font_patch_ini(ini_path)

    img_path = cfg.file
    if not os.path.isabs(img_path):
        img_path = os.path.join(os.path.dirname(ini_path), img_path)
    if not os.path.exists(img_path):
        raise FileNotFoundError(img_path)

    w, h, pixels = read_png_rgba(img_path)
    if w != cfg.width or h != cfg.height:
        raise ValueError(f"PNG size {w}x{h} != cfg {cfg.width}x{cfg.height}")

    cols = w // 8
    rows = h // 8

    auto_rgb = _most_common_rgb(pixels)

    empty_resolved = []
    for ec in cfg.empty_colors:
        empty_resolved.append(auto_rgb if ec[0] < 0 else ec)

    # Строим карту символ → индекс тайла в атласе
    char_to_src: Dict[str, int] = {}
    for y, row in enumerate(cfg.atlas_rows):
        for x, token in enumerate(row):
            if token is None:
                continue
            ch = token.upper()
            if ch not in char_to_src:
                char_to_src[ch] = y * cols + x

    used_base = set(range(0x01, 0x27))
    pt_base   = 0x1000 * (cfg.chr_table & 1)

    # Пул свободных тайлов для auto_assign
    # skip_chars (символы уже размещённые multi_alt) исключаем из обработки
    skip_set = set((c.upper() for c in (skip_chars or [])))
    free_pool = []
    for tid in range(cfg.auto_start, 256):
        if tid in used_base:
            continue
        ofs = pt_base + tid * 16
        if (ofs + 16 <= len(chr_menu_8k) and
                chr_menu_8k[ofs:ofs+16] == b"\x00" * 16):
            free_pool.append(tid)

    auto_map: Dict[str, int] = {}
    pool_i = 0

    def alloc_tile_for(ch: str) -> int:
        nonlocal pool_i
        if pool_i >= len(free_pool):
            raise ValueError(
                f"не хватает пустых тайлов для auto_assign "
                f"(нужен символ '{ch}', пул исчерпан на {pool_i})")
        t = free_pool[pool_i]
        pool_i += 1
        return t

    src_chr: Dict[int, bytes] = {}

    def get_src_chr(src_index: int) -> bytes:
        if src_index in src_chr:
            return src_chr[src_index]
        sx = src_index % cols
        sy = src_index // cols
        tile_rgba = extract_src_tile_rgba(pixels, sx, sy)
        tile_idx  = rgba_tile_to_indexed(
            tile_rgba, empty_resolved,
            cfg.white, cfg.black,
            cfg.empty_tol, cfg.white_tol, cfg.black_tol,
            cfg.unknown_as_empty)
        b = tile8_to_chr(tile_idx)
        src_chr[src_index] = b
        return b

    patched   = 0
    skipped   = 0
    final_map = dict(cfg.chars_map)

    # Если multi_alt активен (skip_chars не пуст) — auto_assign отключаем
    auto_assign_active = cfg.auto_assign and not skip_set

    for ch, src_index in char_to_src.items():
        # Пропуск символов уже обработанных multi_alt
        if ch in skip_set:
            continue
        dst = final_map.get(ch)
        if dst is None:
            dst = _base_encode_char(ch)

        if dst is None:
            if auto_assign_active:
                dst = alloc_tile_for(ch)
                auto_map[ch] = dst
                final_map[ch] = dst
            else:
                skipped += 1
                # При активном multi_alt: тихо пропускаем (символ не нужен)
                continue

        ofs = pt_base + (dst * 16)
        if ofs + 16 > len(chr_menu_8k):
            raise ValueError(f"dst tile ${dst:02X} вне CHR-меню")
        chr_menu_8k[ofs:ofs+16] = get_src_chr(src_index)
        patched += 1

    log(
        "  Font patch: "
        f"patched={patched} skipped={skipped} "
        f"auto_assigned={len(auto_map)} "
        f"empty_auto={auto_rgb} "
        f"empty_tol={cfg.empty_tol} white_tol={cfg.white_tol} "
        f"black_tol={cfg.black_tol} "
        f"unknown_as_empty={int(cfg.unknown_as_empty)} "
        f"img={os.path.basename(img_path)}"
    )
    return final_map


def apply_font_patch_if_present(chr_menu_8k: bytearray,
                                 ini_path: str,
                                 log=print,
                                 skip_chars=None) -> Dict[str, int]:
    if not ini_path or not os.path.exists(ini_path):
        return {}
    return apply_font_patch(chr_menu_8k, ini_path, log=log, skip_chars=skip_chars)



# =============================================================================
# V5.23: Извлечение CHR-данных по символам без модификации chr_menu_8k.
# Используется multi_alt.py для построения custom_chr_data.
# =============================================================================

def extract_chr_for_chars(ini_path: str,
                           chars_wanted) -> "dict":
    """Возвращает словарь {символ → 16-байт CHR-данных} для символов
    из chars_wanted, прочитанных из font.png по atlas.

    Не модифицирует chr_menu_8k. Не использует chars_map (явное назначение
    tile_id больше не нужно — это теперь делает multi_alt).

    chars_wanted: iterable строк (символов в верхнем регистре).
                  Если символа нет в [atlas] — будет KeyError.
    """
    cfg = load_font_patch_ini(ini_path)
    img_path = cfg.file
    if not os.path.isabs(img_path):
        img_path = os.path.join(os.path.dirname(ini_path), img_path)
    if not os.path.exists(img_path):
        raise FileNotFoundError(img_path)

    w, h, pixels = read_png_rgba(img_path)
    if w != cfg.width or h != cfg.height:
        raise ValueError(f"PNG size {w}x{h} != cfg {cfg.width}x{cfg.height}")

    cols = w // 8

    auto_rgb = _most_common_rgb(pixels)
    empty_resolved = []
    for ec in cfg.empty_colors:
        empty_resolved.append(auto_rgb if ec[0] < 0 else ec)

    # символ → индекс тайла в атласе
    char_to_src = {}
    for y, row in enumerate(cfg.atlas_rows):
        for x, token in enumerate(row):
            if token is None:
                continue
            ch = token.upper()
            if ch not in char_to_src:
                char_to_src[ch] = y * cols + x

    result = {}
    for ch in chars_wanted:
        ch_upper = ch.upper()
        if ch_upper not in char_to_src:
            raise KeyError(
                f"Символ '{ch}' не найден в [atlas] раздела {ini_path}")
        src_index = char_to_src[ch_upper]
        sx = src_index % cols
        sy = src_index // cols
        tile_rgba = extract_src_tile_rgba(pixels, sx, sy)
        tile_idx  = rgba_tile_to_indexed(
            tile_rgba, empty_resolved,
            cfg.white, cfg.black,
            cfg.empty_tol, cfg.white_tol, cfg.black_tol,
            cfg.unknown_as_empty)
        result[ch] = tile8_to_chr(tile_idx)

    return result
