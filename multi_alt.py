# multi_alt.py
# Версия: V1.8
#
# V1.8: ФИКС — orig_chr_old в build_full_swap_tables теперь учитывает кастомы
#       на ЦЕЛЕВОМ экране, а не только rom_orig. Раньше: если Class-1 кастом
#       занимал phys $2C на s0 и уходил на $5B на s13, swap "восстанавливал"
#       rom_orig[$2C] на $2C — перезатирая Class-3 P_red, который Class-3-swap
#       уже записал туда. Симптом: пропадала буква "P" в "OPTIONS" на screen13
#       при добавлении любого нового tile через tiles/.
#
# V1.7: добавлен scan_tiles_dir(tiles_dir) — drop-in регистрация кастомов
#       через папку tiles/.
#       Имя файла = символ (★.png) или Unicode код-поинт (U+2665.png).
#       Подсуффикс @N ограничивает экраны: ★@13.png, ★@5-10.png, ★@0,5,7.png.
#       Поддерживаются форматы: PNG (8×8 ч/б, авто-конверт), .chr/.bin (16 байт raw).
#       make_chars_map / make_chars_map_virtual теперь учитывают и kind="raw"
#       если имя кастома — 1 символ (даёт прямое использование в games.list).
#
# V1.3: добавлен swap-механизм для размещения кастомов сверх global-free
#       лимита (41 шт). Алгоритм находит "почти-global-free" слоты
#       (свободны на 12-13 из 14 экранов) и помечает экраны-исключения
#       где нужен runtime swap CHR (через ASM в service bank).
#       Кодирование символов в encode_orig_menu_char остаётся стабильным —
#       phys_tile один на всех экранах, swap делает только перезапись CHR.
#
# V1.2: добавлен load_user_customs(ini_path) — загрузка пользовательских
#       кастомов из multi_alt.ini. Поддерживает типы 'font' (символ из atlas
#       font_patch.ini) и 'raw' (16 байт CHR из binary-файла).
#       Лимит — global-free слоты (без swap). Если новый кастом не помещается
#       — выбросит понятную ошибку.
#
# V1.1: добавлены apply_class1_to_chr и make_chars_map для интеграции в
#       builder.py. После анализа обнаружено что все 41 Класс-1 кастом
#       помещаются в глобально-свободные слоты ($AE..$FF), поэтому swap
#       при смене экрана НЕ НУЖЕН. Класс-3 (red на screen13) остаётся
#       в существующей системе prompt_attr_fix без изменений.
#
# Универсальная система кастомных тайлов на 14 экранах меню.
# Объединяет:
#   - кириллицу (33 символа)
#   - спецсимволы для font (8 символов: : # , ( ) ? - ')
#   - red-version букв для prompt на screen13 (S A F O R P)
#   - red-version букв для submenu на screen13 (C N T)
#
# Каждый кастом имеет внутренний индекс (0..MAX_CUSTOMS-1).
# Для каждого экрана построена таблица MAP[screen][custom_idx] = phys_tile,
# где phys_tile — реальный CHR-слот (или 0 если кастом на этом экране
# не размещён).
#
# При смене экрана ASM-функция svc_multi_alt_swap делает:
#   1. Restore оригиналов для слотов которые "уходят" (были на старом экране,
#      нет на новом)
#   2. Backup + write для слотов которые "приходят"

import os

try:
    from utils import log
except ImportError:
    log = print

# assemble_6502 импортируется только когда реально нужен ASM-вывод
# (не при standalone analysis)
try:
    from asm6502 import assemble_6502
    from config import (CFG5007, MA_LOADED_SCREEN, MA_BACKUP_DEPTH,
                         MA_BACKUP_BASE, MA_BACKUP_STRIDE, MA_MAP_RAM)
    _ASM_AVAILABLE = True
except ImportError:
    _ASM_AVAILABLE = False

# =============================================================================
# Лимиты
# =============================================================================

# Архитектурный лимит виртуального индекса.
# В именах игр кастомы кодируются байтом ($80 | custom_idx).
# Диапазон $80..$FE → custom_idx ≤ 126 ($FF зарезервирован под terminator).
# Жёсткий потолок системы.
MAX_CUSTOMS = 127

# Лимит Класс-1 + Класс-2 кастомов (попадают в RAM-таблицу MA_MAP_RAM).
# = free[13] для оригинального "9999 in 1.nes" — screen 13 самый забитый фоном,
# даёт максимум 89 свободных CHR-слотов из 256 (после исключения шрифта меню
# $01..$26). Раньше тут было 96, но 7 верхних байт RAM-буфера никогда не
# использовались — physically нельзя положить больше 89 кастомов на самом
# тесном экране. Сейчас лимит честный: упёршись в 89 пользователь видит
# понятную причину, а не невнятную ошибку "не помещается на экран 13".
MA_MAP_PER_SCREEN = 89

# Какие phys_tile считать "не для перезаписи" (font оригинала + защищённые ALT
# слоты submenu которые нельзя переписать на этапе runtime).
# $01..$25 — font ('0'-'9', 'A'-'Z', '.') — нужны для draw_list_line.
# $26 — пробел.
PROTECTED_FONT  = set(range(0x01, 0x27))

# =============================================================================
# Анализ свободных слотов на каждом экране
# =============================================================================

HEADER             = 16
SCREEN_DESC_BASE   = 0xD574
SCREEN_DESC_SIZE   = 18
NUM_SCREENS        = 14
NAMETABLE_BYTES    = 960

def _cpu2off(addr): return HEADER + (addr - 0xC000)

def _decode_rle(rom, start_cpu):
    """RLE по логике $C1C9: $00 терм, $01..$7F literal, $80..$FF RLE."""
    out = []
    p = _cpu2off(start_cpu)
    while True:
        cmd = rom[p]; p += 1
        if cmd == 0:
            break
        if cmd < 0x80:
            for _ in range(cmd):
                out.append(rom[p]); p += 1
        else:
            count = cmd & 0x7F
            fill = rom[p]; p += 1
            for _ in range(count):
                out.append(fill)
    return out

def analyze_screens(rom):
    """Возвращает список из 14 set'ов — used tiles на каждом экране."""
    per_screen_used = []
    for s in range(NUM_SCREENS):
        desc_off = _cpu2off(SCREEN_DESC_BASE + s * SCREEN_DESC_SIZE)
        rle_addr = rom[desc_off] | (rom[desc_off + 1] << 8)
        decoded = _decode_rle(rom, rle_addr)
        per_screen_used.append(set(decoded[:NAMETABLE_BYTES]))
    return per_screen_used

def free_slots_per_screen(per_screen_used):
    """Возвращает список из 14 set'ов — свободные phys_tile на каждом экране,
    исключая защищённые font-слоты."""
    full = set(range(256))
    return [
        (full - used) - PROTECTED_FONT
        for used in per_screen_used
    ]

# =============================================================================
# Спецификация кастомов
# =============================================================================

# Каждый кастом — кортеж (name, source_kind, source_data, screen_set, preferred_phys).
#
# source_kind:
#   "font"       — копируется из font.png (источник — символ)
#                  source_data = строка-символ
#   "red_orig"   — генерируется как red-version из оригинального font меню
#                  source_data = phys_tile исходной буквы в CHR оригинала
#   "raw"        — сырые 16 байт CHR (для будущего использования)
#                  source_data = bytes длиной 16
#
# screen_set:
#   None или set() — кастом нужен на ВСЕХ 14 экранах (Класс 1)
#   {13}          — только на screen13 (Класс 3)
#   {0, 5}        — только на этих экранах
#
# preferred_phys:
#   None — алгоритм сам выберет phys_tile
#   int  — алгоритм попробует использовать этот phys_tile (если он global-free
#          для Класс-1, или per-screen-free для Класс-3). Иначе — fallback
#          с warning.
#
# Порядок в этом списке = custom_idx (0, 1, 2, ...).

# Желаемое распределение (по запросу пользователя):
# Кириллица фиксируется в $DD..$FC (32 буквы, Ё нет)
# Спецсимволы и & — в $AE..$B6, $D6, $FD..$FF
_KIRILLIC_PREFS = {
    'А': 0xDD, 'Б': 0xDE, 'В': 0xDF, 'Г': 0xE0, 'Д': 0xE1, 'Е': 0xE2,
    'Ж': 0xE3, 'З': 0xE4, 'И': 0xE5, 'Й': 0xE6, 'К': 0xE7, 'Л': 0xE8,
    'М': 0xE9, 'Н': 0xEA, 'О': 0xEB, 'П': 0xEC, 'Р': 0xED, 'С': 0xEE,
    'Т': 0xEF, 'У': 0xF0, 'Ф': 0xF1, 'Х': 0xF2, 'Ц': 0xF3, 'Ч': 0xF4,
    'Ш': 0xF5, 'Щ': 0xF6, 'Ъ': 0xF7, 'Ы': 0xF8, 'Ь': 0xF9, 'Э': 0xFA,
    'Ю': 0xFB, 'Я': 0xFC,
}
_SPECIALS_PREFS = {
    ':': 0xAE, '#': 0xAF, ',': 0xB0, '(': 0xB5, ')': 0xB6, '?': 0xD6,
    '-': 0xFE, "'": 0xFF,
    # V1.4: расширение с разумно подобранными слотами для swap.
    # Выбраны phys_tile свободные на s2 и s8 (там показывается список ROM-ов
    # с именами, swap там портит отображение имён). Swap нужен только на s9
    # и s13 (там экран без списка либо специальный).
    '!': 0xD0, '@': 0xD1, '^': 0xD2, '_': 0xD3,
    '[': 0xD4, ']': 0xD7, '~': 0xD8,
}
_AMPERSAND_PREF = 0xFD

# Базовая кириллица (32 буквы — Ё нет)
_KIRILLIC = list("АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")

# Спецсимволы (берутся из font.png)
# 8 базовых + 7 расширения (`! @ ^ _ [ ] ~`) — итого 15 спецсимволов
_SPECIALS = [":", "#", ",", "(", ")", "?", "-", "'",
             "!", "@", "^", "_", "[", "]", "~"]

# Red-versions для prompt на screen13 ($1D=S, $0B=A, $10=F, $19=O, $1C=R, $1A=P)
_RED_PROMPT = [
    ("S_red", 0x1D),
    ("A_red", 0x0B),
    ("F_red", 0x10),
    ("O_red", 0x19),
    ("R_red", 0x1C),
    ("P_red", 0x1A),
]

