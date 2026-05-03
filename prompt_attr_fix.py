# prompt_attr_fix.py
# Версия: 2.2
#
# Откат чанкинга 2.1 (он давал постепенную отрисовку) + правильный фикс #1.
#
#   ПРИЧИНА разрыва фона при быстром START:
#     show/hide переключали рендер ($2001=0 -> запись -> $2001=$1E). При быстром
#     show<->hide и длинном NMI это переключение попадало в активный кадр -> фон.
#
#   ЛЕЧЕНИЕ 2.2:
#     - prompt снова рисуется ОДНИМ кадром (мгновенно, без «дорисовки»).
#     - НИ show, НИ hide больше НЕ трогают $2001 (нет источника разрыва).
#     - 19 тайлов + snapshot спокойно укладываются в vblank.
#     - В конце только сброс скролла ($2005 x2), без disable/enable рендера.
#     - svc_prompt_tick вернулся к лёгкому виду 2.0 (только сброс при смене экрана).
#     - Удалён кадровый автомат PROMPT_PHASE и байт $06F7 (снова свободен).

from config import *
from utils import *
from asm6502 import *

# =============================================================================
# Prompt layout
# =============================================================================

PROMPT_TEXT = "PRESS A FOR OPTIONS"
PROMPT_LEN  = len(PROMPT_TEXT)
PROMPT_ROW  = 19
PROMPT_COL  = (32 - PROMPT_LEN) // 2   # = 6
PROMPT_PPU  = 0x2000 + PROMPT_ROW * 32 + PROMPT_COL

# =============================================================================
# RAM state
# =============================================================================

ALT_ACTIVE_FLAG = 0x06E7

# =============================================================================
# SRAM layout for backups / temp
# =============================================================================

ALT_BACKUP_BASE = 0x7F00
TMP_TILE_BUF    = 0x7F90   # не пересекаться с SUB_SNAP_SRAM=$7FA0

# =============================================================================
# ALT tile layout
# =============================================================================

ALT_S = 0x27
ALT_A = 0x28
ALT_F = 0x29
ALT_O = 0x2A
ALT_R = 0x2B
ALT_P = 0x2C

ALT_C = 0x43
ALT_N = 0x48
ALT_T = 0x49

PROMPT_ALT_SPECS = (
    (0x1D, ALT_S, ALT_BACKUP_BASE + 0x00),
    (0x0B, ALT_A, ALT_BACKUP_BASE + 0x10),
    (0x10, ALT_F, ALT_BACKUP_BASE + 0x20),
    (0x19, ALT_O, ALT_BACKUP_BASE + 0x30),
    (0x1C, ALT_R, ALT_BACKUP_BASE + 0x40),
    (0x1A, ALT_P, ALT_BACKUP_BASE + 0x50),
    (0x0D, ALT_C, ALT_BACKUP_BASE + 0x60),
    (0x18, ALT_N, ALT_BACKUP_BASE + 0x70),
    (0x1E, ALT_T, ALT_BACKUP_BASE + 0x80),
)

# =============================================================================
# Prompt tiles
# =============================================================================

PROMPT_TILES_BASE = bytes([
    0x1A, 0x1C, 0x0F, 0x1D, 0x1D,  # P R E S S
    0x26, 0x0B, 0x26, 0x10, 0x19, 0x1C, 0x26,
    0x19, 0x1A, 0x1E, 0x13, 0x19, 0x18, 0x1D,
])

PROMPT_TILES_ALT = bytes([
    0x1A, 0x1C, 0x0F, 0x1D,
    ALT_S, 0x26, ALT_A, 0x26, ALT_F, ALT_O, ALT_R, 0x26, ALT_O, ALT_P,
    0x1E, 0x13, 0x19, 0x18, 0x1D,
])

# =============================================================================
# Helpers
# =============================================================================

def _ppu_addr(tile_id: int, base: int = 0x0000):
    addr = base + ((tile_id & 0xFF) << 4)
    return (addr >> 8) & 0xFF, addr & 0xFF

def _backup_tile_block(tile_id: int, backup_addr: int, label: str) -> str:
    hi, lo = _ppu_addr(tile_id)
    return f"""
    bit $2002
    lda #${hi:02X}
    sta $2006
    lda #${lo:02X}
    sta $2006
    lda $2007
    ldx #$00
{label}:
    lda $2007
    sta ${backup_addr:04X},x
    inx
    cpx #$10
    bne {label}
"""

def _gen_bg0_3_to_1_tile_block(dst_tile: int, src_buf_addr: int) -> str:
    hi, lo = _ppu_addr(dst_tile)
    lines = [
        f"""
    bit $2002
    lda #${hi:02X}
    sta $2006
    lda #${lo:02X}
    sta $2006
"""
    ]
    for y in range(8):
        p0 = src_buf_addr + y
        lines.append(f"    lda ${p0:04X}")
        lines.append("    sta $2007")
    for y in range(8):
        p0 = src_buf_addr + y
        p1 = src_buf_addr + 8 + y
        lines.append(f"    lda ${p0:04X}")
        lines.append("    eor #$FF")
        lines.append(f"    and ${p1:04X}")
        lines.append("    sta $2007")
    return "\n".join(lines) + "\n"

