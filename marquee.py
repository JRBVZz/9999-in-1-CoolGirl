# marquee.py
# Версия: 1.1 (убраны зависимости от build_submenu.py)
# Auto-generated from coolgirl_builder.py

from config import *
from utils import *
from asm6502 import *

def build_marquee_reset(origin_cpu):
    if NAME_MODE != 2 or not MARQUEE_ENABLED:
        return b""
    src = f"""
marquee_reset:
    lda #$00
    sta ${MARQ_ACTIVE:04X}
    sta ${RESTORE_PENDING:04X}
    lda #$FF
    sta ${MARQ_ROW:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_marquee_prepare(origin_cpu, fetch_stub_cpu):
    """
    Вызывается когда курсор переходит на новую строку.
    FIX: restore-буфер заполняем ИЗ СТАРОЙ СТРОКИ (DIR_ENTRY_BUF) ДО fetch новой.
    """
    if NAME_MODE != 2 or not MARQUEE_ENABLED:
        return b""
    src = f"""
marquee_prepare:
    ; если курсор на той же строке — ничего не делать
    lda $12
    cmp ${MARQ_ROW:04X}
    beq mp_done

    ; ── если старая строка крутилась — планируем restore ───────────
    lda ${MARQ_ACTIVE:04X}
    beq mp_no_restore

    ; row, которую надо восстановить
    lda ${MARQ_ROW:04X}
    sta ${RESTORE_ROW:04X}

    ; скопировать первые NAME_VISIBLE символов старой строки
    ; ВАЖНО: копируем ИЗ DIR_ENTRY_BUF (он ещё старый!)
    ldy #$00
mp_copy_old:
    lda ${DIR_ENTRY_BUF:04X},y
    bne mp_co_ok
    lda #$26          ; 0x00 (терминатор) -> пробел
mp_co_ok:
    sta ${RESTORE_BUF:04X},y
    iny
    cpy #${NAME_VISIBLE:02X}
    bne mp_copy_old

    lda #$01
    sta ${RESTORE_PENDING:04X}

mp_no_restore:
    ; ── перейти на новую строку ───────────────────────────────────
    lda $12
    sta ${MARQ_ROW:04X}
    lda #$00
    sta ${MARQ_ACTIVE:04X}
    sta ${MARQ_POS:04X}
    sta ${MARQ_FRAME:04X}

    ; получить DIR_INDEX для этой строки из кеша PAGE_DIRIDX_BASE
    lda $12
    asl a
    tay
    lda ${PAGE_DIRIDX_BASE:04X},y
    sta ${DIR_INDEX_LO:04X}
    iny
    lda ${PAGE_DIRIDX_BASE:04X},y
    sta ${DIR_INDEX_HI:04X}

    ; загрузить запись каталога в DIR_ENTRY_BUF (теперь уже новая)
    jsr ${fetch_stub_cpu:04X}

    ; посчитать длину имени (до 0x00 или до NAME_MAX=48)
    ldy #$00
mp_len_loop:
    lda ${DIR_ENTRY_BUF:04X},y
    beq mp_len_done
    iny
    cpy #${NAME_MAX:02X}
    bne mp_len_loop
mp_len_done:
    sty ${MARQ_LEN:04X}

    ; если имя <= NAME_VISIBLE — marquee не нужна
    cpy #${NAME_VISIBLE:02X}
    bcc mp_done
    beq mp_done

    ; активируем marquee
    lda #$01
    sta ${MARQ_ACTIVE:04X}