# Red-versions для submenu на screen13 ($0D=C, $18=N, $1E=T)
_RED_SUBMENU = [
    ("C_red", 0x0D),
    ("N_red", 0x18),
    ("T_red", 0x1E),
]


def build_custom_table():
    """Собирает финальный список кастомов (name, kind, data, screens, preferred_phys)."""
    table = []
    
    # Класс 1: кириллица — на всех экранах
    for ch in _KIRILLIC:
        table.append((ch, "font", ch, None, _KIRILLIC_PREFS.get(ch)))
    
    # Знак &: на всех экранах
    table.append(("&", "font", "&", None, _AMPERSAND_PREF))
    
    # Спецсимволы — на всех экранах
    for ch in _SPECIALS:
        table.append((ch, "font", ch, None, _SPECIALS_PREFS.get(ch)))
    
    # Red prompt — только на screen13 (preferred_phys = существующие ALT_*)
    # ALT_S=0x27, ALT_A=0x28, ALT_F=0x29, ALT_O=0x2A, ALT_R=0x2B, ALT_P=0x2C
    _RED_PROMPT_PREFS = [0x27, 0x28, 0x29, 0x2A, 0x2B, 0x2C]
    for (name, src), pref in zip(_RED_PROMPT, _RED_PROMPT_PREFS):
        table.append((name, "red_orig", src, {13}, pref))
    
    # Red submenu — только на screen13 (ALT_C=0x43, ALT_N=0x48, ALT_T=0x49)
    _RED_SUBMENU_PREFS = [0x43, 0x48, 0x49]
    for (name, src), pref in zip(_RED_SUBMENU, _RED_SUBMENU_PREFS):
        table.append((name, "red_orig", src, {13}, pref))
    
    if len(table) > MAX_CUSTOMS:
        raise ValueError(f"Too many customs: {len(table)} > {MAX_CUSTOMS}")
    
    return table


# =============================================================================
# Назначение phys_tile для кастомов на каждом экране
# =============================================================================

def assign_slots(custom_table, free_per_screen):
    """V1.5: per-screen раскладка с минимизацией swap.
    
    Возвращает MAP[screen][custom_idx] = phys_tile.
    Один и тот же кастом может иметь РАЗНЫЕ phys_tile на разных экранах.
    
    Стратегия:
    1. Для каждого Класс-1 кастома берём preferred_phys (если задан) и
       пытаемся использовать его на экранах где он свободен. На экранах
       где он занят — выбираем альтернативный свободный слот.
    2. Без preferred — выбираем "хороший" слот (свободный на максимум
       экранов) и используем его как основной. Где конфликт — альтернатива.
    3. Класс-3 — только на нужных экранах, любой свободный слот.
    
    Цель: минимизировать число swap-операций при смене экранов.
    """
    n_customs = len(custom_table)
    
    # MAP[s][i] = phys_tile (0 = не размещён)
    MAP = [[0] * n_customs for _ in range(NUM_SCREENS)]
    
    # available[s] = set свободных phys_tile на экране s (изменяется по мере назначения)
    available = [set(slots) for slots in free_per_screen]
    
    # Глобально-свободные = пересечение
    global_free = set(range(256)) - PROTECTED_FONT
    for s in range(NUM_SCREENS):
        global_free &= available[s]
    
    # Разделяем по классам
    class1_with_pref     = []
    class1_without_pref  = []
    class3_with_pref     = []
    class3_without_pref  = []
    
    for i, ct in enumerate(custom_table):
        screens = ct[3]
        pref = ct[4] if len(ct) >= 5 else None
        if screens is None:
            (class1_with_pref if pref is not None else class1_without_pref).append((i, ct, pref))
        else:
            (class3_with_pref if pref is not None else class3_without_pref).append((i, ct, pref))
    
    # === Шаг 1: Класс-3 с preferred (резервируют свои phys_tile) ===
    # Сначала, чтобы Класс-1 не занял слоты которые нужны для red-prompt.
    for i, ct, pref in class3_with_pref:
        wanted = ct[3]
        for s in wanted:
            if pref in available[s]:
                MAP[s][i] = pref
                available[s].discard(pref)
            else:
                if not available[s]:
                    raise ValueError(
                        f"Кастом #{i} '{ct[0]}' (Класс 3) не помещается на s{s}")
                phys = min(available[s])
                MAP[s][i] = phys
                available[s].discard(phys)

    # === Шаг 2: Класс-3 без preferred ===
    for i, ct, _ in class3_without_pref:
        wanted = ct[3]
        for s in wanted:
            if not available[s]:
                raise ValueError(
                    f"Кастом #{i} '{ct[0]}' (Класс 3) не помещается на s{s}")
            phys = min(available[s])
            MAP[s][i] = phys
            available[s].discard(phys)

    # === Шаг 3: Класс-1 с preferred ===
    for i, ct, pref in class1_with_pref:
        for s in range(NUM_SCREENS):
            if pref in available[s]:
                MAP[s][i] = pref
                available[s].discard(pref)

    # === Шаг 4: Класс-1 без preferred — global_free сначала ===
    # Пересчитаем global_free после Класс-3
    global_free = set(range(256)) - PROTECTED_FONT
    for s in range(NUM_SCREENS):
        global_free &= available[s]
    
    g_free_sorted = sorted(global_free)
    g_idx = 0
    for i, ct, _ in class1_without_pref:
        if g_idx < len(g_free_sorted):
            phys = g_free_sorted[g_idx]
            g_idx += 1
            for s in range(NUM_SCREENS):
                if phys in available[s]:
                    MAP[s][i] = phys
                    available[s].discard(phys)
    
    # === Шаг 5: ВТОРОЙ ПРОХОД для Класс-1 ===
    # Каждый Класс-1 кастом который НЕ размещён на каком-то экране — подбираем
    # альтернативный phys_tile из per-screen свободных.
    for i, ct in enumerate(custom_table):
        if ct[3] is not None:
            continue
        for s in range(NUM_SCREENS):
            if MAP[s][i] != 0:
                continue
            # Не размещён на экране s. Подбираем любой свободный.
            if not available[s]:
                raise ValueError(
                    f"Кастом #{i} '{ct[0]}' не помещается на экран {s}: "
                    f"нет свободных слотов")
            # Берём минимальный — для стабильности
            phys = min(available[s])
            MAP[s][i] = phys
            available[s].discard(phys)
    
    return MAP


def compute_swap_exceptions(custom_table, MAP, per_screen_used):
    """V1.3: вычисляет таблицу exception'ов — для каждого экрана список
    swap'ов которые нужно сделать при ВХОДЕ на этот экран.

    Exception возникает когда Класс-1 кастому назначен phys_tile, но на
    конкретном экране этот phys_tile УЖЕ ЗАНЯТ фоном. В runtime ASM сохранит
    фоновый тайл в SRAM backup, перезапишет на кастом-CHR; при выходе с
    этого экрана восстановит фон.

    Возвращает dict { screen_idx → [(custom_idx, phys_tile), ...] }.
    
    per_screen_used: список set'ов от analyze_screens (что использовано
                     именно фоном экрана).
    """
    exceptions = {s: [] for s in range(NUM_SCREENS)}
    
    for i, ct in enumerate(custom_table):
        if ct[3] is not None:   # Класс-3 не учитываем (старый prompt_attr_fix)
            continue
        # Phys_tile один на всех (для Класс-1)
        phys = MAP[0][i]
        if phys == 0:
            continue
        # На каких экранах этот phys_tile использует фон?
        for s in range(NUM_SCREENS):
            if phys in per_screen_used[s]:
                exceptions[s].append((i, phys))
    
    return exceptions


def report_assignment(custom_table, MAP, log_fn=log, exceptions=None,
                       free_per_screen=None):
    """Подробный отчёт по назначению с разбивкой по классам.
    
    Показывает реальный физический потолок (= узкое место по экранам),
    а не программные лимиты которые недостижимы.
    """
    n = len(custom_table)

    # Подсчёт по классам
    n_class1_global = 0
    n_class1_swap   = 0
    n_class3        = 0
    for i, ct in enumerate(custom_table):
        screens = ct[3]
        if screens is not None:
            n_class3 += 1
            continue
        phys = MAP[0][i]
        has_exc = False
        if exceptions:
            for s_excs in exceptions.values():
                if any(ce[0] == i for ce in s_excs):
                    has_exc = True
                    break
        if has_exc:
            n_class1_swap += 1
        else:
            n_class1_global += 1

    n_class1_total = n_class1_global + n_class1_swap

    # Реальный остаток = свободные тайлы на самом тесном экране
    # (после уже сделанной раздачи). Это и есть честный потолок для
    # ЛЮБОГО нового кастома (Класс-1 со swap, Класс-2, Класс-3 на screen 13).
    bottleneck_free = None
    bottleneck_screen = None
    if free_per_screen is not None:
        # Сколько на каждом экране уже занято этим распределением
        for s in range(NUM_SCREENS):
            used_on_s = sum(
                1 for i, ct in enumerate(custom_table)
                if MAP[s][i] != 0
            )
            remaining = len(free_per_screen[s]) - used_on_s
            if bottleneck_free is None or remaining < bottleneck_free:
                bottleneck_free = remaining
                bottleneck_screen = s

    log_fn("  multi_alt: распределение кастомов")
    log_fn("  ─────────────────────────────────────────────────────────")
    log_fn(f"    Класс 1 (видны на всех 14 экранах меню):")
    log_fn(f"      без swap (в global-free слотах): {n_class1_global:>3}")
    log_fn(f"      со swap (almost-global-free):    {n_class1_swap:>3}")
    log_fn(f"      ИТОГО Класс 1:                   {n_class1_total:>3}")
    log_fn(f"    Класс 3 (только определённые экраны):")
    log_fn(f"      ИТОГО Класс 3:                   {n_class3:>3}")
    log_fn("  ─────────────────────────────────────────────────────────")
    log_fn(f"    ВСЕГО кастомов:                    {n:>3}")
    if bottleneck_free is not None:
        log_fn(f"    Можно добавить ещё (любого класса): {bottleneck_free:>3}"
               f"   (упор в screen {bottleneck_screen})")
    log_fn("  ───────────────────���─────────────────────────────────────")

    if exceptions:
        max_exc_per_screen = max(len(v) for v in exceptions.values())
        total_exc = sum(len(v) for v in exceptions.values())
        screens_with_swap = sum(1 for v in exceptions.values() if v)
        log_fn(f"    Swap-операции при смене экрана:")
        log_fn(f"      экранов со swap-листом:    {screens_with_swap}")
        log_fn(f"      всего записей в swap:      {total_exc}")
        log_fn(f"      максимум на одном экране:  {max_exc_per_screen}"
               f" ({max_exc_per_screen * 16 * 2} байт PPU traffic)")
    log_fn("  ─────────────────────────────────────────────────────────")