def _prompt_reset_scroll_source(prefix: str) -> str:
    # Сброс скролла без переключения $2001 (рендер остаётся включён).
    return f"""
{prefix}_reset_scroll:
    lda $2002
    lda #$00
    sta $2005
    sta $2005
    rts
"""

def _prompt_snapshot_source(prefix: str) -> str:
    return f"""
{prefix}_snapshot_prompt:
    bit $2002
    lda #${(PROMPT_PPU >> 8) & 0xFF:02X}
    sta $2006
    lda #${PROMPT_PPU & 0xFF:02X}
    sta $2006
    lda $2007
    ldx #$00
{prefix}_snapshot_loop:
    lda $2007
    sta ${OPTIONS_PROMPT_BUF:04X},x
    inx
    cpx #${PROMPT_LEN:02X}
    bne {prefix}_snapshot_loop
    rts
"""

def _prompt_restore_source(prefix: str) -> str:
    return f"""
{prefix}_restore_prompt:
    bit $2002
    lda #${(PROMPT_PPU >> 8) & 0xFF:02X}
    sta $2006
    lda #${PROMPT_PPU & 0xFF:02X}
    sta $2006
    ldx #$00
{prefix}_restore_loop:
    lda ${OPTIONS_PROMPT_BUF:04X},x
    sta $2007
    inx
    cpx #${PROMPT_LEN:02X}
    bne {prefix}_restore_loop
    rts
"""

def _prompt_draw_source(prefix: str, func_suffix: str, tiles: bytes) -> str:
    tbl = f"{prefix}_prompt_tiles_{func_suffix}"
    fn  = f"{prefix}_draw_prompt_{func_suffix}"
    return f"""
{fn}:
    bit $2002
    lda #${(PROMPT_PPU >> 8) & 0xFF:02X}
    sta $2006
    lda #${PROMPT_PPU & 0xFF:02X}
    sta $2006
    ldx #$00
{fn}_loop:
    lda {tbl},x
    sta $2007
    inx
    cpx #${PROMPT_LEN:02X}
    bne {fn}_loop
    rts

{tbl}:
    .byte {asm_byte_list(tiles)}
"""

def _cg_5007_helpers(prefix: str) -> str:
    return f"""
{prefix}_5007_alt_on:
    lda ${CFG5007:04X}
    ora #$03
    sta $5007
    rts

{prefix}_5007_chr_only:
    lda ${CFG5007:04X}
    ora #$02
    and #$FE
    sta $5007
    rts

{prefix}_5007_sram_only:
    lda ${CFG5007:04X}
    and #$FD
    ora #$01
    sta $5007
    rts

{prefix}_5007_restore_cfg:
    lda ${CFG5007:04X}
    sta $5007
    rts
"""

def _prompt_prepare_alt_source(prefix: str) -> str:
    parts = [f"""
{prefix}_prepare_alt:
"""]
    for i, (src_tile, dst_tile, backup_addr) in enumerate(PROMPT_ALT_SPECS):
        parts.append(_backup_tile_block(dst_tile, backup_addr,
                                        f"{prefix}_bk_dst_{i:02d}"))
        parts.append(_backup_tile_block(src_tile, TMP_TILE_BUF,
                                        f"{prefix}_bk_src_{i:02d}"))
        parts.append(_gen_bg0_3_to_1_tile_block(dst_tile, TMP_TILE_BUF))
    parts.append("    rts\n")
    return "".join(parts)

def _prompt_restore_alt_source(prefix: str) -> str:
    parts = [f"""
{prefix}_restore_alt:
"""]
    for i, (_, dst_tile, backup_addr) in enumerate(PROMPT_ALT_SPECS):
        hi, lo = _ppu_addr(dst_tile)
        parts.append(f"""
    bit $2002
    lda #${hi:02X}
    sta $2006
    lda #${lo:02X}
    sta $2006
    ldx #$00
{prefix}_rs_{i:02d}:
    lda ${backup_addr:04X},x
    sta $2007
    inx
    cpx #$10
    bne {prefix}_rs_{i:02d}
""")
    parts.append(f"""
    lda #$00
    sta ${ALT_ACTIVE_FLAG:04X}
    rts
""")
    return "".join(parts)

# =============================================================================
# Public builders
# =============================================================================

