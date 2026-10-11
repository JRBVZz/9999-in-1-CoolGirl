import os
import sys
import math
import hashlib
from config import *
from utils import *
from asm6502 import *
from nesrom import *
from service_bank import *
from fixed_bank import *
from marquee import *
from launcher import *
from options_prompt import *
from prompt_attr_fix import *
from roms_list import read_list, generate_template, apply_list

# =============================================================================
# V5.24: NES 2.0 PRG-size encoding (фикс серого экрана на больших сборках)
# =============================================================================
def encode_prg_size(prg_bytes: int):
    """Кодирует размер PRG в формат NES 2.0 header (байты 4 и 9.lo).
    Логика портирована из ClusterM/nes-containers NesFile.cs.

    NES 2.0 spec (https://www.nesdev.org/wiki/NES_2.0):
      - Если страниц <= 0xEFF (3839, ~60 MB):
          LINEAR: b4 = p16k & 0xFF, b9.lo = (p16k >> 8) & 0x0F
      - Если страниц > 0xEFF:
          EXPONENT-MULTIPLIER: b4 = (E << 2) | M, b9.lo = 0xF
          где размер = 2^E x (2M+1) байт (b4 указывается в байтах, не страницах!)

    Спека: "exponent-multiplier form may only be used if PRG-ROM size
    cannot be specified correctly using the simpler notation".

    Args:
        prg_bytes: размер PRG в байтах

    Returns:
        (b4: int, b9_lo: int, encoded_size_bytes: int)
        encoded_size_bytes — какой размер фактически закодирован
        (= prg_bytes для linear, >= prg_bytes padded для exponent).
    """
    length16k = math.ceil(prg_bytes / 0x4000)

    # Linear form (p16k <= 0xEFF = 3839 страниц = ~60 MB)
    if length16k <= 0xEFF:
        b4 = length16k & 0xFF
        b9_lo = (length16k >> 8) & 0x0F
        return b4, b9_lo, length16k * 0x4000

    # Exponent-multiplier form (для > 60 MB)
    # Размер в БАЙТАХ = 2^E x (2M+1), M в {0..3}, E в {0..63}
    # Ищем минимальный размер >= prg_bytes.
    best = None
    for m in range(4):
        mult = 2 * m + 1
        if prg_bytes <= 0:
            e = 0
        else:
            e = max(0, math.ceil(math.log2(prg_bytes / mult)))
        if e > 63:
            continue
        size = (1 << e) * mult
        if size < prg_bytes:
            # численная погрешность ceil(log2) — поднимем e на 1
            e += 1
            if e > 63:
                continue
            size = (1 << e) * mult
        if best is None or size < best[2]:
            best = (e, m, size)

    if best is None:
        raise ValueError(
            f"encode_prg_size: PRG too large for NES 2.0 ({prg_bytes} bytes)")

    e, m, size = best
    b4 = ((e & 0x3F) << 2) | (m & 3)
    b9_lo = 0x0F
    return b4, b9_lo, size

# =============================================================================
# Вспомогательные функции патчинга bank0
# =============================================================================

def cpu_to_bank0_off(cpu_addr: int) -> int:
    if not (0xC000 <= cpu_addr <= 0xFFFF):
        raise ValueError(f"CPU addr out of bank0: ${cpu_addr:04X}")
    return cpu_addr - 0xC000

def patch_bytes(bank0: bytearray, cpu_addr: int, data: bytes):
    off = cpu_to_bank0_off(cpu_addr)
    bank0[off:off + len(data)] = data

def patch_jmp(bank0: bytearray, cpu_addr: int, target_cpu: int):
    patch_bytes(bank0, cpu_addr,
                bytes([0x4C, target_cpu & 0xFF, (target_cpu >> 8) & 0xFF]))

def patch_abs_target_in_range(bank0, range_start_cpu, range_end_cpu,
                               old_target_cpu, new_target_cpu,
                               allowed_opcodes=(0x20, 0x4C)):
    start = cpu_to_bank0_off(range_start_cpu)
    end   = cpu_to_bank0_off(range_end_cpu) + 1
    hits  = []
    for idx in range(start, max(start, end - 2)):
        op = bank0[idx]
        if op not in allowed_opcodes:
            continue
        lo = bank0[idx + 1]
        hi = bank0[idx + 2]
        if lo == (old_target_cpu & 0xFF) and hi == ((old_target_cpu >> 8) & 0xFF):
            hits.append(idx)
    if len(hits) != 1:
        raise ValueError(
            f"expected 1 hit to ${old_target_cpu:04X} in "
            f"${range_start_cpu:04X}-${range_end_cpu:04X}, got {len(hits)}")
    idx = hits[0]
    bank0[idx + 1] = new_target_cpu & 0xFF
    bank0[idx + 2] = (new_target_cpu >> 8) & 0xFF
    return 0xC000 + idx, bank0[idx]

def patch_abs_target_in_range_all(bank0, range_start_cpu, range_end_cpu,
                                   old_target_cpu, new_target_cpu,
                                   expected, allowed_opcodes=(0x20, 0x4C)):
    start = cpu_to_bank0_off(range_start_cpu)
    end   = cpu_to_bank0_off(range_end_cpu) + 1
    hits  = []
    for idx in range(start, max(start, end - 2)):
        op = bank0[idx]
        if op not in allowed_opcodes:
            continue
        lo = bank0[idx + 1]
        hi = bank0[idx + 2]
        if lo == (old_target_cpu & 0xFF) and hi == ((old_target_cpu >> 8) & 0xFF):
            hits.append(idx)
    if len(hits) != expected:
        raise ValueError(
            f"expected {expected} hits to ${old_target_cpu:04X}, got {len(hits)}")
    for idx in hits:
        bank0[idx + 1] = new_target_cpu & 0xFF
        bank0[idx + 2] = (new_target_cpu >> 8) & 0xFF
    return [0xC000 + idx for idx in hits]

def patch_abs_target_in_range_patch_all(bank0, range_start_cpu, range_end_cpu,
                                         old_target_cpu, new_target_cpu,
                                         allowed_opcodes=(0x20,)):
    start = cpu_to_bank0_off(range_start_cpu)
    end   = cpu_to_bank0_off(range_end_cpu) + 1
    hits  = []
    for idx in range(start, max(start, end - 2)):
        op = bank0[idx]
        if op not in allowed_opcodes:
            continue
        lo = bank0[idx + 1]
        hi = bank0[idx + 2]
        if lo == (old_target_cpu & 0xFF) and hi == ((old_target_cpu >> 8) & 0xFF):
            hits.append(idx)
    for idx in hits:
        bank0[idx + 1] = new_target_cpu & 0xFF
        bank0[idx + 2] = (new_target_cpu >> 8) & 0xFF
    return [0xC000 + idx for idx in hits]

def patch_all_c0ee_in_bank0_before_safe(bank0: bytearray,
                                         hook_cpu: int,
                                         safe_off: int) -> int:
    lo_old = 0xEE
    hi_old = 0xC0
    lo_new = hook_cpu & 0xFF
    hi_new = (hook_cpu >> 8) & 0xFF
    count = 0
    for idx in range(0, max(0, safe_off - 2)):
        if (bank0[idx] == 0x20 and
                bank0[idx + 1] == lo_old and
                bank0[idx + 2] == hi_old):
            bank0[idx + 1] = lo_new
            bank0[idx + 2] = hi_new
            count += 1
    return count

# =============================================================================
# Основная функция сборки
# =============================================================================