# =============================================================================
# Генерация CHR-данных кастомов (16 байт на каждый)
# =============================================================================

def make_red_tile(src_chr_16):
    """Превращает 16-байтный CHR обычной буквы в red-version.
    Algorithm: out_p0 = in_p0; out_p1 = ~in_p0 & in_p1
    Это даёт пиксели только в позиции 'букв' с цветом 0x02 в палитре."""
    if len(src_chr_16) != 16:
        raise ValueError("CHR tile must be 16 bytes")
    p0 = src_chr_16[0:8]
    p1 = src_chr_16[8:16]
    out_p0 = bytes(p0)
    out_p1 = bytes(((~a) & b) & 0xFF for a, b in zip(p0, p1))
    return out_p0 + out_p1


def get_orig_chr_tile(rom, tile_id):
    """Возвращает 16-байтный CHR-блок из оригинального ROM (PT0)."""
    # CHR в iNES после header + PRG. У "9999 in 1.nes" PRG=128KB, начинается с offset 16
    # CHR начинается с offset 16 + 128*1024 = 131088
    prg_size = rom[4] * 16384
    chr_start = HEADER + prg_size
    return rom[chr_start + tile_id * 16 : chr_start + (tile_id + 1) * 16]


def build_custom_chr_data(custom_table, rom_orig, font_chr_dict=None):
    """Строит блок CHR-данных всех кастомов (16 байт × N штук).

    rom_orig      — байты оригинального "9999 in 1.nes" (для red_orig)
    font_chr_dict — словарь {символ → 16 байт CHR} из font_patch.extract_chr_for_chars.
                    Если None — кастомы типа 'font' заполняются нулями (placeholder).

    Возвращает bytes длиной N * 16.
    """
    out = bytearray()
    for ct in custom_table:
        # backwards compat: 4 или 5 элементов
        name = ct[0]
        kind = ct[1]
        data = ct[2]
        if kind == "font":
            if font_chr_dict is None:
                out.extend(b"\x00" * 16)
            else:
                ch = data.upper()
                if ch in font_chr_dict:
                    out.extend(font_chr_dict[ch])
                else:
                    raise KeyError(
                        f"Кастом '{name}': символ '{data}' не найден "
                        f"в font_chr_dict (нет в [atlas] font_patch.ini?)")
        elif kind == "red_orig":
            src_tile = data
            src_chr  = get_orig_chr_tile(rom_orig, src_tile)
            out.extend(make_red_tile(src_chr))
        elif kind == "raw":
            if len(data) != 16:
                raise ValueError(f"raw kind needs 16 bytes, got {len(data)}")
            out.extend(data)
        else:
            raise ValueError(f"Unknown custom kind: {kind}")
    return bytes(out)


def get_required_font_chars(custom_table):
    """Возвращает множество символов которые нужно прочитать из font.png
    для всех font-кастомов в таблице."""
    return {
        ct[2].upper()
        for ct in custom_table
        if ct[1] == "font"
    }


# =============================================================================
# V1.1: Интеграция в builder.py
# =============================================================================

def get_class1_phys_tile(custom_table, MAP, idx):
    """Возвращает phys_tile для Класс-1 кастома (одинаковый на всех экранах).
    Для Класс-3 возвращает 0."""
    if custom_table[idx][3] is not None:  # Класс 3 (specific screens)
        return 0
    # Класс 1: phys_tile одинаковый на всех экранах (если global-free)
    s0 = MAP[0][idx]
    return s0


def verify_no_class1_swap(custom_table, MAP):
    """V1.5: УСТАРЕЛО. Per-screen MAP теперь штатно даёт разные phys_tile
    на разных экранах. Эта функция возвращает пустой список (всегда OK).
    Сохранена для обратной совместимости."""
    return []


def apply_class1_to_chr(chr_menu_8k, custom_table, MAP, font_chr_dict,
                        chr_table=0):
    """Записывает CHR-данные всех Класс-1 кастомов в chr_menu_8k.

    chr_menu_8k    : bytearray, 8192 байта (PT0+PT1 для меню)
    custom_table   : из build_custom_table()
    MAP            : из assign_slots()
    font_chr_dict  : {символ → 16 байт CHR} из font_patch.extract_chr_for_chars
    chr_table      : 0 = PT0 (offset 0), 1 = PT1 (offset $1000)

    Класс-3 кастомы (red на screen13) НЕ пишутся — они генерируются и
    свопаются runtime через prompt_attr_fix.

    Возвращает dict с инфой:
        'written_count'  : сколько кастомов записано
        'class1_count'   : сколько кастомов Класс-1 (= ожидаемое)
        'phys_tiles_used': set использованных phys_tile
    """
    if len(chr_menu_8k) < 8192:
        raise ValueError(f"chr_menu_8k must be >= 8192 bytes")

    # Проверка корректности — для безопасности
    problems = verify_no_class1_swap(custom_table, MAP)
    if problems:
        raise ValueError(
            f"Класс-1 кастомы требуют swap (не все в global-free):\n" +
            "\n".join(f"  #{i} '{n}' phys_tiles={p}" for i, n, p in problems))

    pt_base = 0x1000 * (chr_table & 1)
    written = 0
    phys_used = set()

    for i, ct in enumerate(custom_table):
        if ct[3] is not None:   # Класс 3 пропускаем
            continue
        name = ct[0]
        kind = ct[1]
        data = ct[2]
        phys = get_class1_phys_tile(custom_table, MAP, i)
        if phys == 0:
            raise ValueError(f"Кастом #{i} '{name}': phys_tile=0 (не назначен)")

        # Получаем 16 байт CHR
        if kind == "font":
            ch = data.upper()
            if ch not in font_chr_dict:
                raise KeyError(
                    f"Кастом '{name}': символ '{data}' не найден в font.png "
                    f"(нет в [atlas] font_patch.ini?)")
            chr_data = font_chr_dict[ch]
        elif kind == "raw":
            chr_data = data
        else:
            raise ValueError(f"Кастом '{name}': kind={kind} не поддерживается "
                             f"в Класс-1 (red_orig только для Класс-3)")

        if len(chr_data) != 16:
            raise ValueError(f"Кастом '{name}': CHR data length {len(chr_data)} != 16")

        ofs = pt_base + phys * 16
        if ofs + 16 > len(chr_menu_8k):
            raise ValueError(f"phys_tile ${phys:02X} вне CHR ($1000)")
        chr_menu_8k[ofs:ofs+16] = chr_data
        written += 1
        phys_used.add(phys)

    return {
        'written_count'  : written,
        'class1_count'   : sum(1 for ct in custom_table if ct[3] is None),
        'phys_tiles_used': phys_used,
    }


def make_chars_map(custom_table, MAP):
    """Возвращает {символ → phys_tile} для set_custom_char_map().

    Используется в utils.encode_orig_menu_char для кодирования кириллицы и
    спецсимволов. Для Класс-1 берётся phys_tile с экрана 0 (одинаковый на
    всех экранах). Для Класс-3 (red на screen13) НЕ добавляется — эти
    тайлы используются явно через PROMPT_TILES_ALT.

    V1.7: kind="raw" тоже учитывается если name — 1 символ (drop-in из tiles/).
    """
    out = {}
    for i, ct in enumerate(custom_table):
        if ct[3] is not None:
            continue   # Класс-3 пропускаем
        name = ct[0]
        kind = ct[1]
        data = ct[2]
        if kind == "font":
            ch = data.upper()
        elif kind == "raw" and isinstance(name, str) and len(name) == 1:
            ch = name.upper()
        else:
            continue
        phys = get_class1_phys_tile(custom_table, MAP, i)
        if phys == 0:
            raise ValueError(f"Кастом '{name}': phys_tile=0")
        out[ch] = phys
    return out


def make_chars_map_virtual(custom_table):
    """V1.4: возвращает {символ → custom_idx} для set_custom_char_map_virtual().
    
    custom_idx — позиция кастома в custom_table (0..N-1). Должна быть < 128
    чтобы $80 | idx был валиден (< $FF, не конфликтовать с пробелом).
    
    Для font-кастомов которые имеют конкретный символ.

    V1.7: kind="raw" тоже учитывается если name — 1 символ (drop-in из tiles/).

    V5.25: Класс-3 раньше полностью пропускался (нужно было PROMPT_TILES_ALT для
    встроенных red-букв). Теперь Класс-3 RAW-тайлы с 1-символьным именем
    (типа T+{1}@13) ВКЛЮЧАЕМ в карту — это пользовательские тэги, которые
    юзер вписывает в имена игр; на целевом экране они отрисовываются как
    нужный тайл, на других — как то, что лежит в этом phys_tile-слоте.
    Class-3 font-кастомы по-прежнему пропускаем (они через PROMPT_TILES_ALT).
    """
    out = {}
    for i, ct in enumerate(custom_table):
        if i >= 0x7F:
            raise ValueError(f"Кастом #{i} '{ct[0]}': custom_idx >= 127, "
                              f"виртуальный индекс не помещается в byte")
        name = ct[0]
        kind = ct[1]
        data = ct[2]
        is_class3 = ct[3] is not None
        if kind == "font":
            if is_class3:
                continue   # font Class-3 (red-letters) — через PROMPT_TILES_ALT
            ch = data.upper()
        elif kind == "raw" and isinstance(name, str) and len(name) == 1:
            ch = name.upper()
        else:
            continue
        out[ch] = i
    return out


def get_initial_phys_table(custom_table, MAP, screen=0):
    """V1.4: возвращает массив 50 байт — phys_tile для каждого custom_idx
    на указанном экране (по умолчанию screen 0).
    Используется для инициализации MA_MAP_RAM.
    
    Для Класс-3 кастомов на экранах где их нет (MAP[s][i] == 0) возвращается 0.
    """
    n = len(custom_table)
    out = bytearray(n)
    for i in range(n):
        out[i] = MAP[screen][i] & 0xFF
    return bytes(out)


