# Auto-generated from coolgirl_builder.py
# Do not edit by hand unless you know what you are doing.

import os
import json
import zlib
import hashlib

from config import *
from utils import *

COOLGIRL_MAPPERS = {
    0:   {"mc":0,  "pm":7, "cm":0, "fl":0, "sram":True,  "pba":0},
    1:   {"mc":16, "pm":0, "cm":0, "fl":0, "sram":True,  "pba":0},
    2:   {"mc":1,  "pm":0, "cm":0, "fl":0, "sram":False, "pba":0},
    3:   {"mc":2,  "pm":0, "cm":0, "fl":0, "sram":True,  "pba":0},
    4:   {"mc":20, "pm":4, "cm":2, "fl":0, "sram":True,  "pba":0},
    5:   {"mc":15, "pm":4, "cm":7, "fl":0, "sram":True,  "pba":0},
    7:   {"mc":8,  "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    9:   {"mc":17, "pm":4, "cm":5, "fl":0, "sram":True,  "pba":0},
    10:  {"mc":17, "pm":0, "cm":5, "fl":1, "sram":True,  "pba":0},
    11:  {"mc":10, "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    18:  {"mc":7,  "pm":4, "cm":7, "fl":0, "sram":False, "pba":0},
    21:  {"mc":24, "pm":4, "cm":7, "fl":0, "sram":True,  "pba":0},
    22:  {"mc":24, "pm":4, "cm":7, "fl":3, "sram":True,  "pba":0},
    23:  {"mc":24, "pm":4, "cm":7, "fl":4, "sram":True,  "pba":0},
    25:  {"mc":24, "pm":4, "cm":7, "fl":5, "sram":True,  "pba":0},
    30:  {"mc":1,  "pm":0, "cm":0, "fl":0, "sram":False, "pba":0},
    32:  {"mc":26, "pm":4, "cm":7, "fl":0, "sram":True,  "pba":0},
    33:  {"mc":22, "pm":4, "cm":2, "fl":0, "sram":True,  "pba":0},
    34:  {"mc":8,  "pm":7, "cm":0, "fl":1, "sram":False, "pba":0},
    36:  {"mc":29, "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    38:  {"mc":32, "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    42:  {"mc":23, "pm":7, "cm":0, "fl":0, "sram":False, "pba":255},
    48:  {"mc":22, "pm":4, "cm":2, "fl":1, "sram":True,  "pba":0},
    65:  {"mc":14, "pm":4, "cm":7, "fl":0, "sram":False, "pba":0},
    66:  {"mc":11, "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    67:  {"mc":36, "pm":0, "cm":6, "fl":0, "sram":False, "pba":0},
    69:  {"mc":25, "pm":4, "cm":7, "fl":0, "sram":True,  "pba":0},
    70:  {"mc":30, "pm":0, "cm":0, "fl":0, "sram":False, "pba":0},
    71:  {"mc":1,  "pm":0, "cm":0, "fl":1, "sram":False, "pba":0},
    73:  {"mc":19, "pm":0, "cm":0, "fl":0, "sram":True,  "pba":0},
    75:  {"mc":34, "pm":4, "cm":4, "fl":0, "sram":False, "pba":0},
    78:  {"mc":3,  "pm":0, "cm":0, "fl":0, "sram":False, "pba":0},
    79:  {"mc":27, "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    83:  {"mc":35, "pm":4, "cm":7, "fl":0, "sram":False, "pba":0},
    87:  {"mc":12, "pm":0, "cm":0, "fl":0, "sram":False, "pba":0},
    89:  {"mc":37, "pm":0, "cm":0, "fl":0, "sram":False, "pba":0},
    90:  {"mc":13, "pm":4, "cm":7, "fl":0, "sram":False, "pba":0},
    93:  {"mc":5,  "pm":0, "cm":0, "fl":0, "sram":False, "pba":0},
    97:  {"mc":4,  "pm":1, "cm":0, "fl":0, "sram":False, "pba":0},
    112: {"mc":21, "pm":4, "cm":2, "fl":0, "sram":True,  "pba":0},
    118: {"mc":20, "pm":4, "cm":2, "fl":1, "sram":True,  "pba":0},
    133: {"mc":28, "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    146: {"mc":27, "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    152: {"mc":18, "pm":0, "cm":0, "fl":0, "sram":False, "pba":0},
    163: {"mc":6,  "pm":7, "cm":0, "fl":0, "sram":True,  "pba":0},
    184: {"mc":31, "pm":0, "cm":4, "fl":0, "sram":False, "pba":0},
    189: {"mc":20, "pm":7, "cm":2, "fl":2, "sram":False, "pba":0},
    206: {"mc":20, "pm":4, "cm":2, "fl":4, "sram":False, "pba":0},
    228: {"mc":9,  "pm":7, "cm":0, "fl":0, "sram":False, "pba":0},
    241: {"mc":8,  "pm":7, "cm":0, "fl":1, "sram":True,  "pba":0},
}

def load_nes_fixes():
    """Загружает nes-fixes.json и возвращает словарь со ВСЕМИ ключами:
       - 8-символьные ключи  = CRC32 PRG+CHR (для mapper/mirroring/compat фиксов)
       - 16-символьные ключи = SHA1-prefix (для battery-фиксов)
    Lookup в apply_fixes делается сначала по CRC32, затем по SHA1."""
    search = [NES_FIXES_FILE, os.path.join("tools", NES_FIXES_FILE)]
    for p in search:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Берём все ключи (8 и 16 символов).
                # Любые "странные" длины игнорируем — это защита от опечаток в JSON.
                entries = {k: v for k, v in data.items() if len(k) in (8, 16)}
                n_crc  = sum(1 for k in entries if len(k) == 8)
                n_sha1 = sum(1 for k in entries if len(k) == 16)
                log(f"  NES fixes: {p} (CRC32: {n_crc}, SHA1: {n_sha1})")
                return entries
            except Exception as e:
                log(f"  ⚠️ NES fixes: ошибка {p}: {e}")
                return {}
    log("  ℹ️ NES fixes: не найден")
    return {}

class NESRom:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.raw = f.read()
        self.path       = path
        self.name       = os.path.splitext(os.path.basename(path))[0]
        self.prg_count  = self.raw[4]
        self.chr_count  = self.raw[5]
        self.prg_size   = self.prg_count * 16384
        self.chr_size   = self.chr_count * 8192
        self.flags6     = self.raw[6]
        self.mapper     = (self.flags6 >> 4) | (self.raw[7] & 0xF0)
        self.mirror_v   = bool(self.flags6 & 1)
        self.battery    = bool(self.flags6 & 2)
        self.four_screen= bool(self.flags6 & 8)
        t = 512 if (self.flags6 & 4) else 0
        p = 16 + t
        self.prg = self.raw[p:p + self.prg_size]
        self.chr = self.raw[p + self.prg_size:p + self.prg_size + self.chr_size]
        self.uses_chr_ram = (self.chr_count == 0)
        rom_data      = self.raw[p:p + self.prg_size + self.chr_size]
        self.crc32    = zlib.crc32(rom_data) & 0xFFFFFFFF
        self.crc32_hex= f"{self.crc32:08x}"
        # SHA1-prefix для battery-фиксов из nes-fixes.json
        self.sha1_prefix = hashlib.sha1(rom_data).hexdigest()[:16]
        self.fixed    = False
        self.fix_desc = ""
        self.save_id  = 0
        self.compat_mask = 0
        # Размер PRG-RAM в КБ. Default 8 KB (стандарт для большинства battery-игр).
        # Для SUROM/SXROM (FF1+2 J, Koei-стратегии) переопределяется через nes-fixes.json.
        self.prg_ram_kb = 8
        # ClusterM-совместимое поведение: игры с 32 KB PRG-RAM пропускаются.
        # Verilog CoolGirl не имеет flags_for_32k_prg_ram ни для одного маппера.
        self.unsupported_32k_sram = False

    def apply_fixes(self, fixes_db):
        # Lookup сначала по CRC32 (для mapper/mirror/compat фиксов),
        # затем по SHA1-prefix (для battery-фиксов).
        # Если есть оба — мерджим (CRC32 как база, SHA1 дополняет).
        fix_crc  = fixes_db.get(self.crc32_hex)
        fix_sha1 = fixes_db.get(self.sha1_prefix)
        if not fix_crc and not fix_sha1:
            return
        # Объединяем (CRC32 имеет приоритет — переопределяет SHA1)
        fix = {}
        if fix_sha1:
            fix.update(fix_sha1)
        if fix_crc:
            fix.update(fix_crc)
        desc    = fix.get("description", "")
        changes = []
        if "mapper" in fix:
            old_m = self.mapper
            self.mapper = int(fix["mapper"])
            if old_m != self.mapper:
                changes.append(f"mapper:{old_m}→{self.mapper}")
        if "mirroring" in fix:
            mirr = fix["mirroring"]
            if mirr == "Vertical":
                self.mirror_v = True;  self.four_screen = False
            elif mirr == "Horizontal":
                self.mirror_v = False; self.four_screen = False
            elif mirr == "FourScreenVram":
                self.four_screen = True
            changes.append(f"mirror:{mirr}")
        if "battery" in fix:
            self.battery = bool(fix["battery"])
            changes.append(f"battery:{self.battery}")
        if "prg_ram_size" in fix:
            try:
                kb = int(fix["prg_ram_size"])
            except (TypeError, ValueError):
                kb = 8
            # 0 = "battery в header врёт, SRAM не нужен"
            if kb == 0:
                self.battery = False
                self.prg_ram_kb = 0
                changes.append("prg_ram:0KB(no-battery)")
            elif kb in (8, 16):
                self.prg_ram_kb = kb
                changes.append(f"prg_ram:{kb}KB")
            elif kb == 32:
                # ClusterM-совместимое поведение: 32 KB PRG-RAM
                # не поддерживается CoolGirl-Verilog (см. flags_for_32k_prg_ram=0
                # для всех мапперов в coolgirl-mappers.json).
                # Игра будет пропущена в builder.py с предупреждением.
                self.prg_ram_kb = 32
                self.unsupported_32k_sram = True
                changes.append("prg_ram:32KB(UNSUPPORTED)")
            else:
                # Неизвестный/нестандартный размер — игнорируем фикс
                changes.append(f"prg_ram:{kb}KB(IGNORED)")
        if fix.get("will_not_work_on_dendy"):
            self.compat_mask |= 0x0C
        if fix.get("will_not_work_on_ntsc"):
            self.compat_mask |= 0x01
        if fix.get("will_not_work_on_pal"):
            self.compat_mask |= 0x02
        if fix.get("will_not_work_on_new_dendy"):
            self.compat_mask |= 0x08
        if changes:
            self.fixed    = True
            self.fix_desc = f"[FIX {desc}: {', '.join(changes)}]"

def calc_prg_mask(size):
    banks = size // 16384
    return 0x7F if banks <= 1 else (~(banks - 1)) & 0x7F

def calc_chr_mask(size):
    if size <= 0:
        return 0x1F
    banks = size // 8192
    return 0x1F if banks <= 1 else (~(banks - 1)) & 0x1F

def calc_game_regs(game, prg_off, chr_off, actual_prg):
    cg  = COOLGIRL_MAPPERS.get(game.mapper, COOLGIRL_MAPPERS[0])
    mc  = cg["mc"];  pm  = cg["pm"];  cm  = cg["cm"]
    fl  = cg["fl"];  pba = cg.get("pba", 0)
    prg_ram = cg.get("sram", False) or game.battery

    # SUROM / SOROM / SXROM детект для MMC1 (mapper 1).
    # В CoolGirl-Verilog flag[0]=1 включает 16 KB PRG-RAM banking
    # через mmc1_load_register[4] -> sram_page.
    # Игры с >=16 KB SRAM или >256 KB PRG требуют этот режим:
    #   - Final Fantasy I & II (J)         (32 KB SRAM, 512 KB PRG)
    #   - Romance of the 3 Kingdoms        (16 KB SRAM)
    #   - Genghis Khan                      (16 KB SRAM)
    #   - Nobunaga's Ambition               (16 KB SRAM)
    #   - Best Play Pro Yakyuu Special     (32 KB SRAM)
    # Без flag[0]=1 такие игры либо не сохраняются, либо чёрный экран
    # на старте (FF1+2 J: критичные данные в верхней SRAM-странице).
    if game.mapper == 1:
        if game.prg_ram_kb >= 16 or game.prg_size > 256 * 1024:
            fl |= 1

    pb  = prg_off >> 14
    r0  = (pb >> 8) & 0xFF
    r1  = pb & 0xFF

    prg_mask = calc_prg_mask(actual_prg)
    chr_mask = calc_chr_mask(game.chr_size)
    chr_a18  = 1 if game.chr_size > 0 else 0

    r2 = (chr_a18 << 7) | (prg_mask & 0x7F)
    r3 = ((pm & 7) << 5)
    r4 = ((cm & 7) << 5) | (chr_mask & 0x1F)

    pba_bits = (pba >> 1) & 0x1F
    if game.battery:
        sram_page = 0x02
    elif prg_ram:
        sram_page = 0x01
    else:
        sram_page = 0x00

    r5 = (pba_bits << 2) | sram_page
    r6 = ((fl & 7) << 5) | (mc & 0x1F)

    mirr   = 0b00 if game.mirror_v else 0b01
    chr_wr = 1 if game.uses_chr_ram else 0
    sram_en= 1 if prg_ram else 0
    mc5    = (mc >> 5) & 1
    four_s = 1 if game.four_screen else 0
    r7 = (1 << 7) | (mc5 << 6) | (four_s << 5) | (mirr << 3) | (chr_wr << 1) | sram_en

    return bytes([r0, r1, r2, r3, r4, r5, r6, r7])

def build_unique_entry(game, regs, chr_h, chr_l, chr_s, chr_count, save_id):
    launch12 = regs + bytes([chr_h, chr_l, chr_s, chr_count])
    if len(launch12) != 12:
        raise ValueError("launch12 must be 12 bytes")

    if NAME_MODE == 1:
        name11 = encode_orig_menu_name_fixed(game.name, 11)
        entry  = (name11 + bytes([0x00]) + launch12 +
                  bytes([save_id & 0xFF]) + bytes([game.compat_mask & 0x0F]) + bytes(6))
        assert len(entry) == 32, f"entry size {len(entry)}"
        return entry
    else:
        name48 = encode_orig_menu_name_terminated(game.name, NAME_MAX)
        entry  = (name48 + launch12 +
                  bytes([save_id & 0xFF]) + bytes([game.compat_mask & 0x0F]) + bytes(2))
        assert len(entry) == 64, f"entry size {len(entry)}"
        return entry