def build():
    stage = parse_build_stage()
    if OUTPUT_FORMAT not in ("nes", "bin"):
        log(f"  ❌ Неверный OUTPUT_FORMAT: '{OUTPUT_FORMAT}'")
        return False
    if CONSOLE_DETECT_MODE not in ("reset", "launch"):
        log(f"  ❌ Неверный CONSOLE_DETECT_MODE: '{CONSOLE_DETECT_MODE}'")
        return False

    log("=" * 72)
    log(" CoolGirl ROM Builder V5.26")
    log(f" stage={stage}  NAME_MODE={NAME_MODE}  "
        f"MARQUEE_ENABLED={MARQUEE_ENABLED}  "
        f"CONSOLE_DETECT_MODE={CONSOLE_DETECT_MODE}  "
        f"OUTPUT={OUTPUT}")
    log("=" * 72)

    fixes_db = load_nes_fixes()

    if not os.path.exists(ROM_ORIG):
        log(f"  ❌ {ROM_ORIG} не найден")
        return False
    orig = NESRom(ROM_ORIG)

    if not os.path.isdir(ROMS_DIR):
        log(f"  ❌ {ROMS_DIR}/ не найдена")
        return False

    games = []
    fixed_count = 0
    unsupported = []
    skipped_32k = []
    for rf in sorted(os.listdir(ROMS_DIR)):
        if not rf.lower().endswith(".nes"):
            continue
        try:
            g = NESRom(os.path.join(ROMS_DIR, rf))
            g.apply_fixes(fixes_db)
            # Пропускаем игры с 32 KB PRG-RAM как делает ClusterM Combiner.
            # CoolGirl-Verilog не имеет flags_for_32k_prg_ram ни для одного маппера,
            # поэтому такие игры всё равно не запустятся корректно.
            if getattr(g, 'unsupported_32k_sram', False):
                skipped_32k.append(g.name)
                log(f"  ⚠️ {g.name}: 32 KB PRG-RAM не поддерживается CoolGirl, пропуск")
                continue
            if g.mapper in COOLGIRL_MAPPERS:
                games.append(g)
                if g.fixed:
                    fixed_count += 1
                compat_info = f" compat=${g.compat_mask:02X}" if g.compat_mask else ""
                fix_info    = f" {g.fix_desc}" if g.fixed else ""
                log(f"  ✅ {g.name}: mapper={g.mapper} "
                    f"PRG={g.prg_size//1024}K CHR={g.chr_size//1024}K "
                    f"CRC={g.crc32_hex}{compat_info}{fix_info}")
            else:
                unsupported.append((g.name, g.mapper))
                log(f"  ⚠️ {g.name}: mapper {g.mapper} не поддержан")
        except Exception as e:
            log(f"  ❌ {rf}: {e}")

    # V5.22.18: применить override порядка/имён из list-файла, если включен
    if LIST_FORMAT != "none":
        list_entries = read_list(LIST_FILE, LIST_FORMAT)

        if list_entries is None:
            # Файл не существует — генерируем шаблон и используем дефолтный порядок
            generate_template(LIST_FILE, LIST_FORMAT, ROMS_DIR)
        elif len(list_entries) == 0:
            log(f"  ⚠️ {LIST_FILE} пуст или не парсится — используется дефолтный порядок")
        else:
            log(f"  📋 Применяется список: {LIST_FILE} ({len(list_entries)} записей)")
            games = apply_list(games, list_entries, ROMS_DIR)

    if not games:
        log("  ❌ Нет подходящих игр")
        return False
    num_games = len(games)
    if num_games > MAX_UNIQUE_GAMES:
        log(f"  ❌ Слишком много игр ({num_games}), максимум {MAX_UNIQUE_GAMES}")
        return False

    # Число слотов списка: оригинальное меню показывает 9999 слотов, повторяя игры по
    # кругу (REPEAT_LIST=True). Без повторов слотов ровно столько, сколько игр.
    list_total = LIST_SLOTS_ORIG if REPEAT_LIST else num_games
    if not REPEAT_LIST:
        log(f"  Повторы списка отключены: слотов {list_total} (оригинал {LIST_SLOTS_ORIG})")

    next_save_id = 1
    for g in games:
        if g.battery:
            if next_save_id > MAX_SAVE_GAMES:
                log(f"  ❌ Слишком много battery-игр")
                return False
            g.save_id = next_save_id
            next_save_id += 1
        else:
            g.save_id = 0
    battery_count = next_save_id - 1

    def _prg_alloc(g):
        # ap = реально записываемые байты (NROM 16K дублируется в 32K),
        # pa = выделение/выравнивание (pow2, не меньше ROM_ALIGN)
        ap = 32768 if (g.mapper == 0 and g.prg_size == 16384) else g.prg_size
        return ap, next_pow2(max(ap, ROM_ALIGN))

    def _chr_alloc(g):
        return next_pow2(g.chr_size) if g.chr_size > 0 else 0

    def _prg_payload(g, ap):
        # Реальные байты PRG, которые попадут во флеш (с дублированием NROM 16K→32K)
        pd = bytes(g.prg)
        if g.mapper == 0 and g.prg_size == 16384 and ap == 32768:
            pd = pd + pd
        return pd

    _maxpa = max((_prg_alloc(g)[1] for g in games), default=ROM_ALIGN)
    _total = sum(_prg_alloc(g)[1] for g in games) + sum(_chr_alloc(g) for g in games)
    canvas_size = align_up(align_up(GAMES_START, _maxpa) + _total + ROM_ALIGN, ROM_ALIGN)
    if canvas_size > MAX_ROM_BYTES:
        canvas_size = MAX_ROM_BYTES

    # Карта занятости: 0 = свободно (0xFF у Кластера), 1 = занято
    occ = bytearray(canvas_size)
    _resv = min(GAMES_START, canvas_size)
    occ[0:_resv] = b"\x01" * _resv   # menu CHR / service / data / fixed bank / UNIQUE_DIR

    def _will_fit(pos, length):
        if pos < 0 or pos + length > canvas_size:
            return False
        return occ.find(1, pos, pos + length) == -1

    def _mark(pos, length):
        occ[pos:pos + length] = b"\x01" * length

    prg_off, chr_off = {}, {}
    prg_dedup, chr_dedup = {}, {}            # sha1(содержимое) -> офсет
    prg_dedup_name, chr_dedup_name = {}, {}  # sha1 -> имя первой (оригинальной) игры
    prg_dups, chr_dups = [], []              # [(имя_дубля, имя_оригинала)]
    prg_dup_hits = chr_dup_hits = 0

    # PRG: большие вперёд, шаг = собственный pow2 (самовыравнивание)
    for game in sorted(games, key=lambda g: _prg_alloc(g)[1], reverse=True):
        ap, pa = _prg_alloc(game)
        key = hashlib.sha1(_prg_payload(game, ap)).digest()
        if key in prg_dedup:
            prg_off[id(game)] = prg_dedup[key]   # дубль — переиспользуем офсет
            prg_dup_hits += 1
            prg_dups.append((game.name, prg_dedup_name[key]))
            continue
        pos = 0
        while pos + pa <= canvas_size and not _will_fit(pos, pa):
            pos += pa
        if pos + pa > canvas_size:
            log(f"  ❌ PRG не помещается: {game.name} ({pa//1024}K)")
            return False
        _mark(pos, pa)
        prg_off[id(game)] = pos
        prg_dedup[key] = pos
        prg_dedup_name[key] = game.name

    # CHR: большие вперёд, шаг 0x2000, first-fit (заполняет дыры PRG)
    for game in sorted(games, key=lambda g: _chr_alloc(g), reverse=True):
        ca = _chr_alloc(game)
        if ca == 0:
            chr_off[id(game)] = 0
            continue
        key = hashlib.sha1(bytes(game.chr)).digest()
        if key in chr_dedup:
            chr_off[id(game)] = chr_dedup[key]   # дубль CHR — переиспользуем офсет
            chr_dup_hits += 1
            chr_dups.append((game.name, chr_dedup_name[key]))
            continue
        pos = 0
        while pos + ca <= canvas_size and not _will_fit(pos, ca):
            pos += 0x2000
        if pos + ca > canvas_size:
            log(f"  ❌ CHR не помещается: {game.name} ({ca//1024}K)")
            return False
        _mark(pos, ca)
        chr_off[id(game)] = pos
        chr_dedup[key] = pos
        chr_dedup_name[key] = game.name

    # Сборка placements в исходном порядке игр (формат не меняется)
    placements = []
    games_end = GAMES_START
    for game in games:
        ap, pa = _prg_alloc(game)
        po, co = prg_off[id(game)], chr_off[id(game)]
        placements.append({'game': game, 'prg': po, 'chr': co, 'actual_prg': ap})
        games_end = max(games_end, po + pa)
        if co:
            games_end = max(games_end, co + _chr_alloc(game))
    games_end = align_up(games_end, ROM_ALIGN)

    log(f"  Размещение (ClusterM-style + дедуп): "
        f"PRG {num_games}→{len(prg_dedup)} уник. (дублей {prg_dup_hits}), "
        f"CHR {len(chr_dedup)} уник. (дублей {chr_dup_hits})")
    # V5.26: показываем, какие именно игры — дубли (делятся офсетом с оригиналом)
    if prg_dups:
        log(f"    дубли PRG ({len(prg_dups)}) — общий офсет с оригиналом:")
        for _dup, _orig in prg_dups:
            log(f"      • «{_dup}» = «{_orig}»")
    if chr_dups:
        log(f"    дубли CHR ({len(chr_dups)}) — общий CHR с оригиналом:")
        for _dup, _orig in chr_dups:
            log(f"      • «{_dup}» = «{_orig}»")
    log(f"  games_end=0x{games_end:05X}")

    # =========================================================
    # V5.26: расчёт размера флеша + честная проверка лимита
    # =========================================================
    # Резерв под battery-сейвы кладётся в самую вершину флеша и не должен
    # пересекаться с играми. need_bytes = игры + этот резерв.
    if battery_count > 0:
        save_sector_count = (battery_count + 3) // 4
        save_bytes        = save_sector_count * 0x20000
    else:
        save_sector_count = 0
        save_bytes        = 0

    need_bytes = games_end + save_bytes

    if need_bytes > MAX_ROM_BYTES:
        over = need_bytes - MAX_ROM_BYTES
        log(f"  ❌ Превышен лимит {MAX_ROM_BYTES // 1024} КБ "
            f"({MAX_ROM_BYTES // (1024*1024)} ��Б):")
        log(f"       игры:          {games_end // 1024} КБ (0x{games_end:X})")
        log(f"       резерв сейвов: {save_bytes // 1024} КБ "
            f"({save_sector_count} × 128 КБ для {battery_count} battery-игр)")
        log(f"       итого нужно:   {need_bytes // 1024} КБ")
        log(f"       ⛔ превышение: {(over + 1023) // 1024} КБ "
            f"(надо освободить столько места)")
        return False

    # Размер чипа округляется вверх до степени двойки — это нужно только для
    # адресации сейвов от вершины флеша. need_bytes уже проверен (влезает в
    # лимит), поэтому total_flash тут гарантированно <= MAX_ROM_BYTES.
    # (Эквивалент прежнему циклу "total_flash <<= 1 пока сейвы не влезут".)
    total_flash = next_pow2(need_bytes)
    if battery_count > 0:
        saves_end   = total_flash
        saves_start = total_flash - save_bytes
    else:
        saves_start = saves_end = 0

    flash = bytearray(b"\xFF" * total_flash)

    cm = bytearray(orig.chr[:8192])
    if len(cm) < 8192:
        cm += bytearray(8192 - len(cm))

    # Анализирует все 14 экранов меню и распределяет кастомные тайлы в
    # свободные слоты. 41 Класс-1 кастом (кириллица + & + 8 спецов) кладутся
    # в global-free слоты ($DD..$FC и $AE..$D6) — без runtime swap.
    # 9 Класс-3 кастомов (red на screen13) обрабатываются prompt_attr_fix
    # как и раньше, через backup в SRAM.
    #
    # V5.23.1: 7 новых символов (! @ ^ _ [ ] ~) размещены в "almost-global-free"
    # слотах ($AA $AB $C5..$C9). Они занимают один и тот же phys_tile на всех
    # экранах, НО на 2 экранах (s2 и s8) этот phys_tile занят фоном —
    # для них нужен runtime swap CHR через svc_multi_alt_swap.
    #
    # Если есть multi_alt.ini — добавляются пользовательские кастомы.
    try:
        from multi_alt import (build_multi_alt, get_required_font_chars,
                                apply_class1_to_chr, make_chars_map,
                                make_chars_map_virtual, get_initial_phys_table,
                                report_assignment, build_full_swap_tables, MAX_CUSTOMS)
        from font_patch import extract_chr_for_chars
        from utils import set_custom_char_map_virtual

        with open(ROM_ORIG, "rb") as f:
            rom_orig_bytes = f.read()

        # Drop-in папка tiles/ (V5.24)
        # Любой PNG/CHR файл в этой папке = один кастомный тайл.
        ma_tiles = None
        if "TILES_DIR" in globals() and TILES_DIR:
            if os.path.isdir(TILES_DIR):
                ma_tiles = TILES_DIR
                log(f"  multi_alt: tiles/ найдена ({TILES_DIR})")

        # Путь к multi_alt.ini (опционально)
        # V5.9: если файл не существует — создаём подробный шаблон
        # V5.23.5: автогенерация управляется опцией MULTI_ALT_AUTO_GENERATE
        # V5.24: если tiles/ есть — шаблон INI НЕ создаётся (drop-in достаточно)
        ma_ini = None
        if "MULTI_ALT_INI" in globals() and MULTI_ALT_INI:
            if MULTI_ALT_AUTO_GENERATE and not ma_tiles:
                from multi_alt import ensure_multi_alt_ini_exists
                ensure_multi_alt_ini_exists(MULTI_ALT_INI, log_fn=log)
            if os.path.exists(MULTI_ALT_INI):
                ma_ini = MULTI_ALT_INI

        # V5.26: путь к font_patch.ini — чтобы tiles/*.png читались в той же
        # палитре, что и font.png (3 цвета + прозрачный).
        ma_font_ini = (FONT_PATCH_FILE
                       if ("FONT_PATCH_FILE" in globals() and FONT_PATCH_FILE
                           and os.path.exists(FONT_PATCH_FILE))
                       else None)
        ma_info = build_multi_alt(rom_orig_bytes,
                                   user_ini=ma_ini, tiles_dir=ma_tiles,
                                   font_ini=ma_font_ini)

        ma_chars = get_required_font_chars(ma_info['custom_table'])

        # Извлекаем CHR из font.png для всех Класс-1 font-кастомов
        if FONT_PATCH_ENABLED and FONT_PATCH_FILE and os.path.exists(FONT_PATCH_FILE):
            font_chr_dict = extract_chr_for_chars(FONT_PATCH_FILE, ma_chars)
        else:
            log("  ⚠️ multi_alt: font_patch.ini не найден — кастомы не записаны")
            font_chr_dict = None

        if font_chr_dict:
            # Записываем кириллицу/спецы в указанные phys_tile в cm
            ma_result = apply_class1_to_chr(cm, ma_info['custom_table'],
                                             ma_info['MAP'], font_chr_dict,
                                             chr_table=0)
            base_count  = len(ma_info['custom_table']) - ma_info['user_count']
            user_label  = (f" + {ma_info['user_count']} пользовательских"
                           if ma_info['user_count'] > 0 else "")
            log(f"  multi_alt: Class-1 written={ma_result['written_count']} "
                f"slots=${min(ma_result['phys_tiles_used']):02X}.."
                f"${max(ma_result['phys_tiles_used']):02X}"
                f" (base={base_count}{user_label})")
            if ma_ini:
                log(f"  multi_alt: загружен {ma_ini}")

            # V5.9: подробный отчёт по распределению (Класс-1 / Класс-3 / лимиты)
            report_assignment(ma_info['custom_table'], ma_info['MAP'],
                              log_fn=log,
                              exceptions=ma_info.get('exceptions'),
                              free_per_screen=ma_info.get('free_per_screen'))

            # V5.23 Шаг 1: переход на виртуальные индексы (custom_idx 0..N)
            # Базовые символы (0-9 A-Z пробел точка) кодируются в phys_tile $01..$26
            # как раньше, кастомы — как $80 | custom_idx, runtime ASM подменит
            # через MA_MAP_RAM на phys_tile.
            chars_map_virtual = make_chars_map_virtual(ma_info['custom_table'])
            set_custom_char_map_virtual(chars_map_virtual)
            log(f"  multi_alt: chars_map_virtual loaded ({len(chars_map_virtual)} entries)")

            # Также сохраним и старую phys-карту для совместимости — если font_patch
            # будет override'ить, нужны phys_tile (но в виртуальном режиме мы их не
            # используем; см. ниже font_patch override).
            chars_map_phys = make_chars_map(ma_info['custom_table'], ma_info['MAP'])

            # V5.23 Шаг 1: начальные phys_tile для MA_MAP_RAM (50 байт, screen 0)
            initial_phys = get_initial_phys_table(ma_info['custom_table'],
                                                   ma_info['MAP'], screen=0)
            multi_alt_initial_phys = initial_phys

            # V1.9: build_full_swap_tables возвращает (bank1, bank2, info, offsets).
            # bank1 (map+chr+hdr) — в service bank. bank2 (swap_lists) — в data bank 2.
            swap_bank1, swap_bank2, swap_info, swap_offsets = build_full_swap_tables(
                ma_info['custom_table'], ma_info['MAP'],
                font_chr_dict, rom_orig_bytes)
            log(f"  multi_alt: swap_data {swap_info['total_size']} байт "
                f"(map={swap_info['screen_map_size']}, "
                f"chr={swap_info['custom_chr_size']}, "
                f"hdr={swap_info['swap_header_size']}, "
                f"lists={swap_info['swap_lists_size']})")
            log(f"  multi_alt: bank1={swap_info['bank1_size']} (service), "
                f"bank2={swap_info['bank2_size']} (data) — V1.9 split")
            log(f"  multi_alt: swap нужен на {swap_info['screens_with_swap']} экранах "
                f"(макс {swap_info['max_swap_per_screen']} на одном экране)")

            # Сохраняем info для последующих этапов (red на screen13 и т.д.)
            multi_alt_info         = ma_info
            multi_alt_swap_bank1   = swap_bank1
            multi_alt_swap_bank2   = swap_bank2
            multi_alt_swap_info    = swap_info
            multi_alt_swap_offsets = swap_offsets
            multi_alt_swap_data    = swap_bank1  # backwards-compat (legacy var)
        else:
            multi_alt_info         = None
            multi_alt_swap_data    = None
            multi_alt_swap_bank1   = None
            multi_alt_swap_bank2   = None
            multi_alt_swap_info    = None
            multi_alt_swap_offsets = None
            multi_alt_initial_phys = None
            report_assignment(ma_info['custom_table'], ma_info['MAP'],
                              log_fn=log,
                              exceptions=ma_info.get('exceptions'),
                              free_per_screen=ma_info.get('free_per_screen'))
    except Exception as e:
        log(f"  ⚠️ multi_alt: {e}")
        import traceback
        traceback.print_exc()
        multi_alt_info         = None
        multi_alt_swap_data    = None
        multi_alt_swap_bank1   = None
        multi_alt_swap_bank2   = None
        multi_alt_swap_info    = None
        multi_alt_swap_offsets = None
        multi_alt_initial_phys = None

    font_enabled = ("FONT_PATCH_ENABLED" in globals()) and bool(FONT_PATCH_ENABLED)
    font_ini     = FONT_PATCH_FILE if ("FONT_PATCH_FILE" in globals()) else ""
    if font_enabled:
        try:
            from font_patch import apply_font_patch_if_present
            if not font_ini:
                raise ValueError("FONT_PATCH_FILE пустой")
            if not os.path.exists(font_ini):
                raise FileNotFoundError(font_ini)
            # V5.23: пропускаем символы которые уже обработал multi_alt
            skip_chars = None
            if multi_alt_info is not None:
                skip_chars = list(chars_map_virtual.keys())
            char_map = apply_font_patch_if_present(cm, font_ini, log=log,
                                                    skip_chars=skip_chars)
            # V5.23 Шаг 1: при активном multi_alt в virtual-режиме мы НЕ
            # переключаемся обратно на phys-режим. font_patch только
            # перерисовывает базовые ASCII через [atlas]. Override [chars]
            # при virtual-режиме игнорируется.
            if char_map and multi_alt_info is None:
                set_custom_char_map(char_map)
                log(f"  Font chars loaded: {len(char_map)}")
        except Exception as e:
            log(f"  ⚠️ Font patch: {e}")

    flash[0:0x2000] = cm

    for pl in placements:
        g  = pl["game"]
        pd = bytearray(g.prg)
        if g.mapper == 0 and g.prg_size == 16384 and pl["actual_prg"] == 32768:
            pd = pd + pd
        flash[pl["prg"]:pl["prg"] + len(pd)] = pd
        if g.chr_size > 0:
            flash[pl["chr"]:pl["chr"] + g.chr_size] = g.chr

    unique_dir = bytearray()
    for pl in placements:
        g    = pl["game"]
        regs = calc_game_regs(g, pl["prg"], pl["chr"], pl["actual_prg"])
        if g.chr_size > 0:
            sb        = pl["chr"] >> 14
            chr_h     = (sb >> 8) & 0xFF
            chr_l     = sb & 0xFF
            chr_s     = 0x80 + ((pl["chr"] % 16384) >> 8)
            chr_count = g.chr_size // 8192
        else:
            chr_h = chr_l = chr_s = chr_count = 0
        entry = build_unique_entry(g, regs, chr_h, chr_l, chr_s, chr_count, g.save_id)
        unique_dir += entry

    if len(unique_dir) > UNIQUE_DIR_RESERVED:
        log(f"  ❌ UNIQUE_DIR overflow: {len(unique_dir)} > {UNIQUE_DIR_RESERVED}")
        return False
    flash[UNIQUE_DIR_START:UNIQUE_DIR_START + len(unique_dir)] = unique_dir
    unique_dir_end = UNIQUE_DIR_START + len(unique_dir)
    log(f"UNIQUE_DIR: entries={num_games}  size={len(unique_dir)}  "
        f"0x{UNIQUE_DIR_START:05X}-0x{unique_dir_end - 1:05X}")
    log(f"  GAMES_START: 0x{GAMES_START:05X}")

    # =========================================================
    # V1.9: SAFE-2 db_* helpers — должны быть размещены ДО service bank,
    # потому что svc_multi_alt_swap внутри service bank делает JSR на них.
    # Helpers переключают $5005 между service bank (1) и data bank (2)
    # для чтения swap_lists. Делается ТОЛЬКО из fixed bank ($C000+),
    # иначе PC оказывается в неверном банке после переключения.
    # =========================================================
    safe2_code     = bytearray()
    safe2_base_cpu = SAFE_2_CPU
    safe2_size     = SAFE_2_END - SAFE_2 + 1   # = 244 байта

    db_read_count_cpu = safe2_base_cpu + len(safe2_code)
    safe2_code       += build_db_read_count(db_read_count_cpu)

    db_read_header_cpu = safe2_base_cpu + len(safe2_code)
    safe2_code        += build_db_read_header(db_read_header_cpu)

    db_stream16_cpu = safe2_base_cpu + len(safe2_code)
    safe2_code     += build_db_stream16(db_stream16_cpu)

    log(f"  db_read_count  @${db_read_count_cpu:04X} (SAFE-2)")
    log(f"  db_read_header @${db_read_header_cpu:04X} (SAFE-2)")
    log(f"  db_stream16    @${db_stream16_cpu:04X} (SAFE-2)")

    # =========================================================
    # SERVICE BANK
    # =========================================================
    service_code     = bytearray()
    service_base_cpu = 0x8000

    svc_enable_cpu = service_base_cpu + len(service_code)
    service_code  += build_service_enable_prg_ram(svc_enable_cpu)

    svc_disable_cpu = service_base_cpu + len(service_code)
    service_code   += build_service_disable_prg_ram(svc_disable_cpu)

    svc_store_cpu = service_base_cpu + len(service_code)
    service_code += build_service_store_last_save(svc_store_cpu,
                                                  svc_enable_cpu, svc_disable_cpu)

    ram_helper_src_cpu = service_base_cpu + len(service_code)
    ram_helper         = build_ram_save_helper(RAM_HELPER_ADDR,
                                               SERVICE_BANK_INDEX << 2)
    service_code      += ram_helper

    run_ram_helper_cpu = service_base_cpu + len(service_code)
    service_code      += build_run_ram_helper(run_ram_helper_cpu,
                                              ram_helper_src_cpu, len(ram_helper))

    service_load_cpu = service_base_cpu + len(service_code)
    service_code    += build_service_load_save(service_load_cpu,
                                               run_ram_helper_cpu, svc_store_cpu)

    service_flush_cpu = service_base_cpu + len(service_code)
    service_code     += build_service_flush_pending(service_flush_cpu,
                                                    svc_enable_cpu, svc_disable_cpu,
                                                    run_ram_helper_cpu, svc_store_cpu)

    svc_console_cpu = service_base_cpu + len(service_code)
    service_code   += build_console_detect(svc_console_cpu)

    svc_clear_cpu = service_base_cpu + len(service_code)
    service_code += build_clear_menu_work_ram(svc_clear_cpu)

    svc_pre_launch_clear_cpu = service_base_cpu + len(service_code)
    service_code            += build_pre_launch_clear(svc_pre_launch_clear_cpu)
    log(f"  svc_pre_launch_clear @${svc_pre_launch_clear_cpu:04X}")

    svc_prompt_alt_restore_cpu = service_base_cpu + len(service_code)
    service_code              += build_service_prompt_alt_restore(
        svc_prompt_alt_restore_cpu,
        enable_cpu=svc_enable_cpu,
        disable_cpu=svc_disable_cpu
    )

    # V5.7: build_show_saving_warning и build_show_incompatible_console
    # перенесены ВНИЗ — они теперь принимают параметр multi_alt_swap_cpu,
    # который становится известен только после сборки svc_multi_alt_swap.
    # Прямой JSR на svc_multi_alt_swap внутри service bank откатывает все
    # кастомы CHR на state screen 9 (фон с пальмами/огнём + спрайты),
    # без чего на экранах SAVING/INCOMPATIBLE портится графика.

    svc_recalc_cpu = service_base_cpu + len(service_code)
    service_code  += build_recalc_page_row(svc_recalc_cpu)

    dir_helper_src_cpu = service_base_cpu + len(service_code)
    dir_helper         = build_ram_fetch_dir_helper(RAM_DIR_HELPER_ADDR)
    dir_helper_room    = DIR_HELPER_LIMIT - RAM_DIR_HELPER_ADDR
    if len(dir_helper) > dir_helper_room:
        log(f"  ❌ dir helper RAM overflow: {len(dir_helper)} > {dir_helper_room}")
        return False
    service_code += dir_helper

    svc_fetch_dir_cpu = service_base_cpu + len(service_code)
    service_code     += build_service_fetch_dir_entry(svc_fetch_dir_cpu,
                                                      dir_helper_src_cpu,
                                                      len(dir_helper))

    svc_load_state_cpu = None
    svc_save_state_cpu = None
    if stage >= 2:
        svc_load_state_cpu = service_base_cpu + len(service_code)
        service_code      += build_service_load_state(svc_load_state_cpu,
                                                      svc_enable_cpu, svc_disable_cpu,
                                                      total_slots=list_total)
        svc_save_state_cpu = service_base_cpu + len(service_code)
        service_code      += build_service_save_state(svc_save_state_cpu,
                                                      svc_enable_cpu, svc_disable_cpu)

    svc_cfg_init_cpu = service_base_cpu + len(service_code)
    service_code    += build_service_cfg_init(svc_cfg_init_cpu,
                                              svc_enable_cpu, svc_disable_cpu)

    svc_cfg_store_cpu = service_base_cpu + len(service_code)
    service_code     += build_service_cfg_store(svc_cfg_store_cpu,
                                                svc_enable_cpu, svc_disable_cpu)

    svc_prompt_show_cpu = service_base_cpu + len(service_code)
    service_code       += build_service_prompt_show(svc_prompt_show_cpu,
                                                    enable_cpu=svc_enable_cpu,
                                                    disable_cpu=svc_disable_cpu)

    svc_prompt_hide_cpu = service_base_cpu + len(service_code)
    service_code       += build_service_prompt_hide(svc_prompt_hide_cpu,
                                                    enable_cpu=svc_enable_cpu,
                                                    disable_cpu=svc_disable_cpu)

    svc_prompt_tick_cpu = service_base_cpu + len(service_code)
    service_code       += build_service_prompt_tick(svc_prompt_tick_cpu,
                                                    enable_cpu=svc_enable_cpu,
                                                    disable_cpu=svc_disable_cpu)

    svc_prompt_alt_ensure_cpu = service_base_cpu + len(service_code)
    service_code             += build_service_prompt_alt_ensure(svc_prompt_alt_ensure_cpu)

    svc_submenu_draw_cpu = service_base_cpu + len(service_code)
    # V5.23 Шаг 2: при включенном multi_alt swap старый alt_ensure не нужен —
    # multi_alt swap сам подгружает все нужные ALT-CHR через SWAP_LIST[13].
    _submenu_alt_ensure = (None if multi_alt_swap_data is not None
                            else svc_prompt_alt_ensure_cpu)
    service_code        += build_service_submenu_draw(svc_submenu_draw_cpu,
                                                      alt_ensure_cpu=_submenu_alt_ensure)

    svc_music_start_cpu = service_base_cpu + len(service_code)
    service_code       += build_service_music_start(svc_music_start_cpu)

    svc_music_stop_cpu = service_base_cpu + len(service_code)
    service_code      += build_service_music_stop(svc_music_stop_cpu)

    # V5.22.7: блок двухфазной смены экрана (svc_load_palette_for_screen,
    # scr_chg_trigger, palette_scr_stub) удалён. После миграции музыки на
    # APU frame counter IRQ (V5.22) механизм перестал быть нужен:
    # snd_update тикает независимо от PPU-off секций. Патч JSR $C1A1 в
    # основном цикле всё равно никогда не срабатывал — в этом дампе ROM
    # такой инструкции в $C05A-$C104 нет.

    # overlay init (копирование S/T при инициализации меню)
    svc_overlay_init_cpu = service_base_cpu + len(service_code)
    service_code        += build_service_overlay_init(svc_overlay_init_cpu)
    log(f"  svc_overlay_init @${svc_overlay_init_cpu:04X}")

    # overlay NMI (только палитра)
    svc_text_overlay_cpu = service_base_cpu + len(service_code)
    service_code        += build_service_text_overlay_nmi(svc_text_overlay_cpu)
    log(f"  svc_text_overlay_nmi @${svc_text_overlay_cpu:04X}")

    # V5.21: snapshot_vblank — PPU read в vblank (БЕЗ МИГАНИЯ, БЕЗ RENDER OFF!)
    svc_snapshot_vblank_cpu = service_base_cpu + len(service_code)
    service_code           += build_service_snapshot_vblank(svc_snapshot_vblank_cpu)
    log(f"  svc_snapshot_vblank @${svc_snapshot_vblank_cpu:04X}")

    # V1.9: multi_alt swap — полный per-screen механизм с разделением на 2 банка.
    # Bank 1 (service): screen_map + custom_chr + swap_header (фикс. размер).
    # Bank 2 (data):    swap_lists (переменный размер, до 16 КБ).
    multi_alt_swap_cpu = None
    if multi_alt_swap_bank1 is not None:
        # Bank 1 portion — кладём в service bank
        ma_data_base_cpu = service_base_cpu + len(service_code)
        service_code += multi_alt_swap_bank1
        log(f"  multi_alt bank1 (map+chr+hdr) @${ma_data_base_cpu:04X} "
            f"({len(multi_alt_swap_bank1)} байт, service bank)")

        # CPU-адреса частей в bank 1
        screen_map_cpu_v2  = ma_data_base_cpu + multi_alt_swap_offsets['screen_map']
        custom_chr_cpu_v2  = ma_data_base_cpu + multi_alt_swap_offsets['custom_chr']
        swap_header_cpu_v2 = ma_data_base_cpu + multi_alt_swap_offsets['swap_header']

        # Bank 2 portion — swap_lists в data bank. CPU-адрес = DATA_BANK_CPU + offset (0).
        # Физически записываем в flash[DATA_BANK_OFFSET:...] ниже.
        swap_lists_cpu_v2 = DATA_BANK_CPU + multi_alt_swap_offsets['swap_lists']

        log(f"    screen_map @${screen_map_cpu_v2:04X} (bank1)")
        log(f"    custom_chr @${custom_chr_cpu_v2:04X} (bank1)")
        log(f"    swap_hdr   @${swap_header_cpu_v2:04X} (bank1)")
        log(f"    swap_lists @${swap_lists_cpu_v2:04X} (bank2, {len(multi_alt_swap_bank2)} байт)")

        from multi_alt import build_svc_multi_alt_swap
        multi_alt_swap_cpu = service_base_cpu + len(service_code)
        service_code += build_svc_multi_alt_swap(
            multi_alt_swap_cpu,
            screen_map_cpu=screen_map_cpu_v2,
            custom_chr_cpu=custom_chr_cpu_v2,
            swap_header_cpu=swap_header_cpu_v2,
            swap_lists_cpu=swap_lists_cpu_v2,
            db_read_count_cpu=db_read_count_cpu,
            db_read_header_cpu=db_read_header_cpu,
            db_stream16_cpu=db_stream16_cpu)
        log(f"  svc_multi_alt_swap @${multi_alt_swap_cpu:04X} (V1.9 dual-bank)")
    else:
        log(f"  multi_alt swap: пропущен (нет данных)")

    # V5.7: теперь, когда multi_alt_swap_cpu известен, собираем
    # show_saving_warning и show_incompatible_console.
    # Если multi_alt_swap отключён (старый ROM без кастомов), они получат
    # multi_alt_swap_cpu=None и упадут на старый путь через alt_restore_cpu.
    svc_show_cpu = service_base_cpu + len(service_code)
    service_code += build_show_saving_warning(
        svc_show_cpu,
        alt_restore_cpu=svc_prompt_alt_restore_cpu,
        multi_alt_swap_cpu=multi_alt_swap_cpu)
    log(f"  svc_show_saving_warning @${svc_show_cpu:04X}"
        + (" (multi_alt aware)" if multi_alt_swap_cpu else ""))

    svc_incompat_cpu = service_base_cpu + len(service_code)
    service_code    += build_show_incompatible_console(
        svc_incompat_cpu,
        alt_restore_cpu=svc_prompt_alt_restore_cpu,
        multi_alt_swap_cpu=multi_alt_swap_cpu)
    log(f"  svc_show_incompatible @${svc_incompat_cpu:04X}"
        + (" (multi_alt aware)" if multi_alt_swap_cpu else ""))

    svc_menu_ui_nmi_cpu = service_base_cpu + len(service_code)
    service_code       += build_service_menu_ui_nmi(
        svc_menu_ui_nmi_cpu,
        cfg_init_cpu=svc_cfg_init_cpu,
        cfg_store_cpu=svc_cfg_store_cpu,
        prompt_show_cpu=svc_prompt_show_cpu,
        prompt_hide_cpu=svc_prompt_hide_cpu,
        submenu_draw_cpu=svc_submenu_draw_cpu,
        music_start_cpu=svc_music_start_cpu,
        music_stop_cpu=svc_music_stop_cpu,
        fetch_dir_cpu=svc_fetch_dir_cpu,
        prompt_tick_cpu=svc_prompt_tick_cpu,
        text_overlay_cpu=svc_text_overlay_cpu,
        snapshot_vblank_cpu=svc_snapshot_vblank_cpu
    )

    if len(service_code) > SERVICE_BANK_SIZE:
        log(f"  ❌ Service bank overflow: {len(service_code)} > {SERVICE_BANK_SIZE}")
        return False
    flash[SERVICE_BANK_OFFSET:SERVICE_BANK_OFFSET + len(service_code)] = service_code

    # =========================================================
    # V1.9: DATA BANK (bank 2) — swap_lists из multi_alt
    # =========================================================
    if multi_alt_swap_bank2 is not None and len(multi_alt_swap_bank2) > 0:
        if len(multi_alt_swap_bank2) > DATA_BANK_SIZE:
            log(f"  ❌ Data bank overflow: {len(multi_alt_swap_bank2)} > {DATA_BANK_SIZE}")
            return False
        flash[DATA_BANK_OFFSET:DATA_BANK_OFFSET + len(multi_alt_swap_bank2)] = multi_alt_swap_bank2
        log(f"  data bank: swap_lists {len(multi_alt_swap_bank2)} байт "
            f"@ROM[{DATA_BANK_OFFSET:#x}..{DATA_BANK_OFFSET+len(multi_alt_swap_bank2)-1:#x}] "
            f"(bank {DATA_BANK_INDEX}, $5005=${(DATA_BANK_INDEX << 2) & 0xFF:02X})")

    # =========================================================
    # FIXED BANK (menu bank0)
    # =========================================================
    bank0 = bytearray(orig.prg[:16384])

    fixed_code     = bytearray()
    fixed_base_cpu = 0xC000 + SAFE

    # V5.22.3: SAFE-2 — дополнительное окно в мёртвой зоне $EECC..$EFBF.
    # Используем когда основное SAFE-окно заполнено. Расход контролируется
    # отдельно от основного SAFE.
    # V1.9: safe2_code уже инициализирован выше (с db_* helper'ами для multi_alt).
    # Здесь просто продолжаем заполнение.

    # V5.22.3: div24_16 — быстрое 24-bit / 16-bit деление для init_page_state.
    # Заменяет наивный subtract-loop, экономит до 4.5 кадров на page rebuild
    # при slot=9990 + unique_count=2.
    div24_16_cpu = safe2_base_cpu + len(safe2_code)
    safe2_code  += build_div24_16_safe2(div24_16_cpu)
    log(f"  div24_16 @${div24_16_cpu:04X} (SAFE-2)")

    # V5.23 Шаг 1: data + ASM для MA_MAP_RAM init — размещаем в SAFE-2.
    # Освобождаем ~64 байта в основном SAFE-окне (которое уже почти переполнено).
    ma_init_data_cpu    = None
    init_ma_map_ram_cpu = None
    ma_translate_cpu    = None
    if multi_alt_initial_phys is not None:
        from multi_alt import (build_initial_ma_map_data, build_init_ma_map_ram_asm,
                                build_ma_translate_asm)
        ma_init_data     = build_initial_ma_map_data(multi_alt_initial_phys)
        ma_init_data_cpu = safe2_base_cpu + len(safe2_code)
        safe2_code      += ma_init_data
        log(f"  multi_alt initial MA_MAP_RAM data @${ma_init_data_cpu:04X} ({len(ma_init_data)} байт, SAFE-2)")

        init_ma_map_ram_cpu = safe2_base_cpu + len(safe2_code)
        safe2_code += build_init_ma_map_ram_asm(init_ma_map_ram_cpu, ma_init_data_cpu)
        log(f"  init_ma_map_ram @${init_ma_map_ram_cpu:04X} (SAFE-2)")

        ma_translate_cpu = safe2_base_cpu + len(safe2_code)
        safe2_code += build_ma_translate_asm(ma_translate_cpu)
        log(f"  ma_translate @${ma_translate_cpu:04X} (SAFE-2)")

    # V5.24: stub для вызова svc_pre_launch_clear из ext_launch_game.
    # Размещаем в SAFE-2 — основное SAFE-окно почти полное.
    # Простой stub без зависимости от set_service_cpu (адрес которого
    # ещё не известен в этот момент).
    SVC_BANK_5005 = (SERVICE_BANK_INDEX << 2) & 0xFF
    pre_launch_clear_cpu = safe2_base_cpu + len(safe2_code)
    pre_launch_clear_stub_src = f"""
pre_launch_clear_stub:
    php
    sei
    lda #${SVC_BANK_5005:02X}
    sta $5005
    jsr ${svc_pre_launch_clear_cpu:04X}
    lda #$00
    sta $5005
    plp
    rts
"""
    safe2_code += assemble_6502(pre_launch_clear_stub_src, pre_launch_clear_cpu)
    log(f"  pre_launch_clear_stub @${pre_launch_clear_cpu:04X} (SAFE-2)")

    enable_ram_cpu  = fixed_base_cpu + len(fixed_code)
    fixed_code     += build_enable_prg_ram_menu(enable_ram_cpu)

    disable_ram_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code     += build_disable_prg_ram_menu(disable_ram_cpu)

    set_service_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code     += build_set_service_bank(set_service_cpu)

    # V5.24: унифицированный service_call диспетчер.
    # Экономит ~107 байт в fixed bank: 14 stub'ов по 5 байт вместо 17.
    service_call_disp_cpu        = fixed_base_cpu + len(fixed_code)
    _disp_bytes, scd_targets_cpu = build_service_call_dispatcher(service_call_disp_cpu)
    fixed_code                  += _disp_bytes
    log(f"  service_call_disp @${service_call_disp_cpu:04X}, "
        f"scd_targets @${scd_targets_cpu:04X} ({len(_disp_bytes)} байт)")

    service_call_targets = []

    def emit_service_stub(target_cpu, name=""):
        nonlocal fixed_code
        idx = len(service_call_targets)
        if idx >= SERVICE_CALL_TABLE_MAX:
            raise RuntimeError(
                f"Переполнение service_call таблицы "
                f"({SERVICE_CALL_TABLE_MAX}): {name}")
        service_call_targets.append(target_cpu)
        stub_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code += build_service_call_stub(stub_cpu, service_call_disp_cpu, idx)
        return stub_cpu

    console_detect_cpu    = emit_service_stub(svc_console_cpu,      "console_detect")
    clear_work_cpu        = emit_service_stub(svc_clear_cpu,        "clear_work")
    show_warning_cpu      = emit_service_stub(svc_show_cpu,         "show_warning")
    recalc_cpu            = emit_service_stub(svc_recalc_cpu,       "recalc")
    incompat_show_cpu     = emit_service_stub(svc_incompat_cpu,     "incompat_show")
    fetch_dir_stub_cpu    = emit_service_stub(svc_fetch_dir_cpu,    "fetch_dir")
    cfg_init_stub_cpu     = emit_service_stub(svc_cfg_init_cpu,     "cfg_init")
    ui_nmi_stub_cpu       = emit_service_stub(svc_menu_ui_nmi_cpu,  "ui_nmi")
    overlay_init_stub_cpu = emit_service_stub(svc_overlay_init_cpu, "overlay_init")
    log(f"  overlay_init_stub @${overlay_init_stub_cpu:04X}")

    # V5.23 Шаг 1: ma_init_data и init_ma_map_ram уже размещены в SAFE-2 выше.

    if stage >= 2:
        load_state_cpu = emit_service_stub(svc_load_state_cpu, "load_state")
        save_state_cpu = emit_service_stub(svc_save_state_cpu, "save_state")
    else:
        load_state_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code    += build_load_state(load_state_cpu,
                                          enable_ram_cpu, disable_ram_cpu,
                                          total_slots=list_total)
        save_state_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code    += build_save_state(save_state_cpu,
                                          enable_ram_cpu, disable_ram_cpu)

    fixed_splash_hook_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code           += build_fixed_splash_hook(fixed_splash_hook_cpu,
                                                    cfg_init_stub_cpu)

    # V5.21: fixed_nav_hook с проверкой SUBMENU_DIRTY==4 для snapshot в NMI
    fixed_nav_hook_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code        += build_fixed_nav_hook_v521(fixed_nav_hook_cpu,
                                                    save_state_cpu)

    # alt_ensure stub (для вызова после построения nametable)
    alt_ensure_stub_cpu = emit_service_stub(svc_prompt_alt_ensure_cpu, "alt_ensure")
    log(f"  alt_ensure_stub @${alt_ensure_stub_cpu:04X}")

    # V5.23 Шаг 2: stub для вызова svc_multi_alt_swap из main thread.
    # A на входе — screen number. PPU отключается ВНУТРИ функции.
    # V5.23.3: ранний выход ДО PHP/SEI/bank switching если экран не сменился —
    # это критично для сохранения APU IRQ темпа. Stub вызывается на каждом
    # JSR $C26C (который может быть много раз за кадр), и PHP/SEI на ~50 циклов
    # каждый раз накапливается до десятков циклов SEI в кадре, что приводит
    # к пропуску snd_update тиков и деградации звука.
    multi_alt_swap_stub_cpu = None
    if multi_alt_swap_cpu is not None:
        multi_alt_swap_stub_cpu = fixed_base_cpu + len(fixed_code)
        ma_stub_src = f"""
multi_alt_swap_stub:
    cmp ${MA_LOADED_SCREEN:04X}
    bne mass_changed
    rts                       ; экран не сменился — выходим без переключений
mass_changed:
    php
    sei
    pha
    lda #${SERVICE_BANK_INDEX:02X}
    jsr ${set_service_cpu:04X}
    pla
    jsr ${multi_alt_swap_cpu:04X}
    lda #$00
    jsr ${set_service_cpu:04X}
    plp
    rts
"""
        fixed_code += assemble_6502(ma_stub_src, multi_alt_swap_stub_cpu)
        log(f"  multi_alt_swap_stub @${multi_alt_swap_stub_cpu:04X}")

    # c26c_wrapper - вызывается вместо JSR $C26C в основном цикле меню
    c26c_wrapper_cpu = fixed_base_cpu + len(fixed_code)

    # V5.23 Шаг 2: c26c_wrapper упрощён — старая alt-логика заменена на
    # универсальный multi_alt_swap. На screen13 swap-таблица содержит
    # red-9 кастомы вместе с остальными.
    if multi_alt_swap_stub_cpu is not None:
        c26c_wrapper_src = f"""
c26c_wrapper:
    lda ${SCREEN_CACHE:04X}
    jsr ${multi_alt_swap_stub_cpu:04X}
    jsr $C26C
    rts
"""
    else:
        # Fallback: старая alt-логика (если multi_alt swap отключён)
        c26c_wrapper_src = f"""
c26c_wrapper:
    lda ${ALT_ACTIVE_FLAG:04X}
    beq c26c_chk_load
    lda ${SCREEN_CACHE:04X}
    cmp #$0D
    beq c26c_build
    jsr ${enable_ram_cpu:04X}
    lda #${SERVICE_BANK_INDEX:02X}
    jsr ${set_service_cpu:04X}
    jsr ${svc_prompt_alt_restore_cpu:04X}
    lda #$00
    jsr ${set_service_cpu:04X}
    jsr ${disable_ram_cpu:04X}
    jmp c26c_build
c26c_chk_load:
    lda ${SCREEN_CACHE:04X}
    cmp #$0D
    bne c26c_build
    jsr ${enable_ram_cpu:04X}
    jsr ${alt_ensure_stub_cpu:04X}
    jsr ${disable_ram_cpu:04X}
c26c_build:
    jsr $C26C
    rts
"""
    fixed_code += assemble_6502(c26c_wrapper_src, c26c_wrapper_cpu)
    log(f"  c26c_wrapper @${c26c_wrapper_cpu:04X}")

    # calc_screen_hook
    calc_screen_hook_cpu = fixed_base_cpu + len(fixed_code)
    calc_screen_hook_src = f"""
calc_screen_hook:
    jsr $C0EE
    sta ${SCREEN_CACHE:04X}
    rts
"""
    fixed_code += assemble_6502(calc_screen_hook_src, calc_screen_hook_cpu)
    log(f"  calc_screen_hook @${calc_screen_hook_cpu:04X}")

    # marquee
    marquee_reset_cpu   = None
    marquee_nmi_cpu     = None
    cursor_wrap_cpu     = None
    marquee_prepare_cpu = None
    if NAME_MODE == 2 and MARQUEE_ENABLED:
        marquee_reset_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code       += build_marquee_reset(marquee_reset_cpu)

        marquee_prepare_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code         += build_marquee_prepare(marquee_prepare_cpu,
                                                    fetch_dir_stub_cpu)

        marquee_nmi_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code     += build_marquee_nmi(marquee_nmi_cpu,
                                            ma_translate_cpu=ma_translate_cpu)

        cursor_wrap_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code     += build_update_cursor_wrapper(cursor_wrap_cpu,
                                                      marquee_prepare_cpu)

    combined_nmi_hook_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code           += build_fixed_combined_nmi_hook(
        combined_nmi_hook_cpu,
        ui_stub_cpu=ui_nmi_stub_cpu,
        marquee_nmi_cpu=marquee_nmi_cpu if (NAME_MODE == 2 and MARQUEE_ENABLED) else None
    )

    # V5.22: APU frame counter IRQ handler (snd_update в IRQ для непрерывной музыки)
    # V5.22.10: 3 р��жима темпа — auto / 60 / 50. Всегда читает CONSOLE_TYPE_RAM.
    irq_handler_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code     += build_irq_handler(irq_handler_cpu, music_rate=MUSIC_RATE)
    log(f"  irq_handler @${irq_handler_cpu:04X}  music_rate={MUSIC_RATE}")

    # V5.22.1: SEI-обёртки над функциями звукового движка ($FF36/$FF93/$FE0E/$FE9B).
    # Главный поток вызывает их под I=0, и наш APU IRQ может перехватить полу-обновлённое
    # состояние движка → зависание. Обёртки блокируют IRQ на время критической секции.
    safe_stop_music_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code         += build_safe_snd_stop_music(safe_stop_music_cpu)
    safe_stop_sfx_cpu  = fixed_base_cpu + len(fixed_code)
    fixed_code         += build_safe_snd_stop_sfx(safe_stop_sfx_cpu)
    safe_play_music_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code         += build_safe_snd_play_music(safe_play_music_cpu)
    safe_play_sfx_cpu  = fixed_base_cpu + len(fixed_code)
    fixed_code         += build_safe_snd_play_sfx(safe_play_sfx_cpu)
    log(f"  safe_snd_stop_music  @${safe_stop_music_cpu:04X}")
    log(f"  safe_snd_stop_sfx    @${safe_stop_sfx_cpu:04X}")
    log(f"  safe_snd_play_music  @${safe_play_music_cpu:04X}")
    log(f"  safe_snd_play_sfx    @${safe_play_sfx_cpu:04X}")

    load_save_stub_cpu  = emit_service_stub(service_load_cpu,  "load_save")
    flush_save_stub_cpu = emit_service_stub(service_flush_cpu, "flush_save")

    maybe_flush_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code     += build_maybe_flush_with_warning(maybe_flush_cpu,
                                                     enable_ram_cpu, disable_ram_cpu,
                                                     show_warning_cpu,
                                                     flush_save_stub_cpu)

    reset_init_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code    += build_reset_init_v43(reset_init_cpu, console_detect_cpu,
                                           cfg_init_stub_cpu=cfg_init_stub_cpu,
                                           init_ma_map_ram_cpu=init_ma_map_ram_cpu)

    ext_draw_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code  += build_ext_draw_list_line(ext_draw_cpu, num_games,
                                            fetch_dir_stub_cpu,
                                            div24_16_cpu=div24_16_cpu,
                                            ma_translate_cpu=ma_translate_cpu)

    menu_init_cpu = fixed_base_cpu + len(fixed_code)
    # V5.23 Шаг 2: если multi_alt swap включён, старая alt_ensure отключается
    # (multi_alt swap сам обрабатывает screen13 через SWAP_LIST[13]).
    _ma_alt_ensure = (None if multi_alt_swap_stub_cpu is not None
                      else alt_ensure_stub_cpu)
    fixed_code   += build_menu_init_wrapper(menu_init_cpu,
                                            maybe_flush_cpu, load_state_cpu,
                                            save_state_cpu, clear_work_cpu,
                                            recalc_cpu,
                                            marquee_reset_cpu=marquee_reset_cpu,
                                            overlay_init_stub_cpu=overlay_init_stub_cpu,
                                            alt_ensure_stub_cpu=_ma_alt_ensure,
                                            enable_ram_cpu=enable_ram_cpu,
                                            disable_ram_cpu=disable_ram_cpu,
                                            init_ma_map_ram_cpu=init_ma_map_ram_cpu,
                                            multi_alt_swap_stub_cpu=multi_alt_swap_stub_cpu)

    if REPEAT_LIST:
        wrap_page_down_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code        += build_nav_wrapper(wrap_page_down_cpu, "wrap_page_down",
                                               0xEBF8, save_state_cpu)
        wrap_page_up_cpu = fixed_base_cpu + len(fixed_code)
        fixed_code      += build_nav_wrapper(wrap_page_up_cpu, "wrap_page_up",
                                             0xEC58, save_state_cpu)
    else:
        # Без повторов: свои процедуры листания страницами (в оригинале предел 9999
        # зашит в код $EBF8/$EC58). Один общий блок с двумя точками входа; кладём в
        # SAFE-2, если там есть место, иначе в основное SAFE-окно.
        _np_s2_cpu = safe2_base_cpu + len(safe2_code)
        _np_code, _np_pd, _np_pu = build_nav_page_norepeat(_np_s2_cpu, list_total,
                                                           save_state_cpu)
        if len(safe2_code) + len(_np_code) <= safe2_size:
            safe2_code        += _np_code
            _np_where          = "SAFE-2"
        else:
            _np_fx_cpu = fixed_base_cpu + len(fixed_code)
            _np_code, _np_pd, _np_pu = build_nav_page_norepeat(_np_fx_cpu, list_total,
                                                               save_state_cpu)
            fixed_code        += _np_code
            _np_where          = "fixed"
        log(f"  листание страницами (без повторов): {len(_np_code)} байт, "
            f"down=${_np_pd:04X} up=${_np_pu:04X} ({_np_where})")
        wrap_page_down_cpu = _np_pd
        wrap_page_up_cpu   = _np_pu
    wrap_nav_dec_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code      += build_nav_wrapper(wrap_nav_dec_cpu, "wrap_nav_dec",
                                         0xEBA7, save_state_cpu)
    wrap_nav_inc_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code      += build_nav_wrapper(wrap_nav_inc_cpu, "wrap_nav_inc",
                                         0xEBD3, save_state_cpu)

    launcher_cpu = fixed_base_cpu + len(fixed_code)
    launcher     = build_launcher_v42()
    fixed_code  += launcher

    tramp_cpu  = fixed_base_cpu + len(fixed_code)
    fixed_code += build_trampoline(launcher_cpu, len(launcher))

    ext_launch_cpu = fixed_base_cpu + len(fixed_code)
    fixed_code    += build_ext_launch_game(
        ext_launch_cpu, tramp_cpu, save_state_cpu,
        load_save_stub_cpu, incompat_show_cpu, menu_init_cpu,
        fetch_stub_cpu=fetch_dir_stub_cpu,
        console_detect_cpu=(console_detect_cpu
                            if CONSOLE_DETECT_MODE == "launch" else None),
        pre_launch_clear_cpu=pre_launch_clear_cpu)

    if SAFE + len(fixed_code) > SAFE_END:
        log(f"  ❌ fixed bank overflow: {len(fixed_code)} > {SAFE_END - SAFE}")
        return False

    # V5.24: патчим scd_targets таблицу внутри fixed_code фактическими адресами.
    _scd_offset = scd_targets_cpu - fixed_base_cpu
    for _i, _tgt in enumerate(service_call_targets):
        fixed_code[_scd_offset + _i * 2    ] = _tgt        & 0xFF
        fixed_code[_scd_offset + _i * 2 + 1] = (_tgt >> 8) & 0xFF
    log(f"  service_call_targets: {len(service_call_targets)}/{SERVICE_CALL_TABLE_MAX} entries")

    bank0[SAFE:SAFE + len(fixed_code)] = fixed_code

    # V5.22.3: записать safe2_code в мёртвую зону $EECC..$EFBF.
    if len(safe2_code) > safe2_size:
        log(f"  ❌ SAFE-2 overflow: {len(safe2_code)} > {safe2_size}")
        return False
    if len(safe2_code) > 0:
        bank0[SAFE_2:SAFE_2 + len(safe2_code)] = safe2_code
        log(f"  SAFE-2: used={len(safe2_code)} free={safe2_size - len(safe2_code)} "
            f"total={safe2_size} ({100.0 * len(safe2_code) / safe2_size:.1f}%)")

    # =========================================================
    # PATCH BANK0
    # =========================================================
    bank0[0x13] = reset_init_cpu & 0xFF
    bank0[0x14] = (reset_init_cpu >> 8) & 0xFF

    # V5.22: IRQ-вектор $FFFE/$FFFF -> irq_handler (был $C001 = reset)
    # IRQ срабатывает от APU frame counter каждые 1/60 сек на NTSC.
    bank0[0x3FFE] = irq_handler_cpu & 0xFF
    bank0[0x3FFF] = (irq_handler_cpu >> 8) & 0xFF
    log(f"  IRQ vector $FFFE/$FFFF -> ${irq_handler_cpu:04X}")

    # V5.22: убрать JSR $FA67 на $C198 в оригинальном NMI handler.
    # snd_update теперь вызывается из irq_handler. Если оставить и в NMI, получим
    # двойной тик и завышенный темп музыки.
    # $C198: 20 67 FA  ->  EA EA EA
    # bank0 offset = 0xC198 - 0xC000 = 0x198
    if (bank0[0x0198] == 0x20 and
            bank0[0x0199] == 0x67 and
            bank0[0x019A] == 0xFA):
        bank0[0x0198] = 0xEA
        bank0[0x0199] = 0xEA
        bank0[0x019A] = 0xEA
        log("  JSR $FA67 в NMI ($C198) -> NOP×3 (тик музыки теперь только из IRQ)")
    else:
        log(f"  ⚠️ Ожидался JSR $FA67 на $C198, найдено: "
            f"{bank0[0x0198]:02X} {bank0[0x0199]:02X} {bank0[0x019A]:02X}")

    patch_jmp(bank0, 0xC01B, menu_init_cpu)
    patch_jmp(bank0, 0xC2EC, ext_draw_cpu)
    patch_jmp(bank0, 0xED29, ext_launch_cpu)

    # Патчим JSR $C26C в основном цикле меню ($C05A-$C104) на c26c_wrapper
    # Это обеспечит загрузку ALT при каждой смене на screen13
    c26c_in_loop = patch_abs_target_in_range_patch_all(
        bank0, 0xC05A, 0xC104,
        old_target_cpu=0xC26C,
        new_target_cpu=c26c_wrapper_cpu,
        allowed_opcodes=(0x20,)
    )
    log(f"  JSR $C26C -> c26c_wrapper (в основном цикле): {len(c26c_in_loop)} замен")
    if len(c26c_in_loop) == 0:
        log("  ⚠️ Не найден JSR $C26C в основном цикле меню!")

    patch_abs_target_in_range(bank0, 0xECF3, 0xED28, 0xEBF8, wrap_page_down_cpu)
    patch_abs_target_in_range(bank0, 0xECF3, 0xED28, 0xEC58, wrap_page_up_cpu)
    patch_abs_target_in_range(bank0, 0xECF3, 0xED28, 0xEBA7, wrap_nav_dec_cpu)
    patch_abs_target_in_range(bank0, 0xECF3, 0xED28, 0xEBD3, wrap_nav_inc_cpu)

    if not REPEAT_LIST:
        # Предел 9999 ($270F) зашит в оригинальном коде: конец списка при отрисовке
        # страницы ($C26C), заворот при движении вверх на нуле (nav dec $EBA7) и
        # заворот вниз на последней игре (nav inc $EBD3). Меняем на число игр.
        # Листание страницами заменено своими процедурами (см. выше).
        _lt_hi, _lt_lo = (list_total >> 8) & 0xFF, list_total & 0xFF
        _lt_patches = [
            (0xC2D6, 0xC9, 0x27, _lt_hi, "конец страницы $C26C hi"),
            (0xC2DC, 0xC9, 0x0F, _lt_lo, "конец страницы $C26C lo"),
            (0xEBB7, 0xA9, 0x27, _lt_hi, "nav dec: заворот hi"),
            (0xEBBB, 0xA9, 0x0F, _lt_lo, "nav dec: заворот lo"),
            (0xEBE7, 0xC9, 0x27, _lt_hi, "nav inc: предел hi"),
            (0xEBED, 0xC9, 0x0F, _lt_lo, "nav inc: предел lo"),
        ]
        _wait_orig = bytes([0xA9, 0x00, 0x85, 0x24, 0xA5, 0x24, 0xC9, 0x08, 0xD0, 0xFA, 0x60])
        if bytes(bank0[0xEC4D - 0xC000:0xEC4D - 0xC000 + len(_wait_orig)]) != _wait_orig:
            log("  ❌ REPEAT_LIST=False: по $EC4D нет ожидаемой паузы (LDA #0/STA $24/.../RTS), "
                "другая версия оригинальной ROM?")
            return False
        for _a, _opc, _old, _new, _what in _lt_patches:
            _o = _a - 0xC000
            if bank0[_o] != _opc or bank0[_o + 1] != _old:
                log(f"  ❌ REPEAT_LIST=False: по ${_a:04X} ({_what}) ожидалось "
                    f"{_opc:02X} {_old:02X}, найдено {bank0[_o]:02X} {bank0[_o + 1]:02X} "
                    f"(другая версия оригинальной ROM?)")
                return False
            bank0[_o + 1] = _new
        log(f"  REPEAT_LIST=False: предел списка в оригинальном коде = {list_total} "
            f"({len(_lt_patches)} патчей)")

    patch_bytes(bank0, 0xC193,
                bytes([0x20,
                       combined_nmi_hook_cpu & 0xFF,
                       (combined_nmi_hook_cpu >> 8) & 0xFF]))

    # V5.22.6: убираем оригинальные OAM addr + OAM DMA на $C189..$C192.
    # Они переехали в конец combined_nmi_hook чтобы синхронизировать спрайты
    # S/T overlay с текстом. Иначе DMA копирует старые значения OAM до того
    # как наш hook успеет их обновить → задержка 1 кадр на показ S/T.
    # Bank0 offsets: $C189-$C000=0x189 .. $C192-$C000=0x192 (10 байт)
    # Оригинал: A9 00 / 8D 03 20 / A9 02 / 8D 14 40
    expected_oam_dma = bytes([0xA9, 0x00, 0x8D, 0x03, 0x20,
                               0xA9, 0x02, 0x8D, 0x14, 0x40])
    if bytes(bank0[0x0189:0x0193]) == expected_oam_dma:
        for off in range(0x0189, 0x0193):
            bank0[off] = 0xEA  # NOP
        log("  Original OAM DMA at $C189..$C192 -> NOP×10 (DMA moved to combined_nmi_hook)")
    else:
        actual = ' '.join(f'{b:02X}' for b in bank0[0x0189:0x0193])
        log(f"  ⚠️ Ожидался OAM DMA на $C189: A9 00 8D 03 20 A9 02 8D 14 40, найдено: {actual}")

    nav_sites = patch_abs_target_in_range_patch_all(
        bank0, 0xC05A, 0xC104,
        old_target_cpu=0xECF3,
        new_target_cpu=fixed_nav_hook_cpu,
        allowed_opcodes=(0x20,)
    )
    if len(nav_sites) != 1:
        raise ValueError(f"expected 1 JSR $ECF3 in menu loop, got {len(nav_sites)}")
    log(f"  JSR $ECF3 (menu loop) -> fixed_nav_hook ${fixed_nav_hook_cpu:04X} "
        f"at ${nav_sites[0]:04X}")

    patch_abs_target_in_range(
        bank0, 0xC000, 0xC030,
        old_target_cpu=0xC4D9,
        new_target_cpu=fixed_splash_hook_cpu,
        allowed_opcodes=(0x20,)
    )

    if NAME_MODE == 2 and MARQUEE_ENABLED:
        patch_abs_target_in_range_all(bank0, 0xC05A, 0xC104,
                                      0xCB3A, cursor_wrap_cpu, expected=2)

    c0ee_count = patch_all_c0ee_in_bank0_before_safe(
        bank0, calc_screen_hook_cpu, SAFE)
    log(f"  JSR $C0EE -> calc_screen_hook: {c0ee_count} замен в bank0 (до SAFE)")
    if c0ee_count == 0:
        log("  ⚠️ Ни одной замены JSR $C0EE — проверьте bank0!")

    # =========================================================
    # ПАТЧ JSR $C41B В $C219 (фикс паузы музыки)
    # =========================================================
    # V5.22.7: Патч 2 (двухфазная смена экрана через scr_chg_trigger)
    # удалён — он искал JSR $C1A1 в $C05A-$C104, а в этом дампе ROM такой
    # инструкции в основном цикле нет. После миграции snd_update в APU IRQ
    # busy-wait в main thread больше не страшен для музыки.

    # Патч: убрать JSR $C41B внутри $C219 (20 1B C4 → EA EA EA)
    c219_off = cpu_to_bank0_off(0xC219)
    c26b_off = cpu_to_bank0_off(0xC26C)
    found_c41b = False
    for i in range(c219_off, c26b_off - 1):
        if bank0[i] == 0x20 and bank0[i+1] == 0x1B and bank0[i+2] == 0xC4:
            bank0[i]   = 0xEA  # NOP
            bank0[i+1] = 0xEA  # NOP
            bank0[i+2] = 0xEA  # NOP
            log(f"  JSR $C41B → NOP×3 в $C219 @ bank0[0x{i:04X}] ✓")
            found_c41b = True
            break
    if not found_c41b:
        log("  ⚠️ JSR $C41B в $C219 не найден — проверьте дизасм!")

    # STA $8008 -> STA $5005
    if (bank0[0x31C9] == 0x8D and bank0[0x31CA] == 0x08 and bank0[0x31CB] == 0x80):
        bank0[0x31CA] = 0x05
        bank0[0x31CB] = 0x50
        log("  STA $8008 → STA $5005 ✓")
    else:
        log("  ⚠️ STA $8008 не найдена по offset 0x31C9")

    # V5.22.1: перенаправляем все JSR в звуковой движок на SEI-обёртки.
    # Это защищает от reentrancy между главным потоком и нашим APU IRQ.
    # Диапазон: весь bank0 ($C000-$FFFF) ВКЛЮЧАЯ наш SAFE-код, КРОМЕ самих обёрток
    # (они сами делают JSR на оригинальные $FF36/$FF93/$FE0E/$FE9B).
    # patch_abs_target_in_range_patch_all не различает «исключения», поэтому
    # сначала патчим всё, потом восстанавливаем 4 байта внутри обёрток.

    # Сначала запоминаем offsets обёрток в bank0 (CPU - $C000)
    wrap_addrs = {
        safe_stop_music_cpu: 0xFF36,
        safe_stop_sfx_cpu:   0xFF93,
        safe_play_music_cpu: 0xFE0E,
        safe_play_sfx_cpu:   0xFE9B,
    }
    # Внутри каждой обёртки JSR opcode на +2, операнд (lo,hi) на +3,+4
    wrap_internal_jsr_offsets = {wcpu - 0xC000 + 3: orig_target
                                  for wcpu, orig_target in wrap_addrs.items()}

    # Патч JSR $FF36 -> safe_stop_music
    cnt = patch_abs_target_in_range_patch_all(
        bank0, 0xC000, 0xFFFF, 0xFF36, safe_stop_music_cpu, allowed_opcodes=(0x20,))
    log(f"  JSR $FF36 -> safe_snd_stop_music: {len(cnt)} замен")
    cnt = patch_abs_target_in_range_patch_all(
        bank0, 0xC000, 0xFFFF, 0xFF93, safe_stop_sfx_cpu, allowed_opcodes=(0x20,))
    log(f"  JSR $FF93 -> safe_snd_stop_sfx:  {len(cnt)} замен")
    cnt = patch_abs_target_in_range_patch_all(
        bank0, 0xC000, 0xFFFF, 0xFE0E, safe_play_music_cpu, allowed_opcodes=(0x20,))
    log(f"  JSR $FE0E -> safe_snd_play_music: {len(cnt)} замен")
    cnt = patch_abs_target_in_range_patch_all(
        bank0, 0xC000, 0xFFFF, 0xFE9B, safe_play_sfx_cpu, allowed_opcodes=(0x20,))
    log(f"  JSR $FE9B -> safe_snd_play_sfx:  {len(cnt)} замен")

    # Восстанавливаем JSR внутри самих обёрток (чтобы они вызывали оригинальный код)
    for offset, orig_target in wrap_internal_jsr_offsets.items():
        bank0[offset]     = orig_target & 0xFF
        bank0[offset + 1] = (orig_target >> 8) & 0xFF
    log("  обёртки восстановлены (внутренние JSR указывают на оригиналы)")

    flash[FIXED_BANK_OFFSET:FIXED_BANK_OFFSET + 16384] = bank0

    if not ROM_ROUND_TO_POW2:
        real_end = max(games_end, FIXED_BANK_OFFSET + 16384)
        flash = flash[:align_up(real_end, ROM_ALIGN)]
    total_flash = len(flash)

    # =========================================================
    # V5.24: NES 2.0 header с корректной кодировкой PRG size
    # =========================================================
    if OUTPUT_FORMAT == "nes":
        mapper = 342
        p16k   = total_flash // ROM_ALIGN

        # V5.24: portable PRG-size encoder.
        # linear для p16k <= 0xEFF, exponent-multiplier дл�� больше.
        # Ранее код безусловно писал линейное представление с b9.lo = p16k >> 8,
        # что для p16k > 0xEFF давало b9.lo >= 0x10 — невалидный NES 2.0 header
        # (nibble b9.lo занимает только биты [3:0]).
        b4, b9_lo, encoded_size = encode_prg_size(total_flash)

        # Диагностический лог
        if b9_lo == 0x0F:
            # exponent-multiplier форма
            e    = (b4 >> 2) & 0x3F
            m    = b4 & 3
            mult = 2 * m + 1
            log(f"  NES 2.0 header: PRG size = exponent-multiplier "
                f"E={e}, M={m} (2^{e}x{mult} = {encoded_size // 1024} KB), "
                f"real file = {total_flash // 1024} KB, "
                f"p16k=0x{p16k:04X}")
        else:
            log(f"  NES 2.0 header: PRG size = linear "
                f"b4=${b4:02X} b9.lo=${b9_lo:X} "
                f"({p16k} x 16K = {total_flash // 1024} KB)")

        # V5.26: сколько зарезервировано под сейвы и сколько осталось для игр
        _free_games = MAX_ROM_BYTES - games_end - save_bytes
        log(f"  Зарезервировано под сейвы: {save_bytes // 1024} КБ "
            f"({save_sector_count} × 128 КБ, {battery_count} battery-игр)")
        log(f"  Свободно для новых игр:    {max(0, _free_games) // 1024} КБ "
            f"(лимит {MAX_ROM_BYTES // 1024} − игры {games_end // 1024} "
            f"− сейвы {save_bytes // 1024})")

        hdr      = bytearray(16)
        hdr[0:4] = b"NES\x1a"
        hdr[4]   = b4
        hdr[5]   = 0
        hdr[6]   = ((mapper & 0x0F) << 4) | 0x02
        hdr[7]   = (mapper & 0xF0) | 0x08
        hdr[8]   = (mapper >> 8) & 0x0F
        hdr[9]   = b9_lo & 0x0F
        hdr[10]  = 0x90
        hdr[11]  = 0x0C
        output_data = hdr + flash
    else:
        output_data = flash

    with open(OUTPUT, "wb") as f:
        f.write(output_data)

    log("")
    log("=" * 72)
    log(f"  ✅ {OUTPUT}  ({len(output_data)//1024} KB)")
    log("Region space:")
    log_space("UNIQUE_DIR",             len(unique_dir),   UNIQUE_DIR_RESERVED)
    log_space("fixed free window",      len(fixed_code),   SAFE_END - SAFE)
    log_space("SAFE-2 ($EECC-$EFBF)",  len(safe2_code),   safe2_size)
    log_space("service bank",           len(service_code), SERVICE_BANK_SIZE)
    # V5.25: log the data bank (bank 2) usage if multi_alt was built.
    _bank2_used = len(multi_alt_swap_bank2) if multi_alt_swap_bank2 else 0
    log_space("data bank (swap_lists)", _bank2_used,       DATA_BANK_SIZE)
    # V5.26: итоговая строка статистики игр — в самом низу вывода
    log("")
    log(f"Уникальных игр: {num_games}  пропущено: {len(unsupported)}  "
        f"с фиксами: {fixed_count}  battery: {battery_count}")
    return True