# =============================================================================
# V1.3: Build-time данные для swap-механизма
# =============================================================================

# =============================================================================
# V1.5 Шаг 2: Полные таблицы для per-screen swap
# =============================================================================

# V1.5.3: SCREEN_MAP_TABLE использует stride 64 (степень 2 для ASM-расчёта
# `screen * 64` через 6 ASL). А MA_MAP_RAM в RAM = MA_MAP_PER_SCREEN байт.
# При копировании из ROM в RAM берём только первые MA_MAP_PER_SCREEN байт
# каждой строки таблицы.
#
# Stride должен быть степенью 2 и >= MA_MAP_PER_SCREEN. Для MA_MAP_PER_SCREEN=89
# минимальный stride = 128.
MA_MAP_TABLE_STRIDE = 128   # шаг в SCREEN_MAP_TABLE (ROM)
# MA_MAP_PER_SCREEN определён ВЫШЕ в начале файла (= 89).


def build_full_swap_tables(custom_table, MAP, font_chr_dict, rom_orig):
    """V1.5 Шаг 2: строит полный набор данных для runtime swap.
    
    Формат:
        BLOCK_A: SCREEN_MAP_TABLE (14 × MA_MAP_PER_SCREEN байт)
            [s][i] = phys_tile для кастома i на экране s
            (это будущий MA_MAP_RAM для каждого экрана).
        
        BLOCK_B: CUSTOM_CHR_DATA (MA_MAP_PER_SCREEN × 16 байт)
            [i*16..i*16+15] = CHR-глиф кастома i (font, raw, или red_orig).
        
        BLOCK_C: SCREEN_SWAP_LIST (per-screen swap-операции для restore)
            На каждом экране — список phys_tile которые нужно
            ВЕРНУТЬ к фоновому CHR при выходе с экрана.
            Формат записи: count_byte (1) +
                count × (custom_idx (1) + old_phys (1) + new_phys (1) + orig_chr (16))
            = 19 байт на запись + 1 байт count.
        
        BLOCK_D: SWAP_LIST_OFFSETS (14 × 2 = 28 байт, header BLOCK_C)
            offset в BLOCK_C для каждого экрана.
            $FFFF = на этом экране нет swap (s0 — initial state без swap).
    
    SCREEN 0 трактуется как "initial loaded" — на нём CHR кастомов уже
    записаны через apply_class1_to_chr. Поэтому при первом входе на s0
    swap не нужен (LOADED=0xFF означает "fresh").
    
    Возвращает (data_bytes, info_dict, offset_dict).
    """
    NUM = NUM_SCREENS
    n_customs = len(custom_table)
    
    # V1.5.2: Класс-3 кастомы (red на screen13) НЕ нужны в SCREEN_MAP_TABLE/CHR.
    # Они рисуются через PROMPT_TILES_ALT с hard-coded phys_tile, не через
    # виртуальные индексы. Сохраняем их в swap-данных (для swap CHR) но не
    # в основной MAP-таблице.
    n_class1_customs = sum(1 for ct in custom_table if ct[3] is None)
    
    if n_class1_customs > MA_MAP_PER_SCREEN:
        raise ValueError(f"Класс-1 кастомов {n_class1_customs} > {MA_MAP_PER_SCREEN}, "
                          f"увеличьте MA_MAP_PER_SCREEN в multi_alt.py "
                          f"(потолок RAM-буфера ~110 байт, но реальный CHR-предел "
                          f"может быть ниже — определяется самым забитым экраном)")
    
    # ── BLOCK_A: SCREEN_MAP_TABLE (14 × MA_MAP_TABLE_STRIDE) ──
    # ASM использует stride=128 для адресации (screen*128 через 7 ASL/ROL).
    # Реальные данные занимают только первые MA_MAP_PER_SCREEN байт,
    # остальное — padding нулями (не используется ASM).
    map_table = bytearray()
    for s in range(NUM):
        for i in range(MA_MAP_TABLE_STRIDE):
            if i < MA_MAP_PER_SCREEN and i < n_customs and custom_table[i][3] is None:
                # Класс-1 — пишем phys
                map_table.append(MAP[s][i] & 0xFF)
            else:
                # Класс-3 или padding — нули
                map_table.append(0)
    
    # ── BLOCK_B: CUSTOM_CHR_DATA (MA_MAP_TABLE_STRIDE × 16 = 1024 байт) ──
    # Stride=64 потому что ASM индексирует через i*16, и для совместимости с
    # SCREEN_MAP_TABLE (тот же индекс).
    chr_data = bytearray()
    for i in range(MA_MAP_TABLE_STRIDE):
        if i < n_customs:
            ct = custom_table[i]
            kind = ct[1]
            data = ct[2]
            if kind == "font":
                ch = data.upper()
                if ch not in font_chr_dict:
                    raise KeyError(f"Кастом #{i} '{ct[0]}' символ '{data}' не в font_chr_dict")
                chunk = font_chr_dict[ch]
            elif kind == "raw":
                chunk = data
            elif kind == "red_orig":
                src_chr = get_orig_chr_tile(rom_orig, data)
                chunk = make_red_tile(src_chr)
            else:
                raise ValueError(f"Кастом '{ct[0]}': неизвестный kind '{kind}'")
            if len(chunk) != 16:
                raise ValueError(f"Кастом '{ct[0]}': CHR len={len(chunk)} != 16")
            chr_data.extend(chunk)
        else:
            chr_data.extend(b'\x00' * 16)
    
    # ── BLOCK_C/D: SCREEN_SWAP_LIST + offsets ──
    # Формат записи (35 байт):
    #   custom_idx (1)
    #   old_phys (1)        — phys_tile на screen 0 (initial state)
    #   new_phys (1)        — phys_tile на этом экране
    #   orig_chr_old (16)   — что должно быть на old_phys на этом экране (фон)
    #   orig_chr_new (16)   — что должно быть на new_phys в state s0
    #                          (CHR другого кастома или пустой если phys_tile свободен)
    #
    # ASM при входе на экран s:
    #   Для каждой записи:
    #     - Записать orig_chr_old на old_phys (восстановить фон s)
    #     - Записать CUSTOM_CHR[i] на new_phys (поместить кастом на новое место)
    #
    # ASM при выходе с экрана s (откат на s0):
    #   Для каждой записи (в обратном порядке):
    #     - Записать orig_chr_new на new_phys (вернуть state s0)
    #     - Записать CUSTOM_CHR[i] на old_phys (вернуть кастом на screen-0 место)
    
    def get_chr_for_phys_in_screen(phys_tile, screen):
        """V1.8: возвращает CHR который ДОЛЖЕН быть на phys_tile на экране `screen`.
        
        Логика:
          - Если на этом экране phys_tile назначен кастому j → CHR кастома j
          - Иначе → оригинальный CHR из 9999 in 1
        
        Использование:
          - orig_chr_old для swap-записей на экране s
          - orig_chr_new (state s0) — через get_orig_chr_for_phys_in_s0_state
        """
        for j, jct in enumerate(custom_table):
            if MAP[screen][j] == phys_tile:
                jkind = jct[1]
                jdata = jct[2]
                if jkind == "font":
                    jch = jdata.upper()
                    return font_chr_dict[jch]
                elif jkind == "raw":
                    return jdata
                elif jkind == "red_orig":
                    return make_red_tile(get_orig_chr_tile(rom_orig, jdata))
        return get_orig_chr_tile(rom_orig, phys_tile)
    
    def get_orig_chr_for_phys_in_s0_state(phys_tile):
        """V1.5.1: shorthand для get_chr_for_phys_in_screen(phys_tile, 0)."""
        return get_chr_for_phys_in_screen(phys_tile, 0)
    
    swap_lists = bytearray()
    swap_offsets = [0xFFFF] * NUM
    swap_offsets[0] = 0xFFFF
    
    for s in range(1, NUM):
        changes = []
        for i in range(n_customs):
            old_phys = MAP[0][i]
            new_phys = MAP[s][i]
            
            if new_phys == 0:
                continue  # на s этого кастома нет
            if old_phys == new_phys:
                continue  # phys_tile одинаковый — swap не нужен
            
            changes.append((i, old_phys, new_phys))
        
        if not changes:
            continue
        
        swap_offsets[s] = len(swap_lists)
        swap_lists.append(len(changes) & 0xFF)
        for ci, old_phys, new_phys in changes:
            # orig_chr_old — что ДОЛЖНО быть на old_phys на экране s.
            # V1.8: учитываем что old_phys может быть занят другим кастомом
            # на экране s (например Class-3 P_red на $2C при ♥ Class-1 на $2C s0).
            # Раньше брали rom_orig[old_phys] → перезатирали Class-3.
            if old_phys != 0:
                orig_chr_old = get_chr_for_phys_in_screen(old_phys, s)
            else:
                # На s0 этого кастома не было — old_phys=0, не используется
                orig_chr_old = b'\x00' * 16
            # orig_chr_new — state s0 на new_phys
            orig_chr_new = get_orig_chr_for_phys_in_s0_state(new_phys)
            
            swap_lists.append(ci)
            swap_lists.append(old_phys)
            swap_lists.append(new_phys)
            swap_lists.extend(orig_chr_old)
            swap_lists.extend(orig_chr_new)
    
    header = bytearray()
    for s in range(NUM):
        ofs = swap_offsets[s]
        header.append(ofs & 0xFF)
        header.append((ofs >> 8) & 0xFF)
    
    # V1.9: bank1_data — map+chr+header → service bank (фикс. размер).
    #       bank2_data — swap_lists → data bank 2 (переменный размер).
    # Каждый кладётся отдельно в свой банк (см. builder.py).
    bank1_data = bytes(map_table) + bytes(chr_data) + bytes(header)
    bank2_data = bytes(swap_lists)
    
    # full_data сохраняем для обратной совместимости (если код снаружи
    # ожидает единый блок). Старые offsets теперь невалидны при размещении
    # в разных банках — используй split_offsets для bank1/bank2.
    full_data = bank1_data + bank2_data
    
    offsets = {
        # Bank 1 offsets (относительно service-bank базы):
        'screen_map'  : 0,
        'custom_chr'  : len(map_table),
        'swap_header' : len(map_table) + len(chr_data),
        # Bank 2 offset для swap_lists (относительно data-bank базы = 0):
        'swap_lists'  : 0,
        # Backwards-compat: оригинальный offset как в монолитной раскладке
        '_legacy_swap_lists' : len(map_table) + len(chr_data) + len(header),
    }
    
    info = {
        'screen_map_size'    : len(map_table),
        'custom_chr_size'    : len(chr_data),
        'swap_header_size'   : len(header),
        'swap_lists_size'    : len(swap_lists),
        'bank1_size'         : len(bank1_data),
        'bank2_size'         : len(bank2_data),
        'total_size'         : len(full_data),
        'screens_with_swap'  : sum(1 for o in swap_offsets if o != 0xFFFF),
        'max_swap_per_screen': max(
            (swap_lists[swap_offsets[s]] if swap_offsets[s] != 0xFFFF else 0)
            for s in range(NUM)
        ) if swap_lists else 0,
    }
    
    # V1.9: возвращаем bank1_data и bank2_data отдельно.
    # Старая сигнатура (full_data, info, offsets) больше не используется —
    # builder ожидает 4-tuple (bank1, bank2, info, offsets).
    return bank1_data, bank2_data, info, offsets