mp_done:
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_marquee_nmi(origin_cpu, ma_translate_cpu=None):
    """
    V5.2: ранний выход если MENU_ACTIVE_FLAG=0 (заставка или запуск игры).
    Таким образом marquee полностью не трогает PPU вне меню.
    
    V5.23 Шаг 1: ma_translate_cpu — helper подмены virtual byte → phys.
    """
    if NAME_MODE != 2 or not MARQUEE_ENABLED:
        return b""
    src = f"""
marquee_nmi:
    ; V5.2: не работаем вне меню — заставка работает самостоятельно
    lda ${MENU_ACTIVE_FLAG:04X}
    beq mn_exit

    ; --- restore предыдущей строки ---
    lda ${RESTORE_PENDING:04X}
    beq mn_no_restore
    lda ${RESTORE_ROW:04X}
    jsr write_restore_row
    lda #$00
    sta ${RESTORE_PENDING:04X}
mn_no_restore:

    ; --- обработка бегущей строки ---
    lda ${MARQ_ACTIVE:04X}
    beq mn_scroll_fix

    inc ${MARQ_FRAME:04X}
    lda ${MARQ_FRAME:04X}
    cmp #${MARQ_STEP_FRAMES:02X}
    bne mn_scroll_fix

    lda #$00
    sta ${MARQ_FRAME:04X}

    lda ${MARQ_ROW:04X}
    jsr write_marq_row

    lda ${MARQ_POS:04X}
    clc
    adc #$01
    sta ${MARQ_POS:04X}

    lda ${MARQ_POS:04X}
    cmp ${MARQ_LEN:04X}
    bcc mn_scroll_fix
    lda #$00
    sta ${MARQ_POS:04X}

mn_scroll_fix:
    ; восстановить scroll после записи в PPU
    lda $2002
    lda #$00
    sta $2005
    sta $2005

mn_exit:
    rts

; ── calc_ppu_addr(A=row) ─────────────────────────────────────────
calc_ppu_addr:
    sta ${MARQ_MUL_LO:04X}
    lda #$00
    sta ${MARQ_MUL_HI:04X}
    ldx #$05
mul_loop:
    asl ${MARQ_MUL_LO:04X}
    rol ${MARQ_MUL_HI:04X}
    dex
    bne mul_loop
    lda ${MARQ_MUL_LO:04X}
    clc
    adc #${0x40 + LIST_NAME_COL:02X}
    sta ${MARQ_PPU_LO:04X}
    lda ${MARQ_MUL_HI:04X}
    adc #$20
    sta ${MARQ_PPU_HI:04X}
    rts

; ── write_restore_row(A=row) ─────────────────────────────────────
write_restore_row:
    jsr calc_ppu_addr
    lda ${MARQ_PPU_HI:04X}
    sta $2006
    lda ${MARQ_PPU_LO:04X}
    sta $2006
    ldy #$00
wr_loop:
    lda ${RESTORE_BUF:04X},y
    {("jsr $%04X" % ma_translate_cpu) if ma_translate_cpu else ""}
    sta $2007
    iny
    cpy #${NAME_VISIBLE:02X}
    bne wr_loop
    rts

; ── write_marq_row(A=row) ────────────────────────────────────────
write_marq_row:
    jsr calc_ppu_addr
    lda ${MARQ_PPU_HI:04X}
    sta $2006
    lda ${MARQ_PPU_LO:04X}
    sta $2006
    lda ${MARQ_LEN:04X}
    sta ${MARQ_LEN_TMP:04X}
    ldx ${MARQ_POS:04X}
    ldy #${NAME_VISIBLE:02X}
wm_loop:
    txa
    cmp ${MARQ_LEN_TMP:04X}
    beq wm_space
    lda ${DIR_ENTRY_BUF:04X},x
    beq wm_space
    {("jsr $%04X" % ma_translate_cpu) if ma_translate_cpu else ""}
    sta $2007
    inx
    dey
    bne wm_loop
    rts
wm_space:
    lda #$26
    sta $2007
    ldx #$00
    dey
    bne wm_loop
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_nmi_hook(origin_cpu, marquee_nmi_cpu):
    if NAME_MODE != 2 or not MARQUEE_ENABLED:
        return b""
    src = f"""
nmi_hook:
    pha
    txa
    pha
    tya
    pha
    jsr ${marquee_nmi_cpu:04X}
    pla
    tay
    pla
    tax
    pla
    jsr $C5C0
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_update_cursor_wrapper(origin_cpu, marquee_prepare_cpu):
    if NAME_MODE != 2 or not MARQUEE_ENABLED:
        return b""
    src = f"""
update_cursor_wrapper:
    pha
    txa
    pha
    tya
    pha
    jsr $CB3A
    jsr ${marquee_prepare_cpu:04X}
    pla
    tay
    pla
    tax
    pla
    rts
"""
    return assemble_6502(src, origin_cpu)