# =============================================================================
# build_fixed_nav_hook_v521
# V5.19: убран вызов snapshot_sub_stub.
# Snapshot теперь делается внутри NMI через svc_snapshot_vblank.
# =============================================================================
# build_fixed_nav_hook_v520
# =============================================================================
# V5.21: убран вызов snapshot из nav_hook.
# Snapshot теперь делается в NMI vblank (svc_snapshot_vblank, без мигания).
# =============================================================================
def build_fixed_nav_hook_v521(origin_cpu: int,
                               save_state_stub_cpu: int) -> bytes:
    from options_prompt import (BTN_A, BTN_START, SECRET_MASK,
                                 SUBMENU_ACTIVE, SUBMENU_DIRTY,
                                 RESET_CURSOR_REQ, A_BLOCK_REQ,
                                 SUBMENU_EXIT_REQ)
    from config import (MENU_ACTIVE_FLAG, CFG_CACHE, SCREEN_CACHE,
                        SRAM_SIG0, PENDING_SAVE_ID)
    ORIG_NAV = 0xECF3

    src = f"""
fixed_nav_hook:
    ; --- A_BLOCK_REQ ---
    lda ${A_BLOCK_REQ:04X}
    beq fn_chk_reset

    lda $22
    and #${BTN_A:02X}
    bne fn_mask_a

    lda #$00
    sta ${A_BLOCK_REQ:04X}
    jmp fn_chk_reset

fn_mask_a:
    lda $22
    and #$7F
    sta $22
    lda $23
    and #$7F
    sta $23

fn_chk_reset:
    lda ${RESET_CURSOR_REQ:04X}
    beq fn_chk_submenu_nav

    lda #$00
    sta ${RESET_CURSOR_REQ:04X}

    sta $18
    sta $19
    sta $17

    lda #$01
    sta ${A_BLOCK_REQ:04X}

    jsr ${save_state_stub_cpu:04X}
    rts

fn_chk_submenu_nav:
    lda ${SUBMENU_ACTIVE:04X}
    beq fn_chk_secret
    ; в подменю — блокируем навигацию меню
    rts

fn_chk_secret:
    lda $22
    and #${SECRET_MASK:02X}
    cmp #${SECRET_MASK:02X}
    beq fn_call

    lda $22
    and #${BTN_START:02X}
    bne fn_block

fn_call:
    jsr ${ORIG_NAV:04X}
    rts

fn_block:
    rts
"""
    return assemble_6502(src, origin_cpu)

# =============================================================================
if __name__ == "__main__":
    sys.exit(0 if build() else 1)