# =============================================================================
# V1.3: Старая build_swap_tables — оставляем для совместимости (Шаг 1)
# =============================================================================

def build_swap_tables(custom_table, MAP, exceptions, font_chr_dict, rom_orig):
    """Строит данные для swap-механизма (записываются в ROM, читаются ASM).

    КЛЮЧЕВОЙ ПРИНЦИП:
    - Базовый PT0 содержит CHR КАСТОМОВ (записывается apply_class1_to_chr).
    - На большинстве экранов это корректно (фон не использует эти phys_tile).
    - На экранах-исключениях (s2, s8 в нашем случае) фон РЕАЛЬНО использует
      эти phys_tile — поэтому при входе на такой экран ASM swap'ит:
        backup текущий CHR (= кастом из base PT0) → сохранить в SRAM
        write оригинальный фоновый CHR (из payload) → нужный фон
      При выходе ASM восстанавливает из SRAM backup кастом-CHR.

    Формат данных (плоский bytes-блок):

        SWAP_HEADER (NUM_SCREENS * 2 = 28 байт):
            screen_idx → 16-bit offset в SWAP_PAYLOAD от начала payload
            (0xFFFF = на этом экране swap не нужен)

        SWAP_PAYLOAD (концатенация данных по экранам):
            Для каждого экрана с exception'ами:
                count_byte (1 байт): количество swap-слотов
                Для каждого слота:
                    phys_tile (1 байт)
                    orig_chr  (16 байт) — оригинальный CHR ФОНА на этом
                                          экране для этого phys_tile
                                          (т.е. что было в исходном "9999 in 1")

    Возвращает: (swap_data_bytes, info_dict)
    """
    NUM = NUM_SCREENS
    
    # Сначала собираем payload и offsets
    payload = bytearray()
    offsets = [0xFFFF] * NUM
    max_per_screen = 0
    
    for s in range(NUM):
        excs = exceptions.get(s, [])
        if not excs:
            continue
        if len(excs) > max_per_screen:
            max_per_screen = len(excs)
        offsets[s] = len(payload)
        payload.append(len(excs))
        for ci, phys in excs:
            # Для exception нам нужен ОРИГИНАЛЬНЫЙ CHR фона на этом phys_tile.
            # Берём из исходного ROM "9999 in 1.nes" — там нет наших кастомов,
            # PT0 содержит фоновую графику меню.
            orig_chr = get_orig_chr_tile(rom_orig, phys)
            if len(orig_chr) != 16:
                raise ValueError(f"orig CHR for tile ${phys:02X} len={len(orig_chr)}")
            
            payload.append(phys)
            payload.extend(orig_chr)
    
    # Header: 14 × 2 байта (lo, hi)
    header = bytearray()
    for s in range(NUM):
        ofs = offsets[s]
        header.append(ofs & 0xFF)
        header.append((ofs >> 8) & 0xFF)
    
    swap_data = bytes(header) + bytes(payload)
    
    info = {
        'header_size'      : len(header),
        'payload_size'     : len(payload),
        'total_size'       : len(swap_data),
        'screens_with_swap': sum(1 for o in offsets if o != 0xFFFF),
        'max_per_screen'   : max_per_screen,
        'total_exceptions' : sum(len(v) for v in exceptions.values()),
    }
    
    return swap_data, info


# =============================================================================
# V1.3: ASM-функция для runtime swap (в service bank)
# =============================================================================

def build_initial_ma_map_data(initial_phys_table):
    """V1.4 Шаг 1: возвращает MA_MAP_PER_SCREEN байт начальных phys_tile.
    
    initial_phys_table: bytes длины <= MA_MAP_PER_SCREEN.
    Если меньше — добивается нулями.
    
    V1.5.3: используется только если SCREEN_MAP_TABLE отсутствует. Когда
    SCREEN_MAP_TABLE есть, init_ma_map_ram копирует первые MA_MAP_PER_SCREEN
    байт прямо из неё (для screen 0).
    """
    out = bytearray(MA_MAP_PER_SCREEN)
    n = min(len(initial_phys_table), MA_MAP_PER_SCREEN)
    out[:n] = initial_phys_table[:n]
    return bytes(out)


def build_init_ma_map_ram_asm(origin_cpu, data_cpu):
    """V1.4 Шаг 1: генерирует ASM функцию `init_ma_map_ram`
    которая копирует MA_MAP_PER_SCREEN байт начальной таблицы из data_cpu в MA_MAP_RAM.
    Вызывается из reset_init и menu_init_wrapper.
    
    V1.5.3: data_cpu это начало SCREEN_MAP_TABLE для screen 0 (первые 128 байт),
    но мы копируем только MA_MAP_PER_SCREEN (соответствует MA_MAP_RAM_SIZE).
    """
    src = f"""
init_ma_map_ram:
    ldx #$00
imr_loop:
    lda ${data_cpu:04X},x
    sta ${MA_MAP_RAM:04X},x
    inx
    cpx #${MA_MAP_PER_SCREEN:02X}
    bne imr_loop
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_ma_translate_asm(origin_cpu):
    """V1.4 Шаг 1: helper-процедура подмены virtual byte → phys_tile.
    
    Вход:  A = byte (если bit7=0, обычный phys_tile; если bit7=1, custom_idx)
    Выход: A = phys_tile (для bit7=0 — без изменений; для bit7=1 — из MA_MAP_RAM)
    Не разрушает X, Y.
    
    Размер ~14 байт. Используется ZP $5A как scratch.
    """
    src = f"""
ma_translate:
    bpl mat_done            ; bit7=0 → A уже phys, выход
    sty $5A                 ; сохраняем Y
    and #$7F
    tay
    lda ${MA_MAP_RAM:04X},y
    ldy $5A