def build_service_prompt_alt_ensure(origin_cpu: int) -> bytes:
    """
    Готовит ALT-набор на screen13, ничего не рисует. Читает SCREEN_CACHE.
    A=1 если screen==13, A=0 иначе. (Без изменений по сравнению с 2.0/2.1.)
    """
    src = f"""
svc_prompt_alt_ensure:
    lda ${SCREEN_CACHE:04X}
    cmp #$0D
    bne pae_not13

    lda ${ALT_ACTIVE_FLAG:04X}
    bne pae_ready

    jsr pae_5007_alt_on
    jsr pae_prepare_alt
    lda #$01
    sta ${ALT_ACTIVE_FLAG:04X}

pae_ready:
    jsr pae_5007_sram_only
    lda #$01
    rts

pae_not13:
    lda #$00
    rts

pae_5007_alt_on:
    lda ${CFG5007:04X}
    ora #$03
    sta $5007
    rts

pae_5007_sram_only:
    lda ${CFG5007:04X}
    and #$FD
    ora #$01
    sta $5007
    rts
"""
    src += _prompt_prepare_alt_source("pae")
    return assemble_6502(src, origin_cpu)

def build_service_prompt_show(origin_cpu: int,
                              enable_cpu: int,
                              disable_cpu: int) -> bytes:
    """
    Фикс #1 (2.2): prompt рисуется ОДНИМ кадром, БЕЗ переключения $2001.
    19 тайлов + snapshot укладываются в vblank; в конце только сброс скролла.

    enable_cpu / disable_cpu не используются (рендер не отключается),
    оставлены в сигнатуре для совместимости с builder.py.
    """
    _ = (enable_cpu, disable_cpu)

    src = f"""
svc_prompt_show:
    lda ${OPTIONS_PROMPT_SHOWN:04X}
    bne ps_exit

    lda ${SCREEN_CACHE:04X}
    pha                 ; сохранить screen (0..13)

    cmp #$0D
    bne ps_base

    ; --- screen13: ALT-тайлы (уже подготовлены в calc_screen_hook) ---
    lda ${ALT_ACTIVE_FLAG:04X}
    beq ps_base         ; если не готовы — используем base
    jsr ps_5007_chr_only
    jsr ps_snapshot_prompt
    jsr ps_draw_prompt_alt
    jmp ps_set_shown

ps_base:
    jsr ps_5007_restore_cfg
    jsr ps_snapshot_prompt
    jsr ps_draw_prompt_base

ps_set_shown:
    ; OPTIONS_PROMPT_SHOWN = screen + 1 (1..14)
    pla
    clc
    adc #$01
    sta ${OPTIONS_PROMPT_SHOWN:04X}

    jsr ps_5007_restore_cfg
    jsr ps_reset_scroll
ps_exit:
    rts
"""
    src += _cg_5007_helpers("ps")
    src += _prompt_snapshot_source("ps")
    src += _prompt_draw_source("ps", "base", PROMPT_TILES_BASE)
    src += _prompt_draw_source("ps", "alt",  PROMPT_TILES_ALT)
    src += _prompt_reset_scroll_source("ps")
    return assemble_6502(src, origin_cpu)

def build_service_prompt_hide(origin_cpu: int,
                              enable_cpu: int,
                              disable_cpu: int) -> bytes:
    """
    Фикс #1 (2.2): hide восстанавливает строку ОДНИМ кадром, БЕЗ $2001.
    """
    _ = (enable_cpu, disable_cpu)

    src = f"""
svc_prompt_hide:
    lda ${OPTIONS_PROMPT_SHOWN:04X}
    beq ph_exit

    jsr ph_5007_restore_cfg
    jsr ph_restore_prompt

    lda #$00
    sta ${OPTIONS_PROMPT_SHOWN:04X}

    jsr ph_reset_scroll
ph_exit:
    rts
"""
    src += _cg_5007_helpers("ph")
    src += _prompt_restore_source("ph")
    src += _prompt_reset_scroll_source("ph")
    return assemble_6502(src, origin_cpu)

def build_service_prompt_tick(origin_cpu: int,
                              enable_cpu: int,
                              disable_cpu: int) -> bytes:
    """
    Лёгкий tick (как 2.0): если экран сменился — сбросить OPTIONS_PROMPT_SHOWN.
    """
    _ = (enable_cpu, disable_cpu)

    src = f"""
svc_prompt_tick:
    lda ${OPTIONS_PROMPT_SHOWN:04X}
    beq pt_exit

    lda ${SCREEN_CACHE:04X}
    clc
    adc #$01
    cmp ${OPTIONS_PROMPT_SHOWN:04X}
    beq pt_exit

    lda #$00
    sta ${OPTIONS_PROMPT_SHOWN:04X}

pt_exit:
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_prompt_alt_restore(origin_cpu: int,
                                     enable_cpu: int,
                                     disable_cpu: int) -> bytes:
    """
    Принудительное восстановление ALT-слотов из SRAM backup. (Без изменений.)
    """
    src = f"""
svc_prompt_alt_restore:
    lda ${ALT_ACTIVE_FLAG:04X}
    beq par_exit

    jsr ${enable_cpu:04X}
    lda ${CFG5007:04X}
    ora #$03
    sta $5007

    jsr par_restore_alt

    jsr ${disable_cpu:04X}
    lda ${CFG5007:04X}
    sta $5007

par_exit:
    rts
"""
    src += _prompt_restore_alt_source("par")
    return assemble_6502(src, origin_cpu)