mat_done:
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_svc_multi_alt_swap(origin_cpu,
                              screen_map_cpu, custom_chr_cpu,
                              swap_header_cpu, swap_lists_cpu,
                              db_read_count_cpu, db_read_header_cpu,
                              db_stream16_cpu):
    """V1.9: ASM функция полного per-screen swap с разделённым хранением:
    map+chr+header в service bank 1, swap_lists — в data bank 2.
    
    Параметры (CPU адреса):
      screen_map_cpu  — SCREEN_MAP_TABLE в service bank
      custom_chr_cpu  — CUSTOM_CHR_DATA в service bank
      swap_header_cpu — SWAP_HEADER в service bank
      swap_lists_cpu  — SWAP_LISTS в data bank ($8000 при включённом bank 2)
      db_read_count_cpu  — helper в fixed bank: читает count из data bank
      db_read_header_cpu — helper в fixed bank: читает 3-байт header
      db_stream16_cpu    — helper в fixed bank: стримит 16 байт в PPU $2007
    
    Все чтения из swap_lists идут через helper'ы (они переключают
    $5005 на data bank и обратно). Чтения screen_map / custom_chr /
    swap_header остаются inline — эти данные в service bank.
    
    Контракт:
      Вход: A = новый screen (0..13). >= 14 = unload (откат на s0).
    
    Запись (35 байт):
      [0]: custom_idx
      [1]: old_phys
      [2]: new_phys
      [3..18]:  orig_chr_old (16)
      [19..34]: orig_chr_new (16)
    """
    HEADER = swap_header_cpu
    LISTS  = swap_lists_cpu
    SMT    = screen_map_cpu
    CHR    = custom_chr_cpu
    DB_RC  = db_read_count_cpu
    DB_RH  = db_read_header_cpu
    DB_S16 = db_stream16_cpu
    
    src = f"""
svc_multi_alt_swap:
    sta $5A                  ; new_screen в $5A
    
    lda ${MA_LOADED_SCREEN:04X}
    cmp $5A
    bne mas_changed
    rts                      ; экран не сменился

mas_changed:
    ; Отключим PPU render
    lda $2002
    lda #$00
    sta $2001
    
    ; SRAM + CHR write
    lda ${CFG5007:04X}
    ora #$03
    sta $5007
    
    ; ── Шаг 1: откат старого экрана на s0 (если нужен) ──
    lda ${MA_LOADED_SCREEN:04X}
    cmp #$FF
    bne mas_chk_zero
    jmp mas_no_unload
mas_chk_zero:
    cmp #$00
    bne mas_chk_inv
    jmp mas_no_unload
mas_chk_inv:
    cmp #$0E
    bcc mas_unload_proceed
    jmp mas_no_unload
mas_unload_proceed:
    ; lookup SWAP_HEADER[LOADED]
    asl a
    tay
    lda ${HEADER:04X},y
    sta $5C
    iny
    lda ${HEADER:04X},y
    sta $5D
    
    ; FFFF? тогда откат не нужен
    cmp #$FF
    bne mas_unload_have
    lda $5C
    cmp #$FF
    bne mas_unload_have
    jmp mas_no_unload

mas_unload_have:
    ; ($5C:$5D) = offset → addr = LISTS + offset (LISTS в data bank, $8000+)
    clc
    lda $5C
    adc #${LISTS & 0xFF:02X}
    sta $5C
    lda $5D
    adc #${(LISTS >> 8) & 0xFF:02X}
    sta $5D
    
    ; V1.9: читаем count из data bank через helper.
    ; helper переключает $5005 на bank 2, читает в $5B, возвращает $5005 на bank 1,
    ; и продвигает $5C/$5D на +1.
    jsr ${DB_RC:04X}

mas_unl_step:
    lda $5B
    bne mas_unl_do
    jmp mas_no_unload
mas_unl_do:
    
    ; V1.9: читаем 3-байт header (ci/old/new) из data bank через helper.
    ; На выходе Y=3 (готов для чтения orig_chr_old без оверхеда).
    jsr ${DB_RH:04X}
    
    ; orig_chr_new → new_phys (offset 19..34)
    bit $2002
    lda $62
    lsr a
    lsr a
    lsr a
    lsr a
    sta $2006
    lda $62
    asl a
    asl a
    asl a
    asl a
    sta $2006
    
    ; V1.9: стрим 16 байт orig_chr_new из ($5C),Y=19 в PPU через helper.
    ; X=$10 счётчик (экономия 2 байт SAFE-2 на хелпере).
    ldx #$10
    ldy #19
    jsr ${DB_S16:04X}        ; Y на выходе = 35
    
    ; CUSTOM_CHR[i] → old_phys (если old_phys != 0)
    lda $61
    beq mas_unl_skip
    
    bit $2002
    lda $61
    lsr a
    lsr a
    lsr a
    lsr a
    sta $2006
    lda $61
    asl a
    asl a
    asl a
    asl a
    sta $2006
    
    ; ($63:$64) = CHR + i*16
    lda $60
    and #$0F
    asl a
    asl a
    asl a
    asl a
    sta $63
    lda $60
    lsr a
    lsr a
    lsr a
    lsr a
    sta $64
    clc
    lda $63
    adc #${CHR & 0xFF:02X}
    sta $63
    lda $64
    adc #${(CHR >> 8) & 0xFF:02X}
    sta $64
    
    ldy #$00
mas_unl_wcust:
    lda ($63),y
    sta $2007
    iny
    cpy #$10
    bne mas_unl_wcust
    
mas_unl_skip:
    clc
    lda $5C
    adc #35
    sta $5C
    lda $5D
    adc #$00
    sta $5D
    dec $5B
    jmp mas_unl_step

mas_no_unload:
    ; ── Шаг 2: загрузка нового экрана ──
    lda $5A
    cmp #$0E
    bcc mas_load_chk
    lda #$FF
    sta ${MA_LOADED_SCREEN:04X}
    lda #$00
    sta $06E7              ; ALT_ACTIVE_FLAG=0 (unload)
    jmp mas_finish

mas_load_chk:
    sta ${MA_LOADED_SCREEN:04X}
    
    ; V5.23 Шаг 2.1: ALT_ACTIVE_FLAG=1 если screen=13 (для prompt_show ветки ALT)
    cmp #$0D
    bne mas_alt_off
    lda #$01
    sta $06E7
    jmp mas_alt_done
mas_alt_off:
    lda #$00
    sta $06E7
mas_alt_done:
    
    ; копируем SCREEN_MAP[A] → MA_MAP_RAM
    ; A = screen, нужно умножить на MA_MAP_TABLE_STRIDE (128) для смещения
    ; в SCREEN_MAP_TABLE. 7 ASL/ROL = ×128.
    lda $5A
    sta $5C
    lda #$00
    sta $5D
    asl $5C
    rol $5D
    asl $5C
    rol $5D
    asl $5C
    rol $5D
    asl $5C
    rol $5D
    asl $5C
    rol $5D
    asl $5C
    rol $5D
    asl $5C
    rol $5D
    clc
    lda $5C
    adc #${SMT & 0xFF:02X}
    sta $5C
    lda $5D
    adc #${(SMT >> 8) & 0xFF:02X}
    sta $5D
    
    ldy #$00
mas_cpmap:
    lda ($5C),y
    sta ${MA_MAP_RAM:04X},y
    iny
    cpy #${MA_MAP_PER_SCREEN:02X}
    bne mas_cpmap
    
    ; lookup SWAP_HEADER[A]
    lda $5A
    asl a
    tay
    lda ${HEADER:04X},y
    sta $5C
    iny
    lda ${HEADER:04X},y
    sta $5D
    cmp #$FF
    bne mas_load_have
    lda $5C
    cmp #$FF
    bne mas_load_have
    jmp mas_finish

mas_load_have:
    clc
    lda $5C
    adc #${LISTS & 0xFF:02X}
    sta $5C
    lda $5D
    adc #${(LISTS >> 8) & 0xFF:02X}
    sta $5D
    
    ; V1.9: читаем count из data bank.
    jsr ${DB_RC:04X}

mas_ld_step:
    lda $5B
    bne mas_ld_do
    jmp mas_finish
mas_ld_do:
    
    ; V1.9: читаем 3-байт header из data bank.
    jsr ${DB_RH:04X}
    
    ; orig_chr_old → old_phys (если old_phys != 0)
    lda $61
    beq mas_ld_skip_old
    
    bit $2002
    lda $61
    lsr a
    lsr a
    lsr a
    lsr a
    sta $2006
    lda $61
    asl a
    asl a
    asl a
    asl a
    sta $2006
    
    ; V1.9: стрим 16 байт orig_chr_old из ($5C),Y=3 в PPU через helper.
    ; X=$10 счётчик.
    ldx #$10
    ldy #$03
    jsr ${DB_S16:04X}        ; Y на выходе = 19

mas_ld_skip_old:
    ; CUSTOM_CHR[i] → new_phys
    bit $2002
    lda $62
    lsr a
    lsr a
    lsr a
    lsr a
    sta $2006
    lda $62
    asl a
    asl a
    asl a
    asl a
    sta $2006
    
    ; ($63:$64) = CHR + i*16
    lda $60
    and #$0F
    asl a
    asl a
    asl a
    asl a
    sta $63
    lda $60
    lsr a
    lsr a
    lsr a
    lsr a
    sta $64
    clc
    lda $63
    adc #${CHR & 0xFF:02X}
    sta $63
    lda $64
    adc #${(CHR >> 8) & 0xFF:02X}
    sta $64
    
    ldy #$00
mas_ld_wcust:
    lda ($63),y
    sta $2007
    iny
    cpy #$10
    bne mas_ld_wcust
    
    clc
    lda $5C
    adc #35
    sta $5C
    lda $5D
    adc #$00
    sta $5D
    dec $5B
    jmp mas_ld_step

mas_finish:
    ; PPU scroll reset
    lda $2002
    lda #$00
    sta $2005
    sta $2005
    
    ; restore $5007
    lda ${CFG5007:04X}
    sta $5007
    rts
"""
    return assemble_6502(src, origin_cpu)


# =============================================================================
# V1.2: Загрузка пользовательских кастомов из multi_alt.ini
# =============================================================================

# =============================================================================
# V1.7: scan_tiles_dir — drop-in регистрация кастомов через папку tiles/
# =============================================================================

def _png_to_chr_tile(path, color_cfg=None):
    """Читает PNG, берёт первый 8×8 тайл (top-left), конвертирует в 16 байт CHR.

    V5.26: если передан color_cfg (FontPatchConfig из font_patch.ini), тайл
    интерпретируется ТОЧНО так же, как тайлы из font.png — 3 цвета + прозрачный:
      - alpha == 0 (прозрачный)         → background (0)
      - white  (по [colors] white)      → ink        (3)
      - black  (по [colors] black)      → background (0)
      - empty/marker (по [colors] empty)→ empty       (2)
      - неизвестный цвет                → empty(0) если unknown_as_empty иначе ошибка
    Это позволяет рисовать кастомные тайлы в том же дизайне/палитре, что и font.png.

    Если color_cfg не передан — простая ч/б логика (обратная совместимость):
      - alpha < 128                  → background (0)
      - luminance < 128 и alpha>=128 → foreground (3)
      - иначе                        → background (0)
    """
    # Импорт ленивый — font_patch может быть недоступен в standalone-режиме
    from font_patch import read_png_rgba, tile8_to_chr
    w, h, pixels = read_png_rgba(path)
    if w < 8 or h < 8:
        raise ValueError(f"размер {w}x{h} — нужно минимум 8x8")

    # V5.26: палитра font.png (3 цвета + прозрачный) — переиспользуем ту же
    # классификацию цветов, что и для атласа font.png.
    if color_cfg is not None:
        from font_patch import (rgba_tile_to_indexed, extract_src_tile_rgba,
                                 _most_common_rgb)
        auto_rgb = _most_common_rgb(pixels)
        empty_resolved = [auto_rgb if ec[0] < 0 else ec
                          for ec in color_cfg.empty_colors]
        tile_rgba = extract_src_tile_rgba(pixels, 0, 0)
        tile_idx = rgba_tile_to_indexed(
            tile_rgba, empty_resolved,
            color_cfg.white, color_cfg.black,
            color_cfg.empty_tol, color_cfg.white_tol, color_cfg.black_tol,
            color_cfg.unknown_as_empty)
        return tile8_to_chr(tile_idx)

    # Fallback: простая ч/б логика.
    tile_idx = [[0]*8 for _ in range(8)]
    for y in range(8):
        for x in range(8):
            r, g, b, a = pixels[y][x]
            lum = (r*299 + g*587 + b*114) // 1000
            tile_idx[y][x] = 3 if (a >= 128 and lum < 128) else 0
    return tile8_to_chr(tile_idx)


def _parse_tile_filename(stem):
    """Парсит имя файла без расширения в (char, screens).

    Форматы:
      'X'           → char='X', screens=None (все экраны)
      'U+XXXX'      → char=chr(0xXXXX), screens=None
      'T+{tag}'     → char=PUA-symbol для тэга 'tag' (см. utils.register_tag)
      'X@13'        → char='X', screens={13}
      'X@5-10'      → screens=range(5,11)
      'X@0,5,7'     → screens={0,5,7}
      'X@all'       → screens=None
      'T+{1}@13'    → тэг {1}, только screen 13

    Тэги типа T+{1} в CSV будут заменяться на этот тайл через
    utils.expand_tags() перед per-char-кодированием.
    """
    # V5.25: T+{tag}[@screens] — пользовательский тэг.
    if stem.startswith("T+"):
        rest = stem[2:]
        if not rest.startswith("{"):
            raise ValueError(f"T+ должен быть в виде T+{{...}}: '{stem}'")
        # Найти ПЕРВУЮ закрывающую скобку (не поддерживаем вложенные {})
        end = rest.find("}")
        if end < 0:
            raise ValueError(f"T+: не найдена закрывающая }} в '{stem}'")
        tag = rest[:end + 1]  # вместе со скобками: '{1}', '{flag}', ...
        suffix = rest[end + 1:]
        if suffix:
            if not suffix.startswith("@"):
                raise ValueError(
                    f"T+: после }} должен быть @ или конец имени: '{stem}'")
            scr_str = suffix[1:]
        else:
            scr_str = None
        from utils import register_tag
        ch = register_tag(tag)
        screens = _parse_screens_set(scr_str) if scr_str else None
        return ch, screens

    if "@" in stem:
        base, scr_str = stem.rsplit("@", 1)
    else:
        base, scr_str = stem, None

    base = base.strip()
    if not base:
        raise ValueError("пустое имя")

    # Resolve char from base
    if base.upper().startswith("U+"):
        try:
            cp = int(base[2:], 16)
        except ValueError:
            raise ValueError(f"некорректный U+ код в '{base}'")
        ch = chr(cp)
    else:
        if len(base) != 1:
            raise ValueError(
                f"'{base}' — имя должно быть 1 символом или U+XXXX")
        ch = base

    screens = _parse_screens_set(scr_str) if scr_str else None
    return ch, screens


def scan_tiles_dir(tiles_dir, font_ini=None):
    """Сканирует папку tiles/ и возвращает список кастомов.

    Принимает в работу файлы:
      *.png             — 8×8 чёрно-белый, авто-конвертируется в CHR
      *.chr / *.bin     — сырые 16 байт CHR

    Имена файлов:
      ★.png             → ★, screens=all
      U+2665.png        → ♥ (codepoint 2665), screens=all
      ★@13.png          → ★, только screen 13
      ★@5-10.png        → screens 5..10
      ★@0,5,7.png       → screens {0,5,7}
      ★@all.png         → screens=all (явная форма)

    Возвращает список 5-tuple (name, "raw", 16-byte CHR, screens_set, None).
    name = символ (ct[0]) — нужно чтобы make_chars_map увидел кастом.
    """
    customs = []
    if not os.path.isdir(tiles_dir):
        return customs

    # V5.26: цвета PNG берём из font_patch.ini, чтобы тайлы можно было рисовать
    # в той же палитре, что и font.png (3 цвета + прозрачный). Если ini нет или
    # не читается — откатываемся на простой ч/б режим.
    color_cfg = None
    if font_ini and os.path.exists(font_ini):
        try:
            from font_patch import load_font_patch_ini
            color_cfg = load_font_patch_ini(font_ini)
            log("  tiles/: палитра из font_patch.ini (3 цвета + прозрачный, "
                "как font.png)")
        except Exception as e:
            log(f"  ⚠️ tiles/: не удалось взять цвета из font_patch.ini "
                f"({e}); используется ч/б режим")

    SUPPORTED = (".png", ".chr", ".bin")
    files = sorted(os.listdir(tiles_dir))

    for fname in files:
        path = os.path.join(tiles_dir, fname)
        if not os.path.isfile(path):
            continue
        stem, ext = os.path.splitext(fname)
        ext = ext.lower()
        if ext not in SUPPORTED:
            continue

        # README.* и подобные служебные файлы пропускаем
        if stem.lower() in ("readme", "_readme"):
            continue

        try:
            ch, screens = _parse_tile_filename(stem)
        except ValueError as e:
            log(f"  ⚠️ tiles/{fname}: пропуск ({e})")
            continue

        try:
            if ext == ".png":
                chr_data = _png_to_chr_tile(path, color_cfg)
            else:
                with open(path, "rb") as f:
                    chr_data = f.read()
                if len(chr_data) != 16:
                    log(f"  ⚠️ tiles/{fname}: должен быть 16 байт, "
                        f"получено {len(chr_data)} — пропуск")
                    continue
        except Exception as e:
            log(f"  ⚠️ tiles/{fname}: ошибка чтения ({e}) — пропуск")
            continue

        customs.append((ch, "raw", chr_data, screens, None))
        scr_str = "all" if screens is None else (
                  f"s{sorted(screens)[0]}" if len(screens) == 1
                  else f"screens={sorted(screens)}")
        # V5.25: для PUA-тэгов показываем сам тэг вместо невидимого символа.
        from utils import get_tag_for_char
        tag = get_tag_for_char(ch)
        label = f"тэг {tag}" if tag else f"'{ch}'"
        log(f"  tiles/{fname} → {label} (U+{ord(ch):04X}, {scr_str})")

    return customs


# =============================================================================

def _parse_multi_alt_ini(path):
    """Минимальный INI-парсер для multi_alt.ini.
    Возвращает список словарей с ключами секций.
    """
    if not os.path.exists(path):
        return []

    sections = []
    current = None

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\r\n").strip()
            if not line or line.startswith(";") or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = {"_section": line[1:-1].strip()}
                sections.append(current)
                continue
            if current is None:
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            # inline comment после ;
            if ";" in v:
                v = v[:v.index(";")].strip()
            current[k] = v

    return sections


def _parse_int_hex(s):
    s = s.strip()
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.lower().startswith("0x"):
        return int(s[2:], 16)
    return int(s, 16) if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) <= 4 else int(s, 10)


def _parse_screens_set(s):
    """Парсит 'all', '13', '0,1,2', '5-10' в set или None."""
    s = s.strip().lower()
    if s in ("", "all", "*"):
        return None
    out = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out if out else None


def load_user_customs(ini_path):
    """Загружает дополнительные кастомы из multi_alt.ini.
    
    Возвращает список 5-tuple (name, kind, data, screens, preferred_phys)
    готовый к добавлению в build_custom_table().

    Формат INI:

        [custom]
        name = MY_TILE
        kind = font     ; font | raw | red_orig
        char = ©        ; для kind=font
        ; src_file = mytile.bin   ; для kind=raw (16 байт)
        ; src_tile = $0B          ; для kind=red_orig
        screens = all   ; all | 13 | 0,1,2 | 5-10
        preferred = $D0 ; необязательный hex слот

        [custom]
        name = COPYRIGHT
        kind = raw
        src_file = copyright.bin
        screens = all
    
    Если файл не существует — возвращает пустой список (это не ошибка).
    """
    sections = _parse_multi_alt_ini(ini_path)
    customs = []

    for sec in sections:
        if sec.get("_section", "").lower() != "custom":
            continue

        name = sec.get("name", f"USR{len(customs)}")
        kind = sec.get("kind", "font").lower()
        screens = _parse_screens_set(sec.get("screens", "all"))
        pref_str = sec.get("preferred", "").strip()
        pref = _parse_int_hex(pref_str) if pref_str else None

        if kind == "font":
            ch = sec.get("char", "")
            if not ch:
                raise ValueError(f"Кастом '{name}': kind=font требует 'char'")
            data = ch
        elif kind == "raw":
            src_file = sec.get("src_file", "").strip()
            if not src_file:
                raise ValueError(f"Кастом '{name}': kind=raw требует 'src_file'")
            full_path = src_file
            if not os.path.isabs(full_path):
                full_path = os.path.join(os.path.dirname(ini_path), src_file)
            if not os.path.exists(full_path):
                raise FileNotFoundError(f"Кастом '{name}': src_file не найден: {full_path}")
            with open(full_path, "rb") as f:
                data = f.read()
            if len(data) != 16:
                raise ValueError(f"Кастом '{name}': src_file должен быть 16 байт, "
                                  f"получено {len(data)}")
        elif kind == "red_orig":
            src_str = sec.get("src_tile", "").strip()
            if not src_str:
                raise ValueError(f"Кастом '{name}': kind=red_orig требует 'src_tile'")
            data = _parse_int_hex(src_str)
        else:
            raise ValueError(f"Кастом '{name}': неизвестный kind '{kind}'")

        customs.append((name, kind, data, screens, pref))

    return customs


# =============================================================================
# V1.6 (V5.9): автогенерация шаблона multi_alt.ini при первом запуске
# =============================================================================

_MULTI_ALT_INI_TEMPLATE = """; ============================================================================
; multi_alt.ini — пользовательские кастомные тайлы (расширенный формат)
; ============================================================================
;
; Этот файл создан автоматически при первой сборке.
; Здесь регистрируешь СВОИ символы/иконки сверх базовой таблицы (33 кириллицы
; + & + 15 спецов + 9 red на screen 13 = 57 кастомов).
;
; ----------------------------------------------------------------------------
;  💡 ПРОСТОЙ СПОСОБ (V5.24+): папка tiles/
; ----------------------------------------------------------------------------
;
; В большинстве случаев INI редактировать НЕ НУЖНО. Просто положи 8×8 PNG
; в папку tiles/ — он автоматически станет кастомным тайлом:
;
;   tiles/★.png             →  символ ★ на всех экранах
;   tiles/U+2665.png        →  символ ♥ (для имён несовместимых с ФС)
;   tiles/★@13.png          →  только screen 13
;   tiles/★@5-10.png        →  экраны 5..10
;
; См. tiles/README.md для подробностей.
;
; multi_alt.ini нужен только если хочешь:
;   - использовать font.png + atlas в font_patch.ini (kind=font),
;   - сгенерировать red-версию существующей буквы (kind=red_orig),
;   - задать preferred phys_tile вручную.
;
; ============================================================================
;  СПРАВКА ПО АРХИТЕКТУРЕ
; ============================================================================
;
; КАК В МЕНЮ ОТРИСОВЫВАЕТСЯ КАСТОМ:
;   1. В [atlas] секции font_patch.ini нарисованный тайл подписан символом X
;      (например 'А', '★', '#').
;   2. Здесь в multi_alt.ini ты регистрируешь этот символ как [custom] с
;      char = X.
;   3. В имени игры (games.list / games.csv) пишешь символ X как обычно.
;   4. При сборке Python заменит каждое X в имени на спец-байт ($80|idx),
;      который ROM-меню runtime превратит в правильный phys_tile.
;
; КЛАССЫ КАСТОМОВ (важно понимать перед добавлением):
;   - Класс 1 (screens=all): тайл виден на ВСЕХ 14 экранах меню.
;     Это нормальные символы для имён игр (буквы, знаки препинания, цифры).
;     Лимит: до 89 штук всего (физика — самый забитый экран меню,
;     screen 13, даёт только 89 свободных CHR-слотов после исключения
;     базового шрифта $01..$26).
;   - Класс 3 (screens=13): тайл нужен ТОЛЬКО на screen 13 (экран помощи).
;     Подходит для red-версий букв в prompt ("PRESS A FOR OPTIONS"), для
;     иконок/декора который нужен только в одном месте.
;     Берёт ОДИН слот из общего пула 89.
;
; ВАЖНЫЕ ОГРАНИЧЕНИЯ:
;   - Жёсткий потолок: 89 кастомов всего (Класс-1 + Класс-2 + Класс-3
;     вместе). Этот предел диктуется фоном экранов оригинального меню
;     "9999 in 1" — больше CHR-слотов на самом тесном экране нет.
;   - Реально в базовой комплектации уже занято 57 (48 Класс-1 + 9 Класс-3),
;     то есть свободно ~32 слота под пользовательские кастомы.
;   - Не каждый символ можно сделать Класс-1: на разных экранах меню разный
;     набор свободных CHR-слотов. Если символу негде "приземлиться" сразу
;     на всех 14 экранах — билд упадёт с ошибкой "не помещается на экран X".
;     Тогда либо: (a) укажи preferred=$XX вручную, (b) сделай screens=13.
;
; ============================================================================
;  ФОРМАТ СЕКЦИИ [custom]
; ============================================================================
;
; [custom]
; name      = STAR             ; уникальное имя (любое, для отладки в логе)
; kind      = font             ; font | raw | red_orig
; char      = ★                ; для kind=font: символ из [atlas] font_patch.ini
; src_file  = mytile.bin       ; для kind=raw: 16 байт CHR (binary файл)
; src_tile  = $0B              ; для kind=red_orig: phys_tile исходной буквы
; screens   = all              ; all | 13 | 0,1,2 | 5-10 (по умолчанию all)
; preferred = $D0              ; (опционально) предпочтительный phys_tile
;
; KIND'Ы:
;   font     — обычный тайл из font.png (atlas в font_patch.ini).
;              Самый частый случай — для добавления символов и иконок.
;   raw      — сырые 16 байт CHR из бинарного файла (для тех у кого свой
;              CHR-генератор/конвертер).
;   red_orig — генерируется автоматически из существующего тайла как
;              "red-версия": p0 копируется, p1 = ~p0 & p1.
;              Используется для подсветки букв на screen 13.
;
; ============================================================================
;  ПРИМЕРЫ
; ============================================================================
;
; Пример 1: Добавить символ '+' (плюс) для всех экранов
;
;   1. В font_patch.ini добавь '+' в [atlas]:
;        row6 = + ...
;   2. Пересобери font.png добавив рисунок плюса в нужной ячейке.
;   3. Здесь:
;        [custom]
;        name = PLUS
;        kind = font
;        char = +
;   4. В games.list пиши имя игры с плюсом: "GAME 1+2"
;
; Пример 2: Иконка дискеты только для screen 13
;
;   [custom]
;   name = DISK
;   kind = raw
;   src_file = disk_icon.bin   ; 16 байт CHR-данных
;   screens = 13
;
; Пример 3: Red-версия буквы 'D' для screen 13
;
;   [custom]
;   name = D_red
;   kind = red_orig
;   src_tile = $0E             ; $0E = phys_tile буквы D
;   screens = 13
;
; ============================================================================
;  ТВОИ КАСТОМЫ (раскомментируй и измени)
; ============================================================================
;
; [custom]
; name = MY_FIRST_TILE
; kind = font
; char = ?
; screens = all
"""


def ensure_multi_alt_ini_exists(ini_path, log_fn=log):
    """Если файла нет — создаёт подробный шаблон.
    
    Возвращает True если файл был создан, False если уже существовал.
    """
    if os.path.exists(ini_path):
        return False
    try:
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(_MULTI_ALT_INI_TEMPLATE)
        log_fn(f"  multi_alt: создан шаблон {ini_path}")
        log_fn(f"             (отредактируй его чтобы добавить свои символы)")
        return True
    except Exception as e:
        log_fn(f"  ⚠️ multi_alt: не удалось создать {ini_path}: {e}")
        return False


def build_custom_table_with_user(user_ini=None, tiles_dir=None, font_ini=None):
    """Расширенная build_custom_table — подмешивает пользовательские
    кастомы из tiles/ и/или multi_alt.ini если они есть.

    Порядок добавления:
      1. Базовая таблица (кириллица + спецы + red prompt/submenu)
      2. tiles/*.png|.chr|.bin (drop-in)
      3. multi_alt.ini [custom] секции (overrides/расширения)

    Если в tiles/ есть символ, который уже есть в базе как font-кастом,
    базовая версия удаляется и заменяется на tiles/-версию.
    """
    table = build_custom_table()

    # 1. Drop-in PNG/CHR из tiles/
    if tiles_dir and os.path.isdir(tiles_dir):
        tile_customs = scan_tiles_dir(tiles_dir, font_ini=font_ini)
        # Символы которые tiles/ перекрывает — удаляем из базовой таблицы
        # (чтобы пользователь мог переопределить кириллицу/спецы своими PNG).
        override_chars = {c[0].upper() for c in tile_customs
                          if isinstance(c[0], str) and len(c[0]) == 1}
        if override_chars:
            table = [ct for ct in table
                     if not (ct[1] in ("font", "raw") and
                             isinstance(ct[0], str) and len(ct[0]) == 1 and
                             ct[3] is None and
                             ct[0].upper() in override_chars)]
        for c in tile_customs:
            if len(table) >= MAX_CUSTOMS:
                raise ValueError(
                    f"multi_alt: лимит MAX_CUSTOMS={MAX_CUSTOMS} превышен. "
                    f"Не получается добавить кастом '{c[0]}' из tiles/.")
            table.append(c)

    # 2. multi_alt.ini поверх
    if user_ini and os.path.exists(user_ini):
        user_customs = load_user_customs(user_ini)
        for c in user_customs:
            if len(table) >= MAX_CUSTOMS:
                raise ValueError(
                    f"multi_alt: лимит MAX_CUSTOMS={MAX_CUSTOMS} превышен. "
                    f"Не получается добавить кастом '{c[0]}'.")
            table.append(c)
    return table


# =============================================================================
# Высокоуровневый build entry-point
# =============================================================================

def build_multi_alt(rom_orig, user_ini=None, tiles_dir=None, font_ini=None):
    """Главная функция: анализирует ROM, строит таблицу кастомов и mapping.
    
    user_ini : путь к multi_alt.ini для пользовательских кастомов (опционально)
    tiles_dir: путь к папке tiles/ с drop-in PNG/CHR файлами (опционально)
    
    Возвращает dict с ключами:
        custom_table : список кастомов
        MAP          : MAP[screen][custom_idx] = phys_tile
        free_per_screen : что свободно на каждом экране (для отладки)
        per_screen_used : что использовано фоном экрана (для exceptions)
        exceptions   : V1.3, dict { screen → [(custom_idx, phys_tile), ...] }
                       — runtime swap'ы которые нужно делать при смене экрана
        user_count   : сколько пользовательских кастомов добавлено
    """
    per_screen_used = analyze_screens(rom_orig)
    free_per_screen = free_slots_per_screen(per_screen_used)
    
    if user_ini or tiles_dir:
        custom_table = build_custom_table_with_user(user_ini, tiles_dir, font_ini=font_ini)
        # Считаем сколько пользовательских — по разнице
        base_table = build_custom_table()
        user_count = len(custom_table) - len(base_table)
    else:
        custom_table = build_custom_table()
        user_count = 0
    
    MAP = assign_slots(custom_table, free_per_screen)
    exceptions = compute_swap_exceptions(custom_table, MAP, per_screen_used)
    
    return {
        "custom_table"   : custom_table,
        "MAP"            : MAP,
        "free_per_screen": free_per_screen,
        "per_screen_used": per_screen_used,
        "exceptions"     : exceptions,
        "user_count"     : user_count,
    }


# =============================================================================
# Standalone: можно запустить как python multi_alt.py для проверки
# =============================================================================

if __name__ == "__main__":
    import sys
    rom_path = "9999 in 1.nes"
    if not os.path.exists(rom_path):
        print(f"❌ Не найден {rom_path}")
        sys.exit(1)
    
    rom = open(rom_path, "rb").read()
    
    # Sanity check
    info = build_multi_alt(rom)
    
    print(f"Кастомов в таблице: {len(info['custom_table'])}")
    for i, ct in enumerate(info['custom_table']):
        name, kind, data, scr = ct[0], ct[1], ct[2], ct[3]
        pref = ct[4] if len(ct) >= 5 else None
        scr_str = "all" if scr is None else str(sorted(scr))
        pref_str = f" pref=${pref:02X}" if pref is not None else ""
        print(f"  #{i:>2}: {name:<8} ({kind:<8}) screens={scr_str}{pref_str}")
    
    print()
    print("Mapping (phys_tile на каждом экране, 0 = не размещён):")
    
    # Печатаем удобный таблично-вид
    n = len(info['custom_table'])
    print(f"  custom_idx | name     | " + " ".join(f"s{s:>2}" for s in range(NUM_SCREENS)))
    print(f"  " + "-" * (15 + NUM_SCREENS * 4))
    for i in range(n):
        ct = info['custom_table'][i]
        cells = []
        for s in range(NUM_SCREENS):
            v = info['MAP'][s][i]
            cells.append(f"{v:02X}" if v else "--")
        print(f"  #{i:>2}        | {ct[0]:<8} | " + " ".join(cells))
    
    print()
    report_assignment(info['custom_table'], info['MAP'], log_fn=print,
                       exceptions=info.get('exceptions'))
    
    # Детализация exception'ов
    print()
    print("Exception'ы (swap нужен при входе на экран):")
    excs = info.get('exceptions', {})
    if not any(excs.values()):
        print("  Нет exception'ов — всё в global-free")
    else:
        for s in sorted(excs.keys()):
            if excs[s]:
                items = ", ".join(
                    f"#{ci}({info['custom_table'][ci][0]})=${ph:02X}"
                    for ci, ph in excs[s])
                print(f"  s{s:>2} ({len(excs[s])} swap): {items}")
