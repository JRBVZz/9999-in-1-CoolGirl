# options_prompt.py
# Версия: 5.30 (Level 3 + предвзвод спрайта S/T на 1 кадр)
#
# V5.30: устранение «лишнего кадра» при появлении буквы S в подсказке START.
#        ПРИЧИНА: надпись пишется в nametable в том же NMI, где принимается
#        решение её показать, а спрайт-буква S взводится хуком только в конце
#        кадра и реально попадает в PPU следующим OAM DMA (DMA перенесён в
#        начало NMI в V5.27). Итог — 1 кадр, где видна «голая» буква фона
#        не того цвета, и лишь затем её накрывает спрайт.
#        РЕШЕНИЕ (двухстадийный предвзвод, флаг PROMPT_ARM_PENDING):
#          кадр 1 — взводим спрайт S (через хук), надпись НЕ рисуем;
#          кадр 2 — рисуем надпись prompt_show; спрайт как раз DMA'ится в
#                   начале этого кадра → буква и текст появляются ОДНОВРЕМЕННО.
#        Задержка показа подсказки = 1 кадр (~16 мс), незаметно.
#        Буква T (submenu) предвзвода не требует: после чанкинга спрайт T
#        приходит раньше, чем рисуется строка EXIT, «голой» буквы там нет.
#        Изменения только в build_service_menu_ui_nmi и build_fixed_combined_nmi_hook.
#
# V5.29 (Уровень 3): устранение мигания фона при входе в доп.меню и при
#        переключении настроек on/off.
#
#        ПРИЧИНА мигания: на «тяжёлом» кадре submenu_draw писал ~49 байт в
#        $2007 одним пакетом. После переноса OAM DMA в начало NMI (V5.27)
#        эти 49 записей + 514 тактов DMA вместе не помещались в один vblank
#        (~2273 такта NTSC) → часть записей $2007 вылезала за vblank →
#        разрыв/мигание фона. Пропуск sprite_engine (V5.28) не помогал —
#        он экономил такты RAM-работы, а не критичные такты $2007.
#
#        РЕШЕНИЕ (chunking): отрисовка submenu разбита на ОДИН регион за кадр.
#        Шаг хранится в SUBMENU_DIRTY:
#            0x00 = покой
#            0x04 = снапшот (один кадр; чтения фон не рвут)
#            0x08 = рисовать MUSIC   → 0x07
#            0x07 = рисовать INTRO   → 0x06
#            0x06 = рисовать FIRST+PAGE → 0x05
#            0x05 = рисовать EXIT    → 0x00 (готово → mu_force_cursor)
#        За кадр пишется максимум 19 байт в $2007 → вместе с OAM DMA всегда
#        влезает в vblank. Меню «дорисовывается» за ~5 кадров (≈85 мс) —
#        как быстрое появление, без мигания. Во время сборки ввод игнорируется
#        (DIRTY≠0), что убирает гонки.
#
#        Прочее по V5.29:
#        - combined_nmi_hook откатан к чистому V5.27 (OAM DMA в начале NMI,
#          без stack-флага «тяжёлого кадра» из V5.28 — он реагировал на
#          DIRTY==1, которого больше нет; это освобождает ~28 байт fixed-банка).
#        - snapshot_vblank в конце ставит DIRTY=0x08 (старт пошаговой отрисовки)
#          вместо DIRTY=1.
#        - на кадре снапшота вызывается text_overlay → палитра S/T корректна
#          сразу, без кадров «чужого» цвета во время сборки submenu.
#        - submenu_draw определяет screen13 по SCREEN_CACHE (==0x0D) в каждой
#          фазе; параметр alt_ensure_cpu сохранён в сигнатуре, но не используется
#          (актуальная конфигурация при активном multi_alt и так передаёт None).
#
# ----------------------------------------------------------------------------
# (история ниже — V5.20)
#
# Исправление проблемы с сохранением тайлов на всех экранах:
#
# РЕШЕНИЕ V5.20:
# 1) build_service_snapshot_vblank: PPU READ snapshot в vblank.
# 2) build_service_menu_ui_nmi: snapshot при SUBMENU_DIRTY==4.
# 3) SUB_SNAP буферы (0x7FA0-0x7FD2) содержат копию тайлов для восстановления.

from config import *
from utils import *
from asm6502 import *
from prompt_attr_fix import *

# ---------- Сдвигаем prompt на 1 тайл вправо ----------
PROMPT_COL = (32 - PROMPT_LEN) // 2
PROMPT_PPU = 0x2000 + PROMPT_ROW * 32 + PROMPT_COL
PROMPT_BYTES = bytes([encode_orig_menu_char(c) for c in PROMPT_TEXT])

# ---------- left submenu column ----------
OPT_ROW1 = 20
OPT_ROW2 = 21
OPT_COL  = 6
OPT_PPU1 = 0x2000 + OPT_ROW1 * 32 + OPT_COL
OPT_PPU2 = 0x2000 + OPT_ROW2 * 32 + OPT_COL

# ---------- right "button" FIRST / PAGE ----------
FP_ROW1 = 20
FP_COL1 = 18
FP_PPU1 = 0x2000 + FP_ROW1 * 32 + FP_COL1

FP_ROW2 = 21
FP_COL2 = FP_COL1
FP_PPU2 = 0x2000 + FP_ROW2 * 32 + FP_COL2

FIRST_TEXT  = "FIRST"
PAGE_TEXT   = "PAGE"
FIRST_LEN   = len(FIRST_TEXT)
PAGE_LEN    = len(PAGE_TEXT)
FP_WIDTH    = FIRST_LEN

FIRST_BYTES = bytes([encode_orig_menu_char(c) for c in FIRST_TEXT])
PAGE_BYTES  = bytes([encode_orig_menu_char(c) for c in PAGE_TEXT])

# ---------- exit hint ----------
EXIT_TEXT = "PRESS START TO EXIT"
EXIT_LEN  = len(EXIT_TEXT)

EXIT_ROW  = 23
EXIT_COL  = OPT_COL
EXIT_PPU  = 0x2000 + EXIT_ROW * 32 + EXIT_COL
EXIT_BYTES = bytes([encode_orig_menu_char(c) for c in EXIT_TEXT])

def _term_tiles(s: str) -> bytes:
    data = [encode_orig_menu_char(c) for c in s.upper()]
    data.append(0x00)
    return bytes(data)

def _term_tiles_overrides(s: str, overrides: dict) -> bytes:
    base = [encode_orig_menu_char(c) for c in s.upper()]
    for pos, tile in (overrides or {}).items():
        if 0 <= pos < len(base):
            base[pos] = tile & 0xFF
    base.append(0x00)
    return bytes(base)

OPT_LINE_LEN = 10
LINE_MUSIC_ON_T   = _term_tiles("MUSIC ON")
LINE_MUSIC_OFF_T  = _term_tiles("MUSIC OFF")
LINE_INTRO_ON_T   = _term_tiles("INTRO ON")
LINE_INTRO_OFF_T  = _term_tiles("INTRO OFF")

LINE_MUSIC_OFF_13_T = _term_tiles_overrides("MUSIC OFF", {
    4: ALT_C,
    6: ALT_O,
    7: ALT_F,
    8: ALT_F,
})
LINE_MUSIC_ON_13_T = _term_tiles_overrides("MUSIC ON", {
    4: ALT_C,
    6: ALT_O,
    7: ALT_N,
})
LINE_INTRO_OFF_13_T = _term_tiles_overrides("INTRO OFF", {
    4: ALT_O,
    6: ALT_O,
    7: ALT_F,
    8: ALT_F,
})
LINE_INTRO_ON_13_T = _term_tiles_overrides("INTRO ON", {
    4: ALT_O,
    6: ALT_O,
    7: ALT_N,
})

LINE_FIRST_T = _term_tiles("FIRST")
LINE_PAGE_T  = _term_tiles("PAGE")

EXIT_T = EXIT_BYTES + b"\x00"

EXIT_BYTES_13 = bytearray(EXIT_BYTES)
EXIT_BYTES_13[6] = ALT_S
EXIT_BYTES_13[7] = ALT_T
EXIT_BYTES_13[8] = ALT_A
EXIT_BYTES_13[9] = ALT_R
EXIT_BYTES_13 = bytes(EXIT_BYTES_13)

BTN_A     = 0x80
BTN_START = 0x10
BTN_UP    = 0x08
BTN_DOWN  = 0x04
BTN_LEFT  = 0x02
BTN_RIGHT = 0x01
SECRET_MASK = 0x52

CUR_OBJ_X_LO = 0x0402
CUR_OBJ_X_HI = 0x0403
CUR_OBJ_Y_LO = 0x0404
CUR_OBJ_Y_HI = 0x0405
CURSOR_X     = 0x1D

Y_OPT0 = (OPT_ROW1 * 8 - 2) & 0xFF
Y_OPT1 = (OPT_ROW2 * 8 - 2) & 0xFF
Y_FP   = ((Y_OPT0 + Y_OPT1) // 2) & 0xFF

CURSOR_LEFT_GAP_PX = (OPT_COL * 8) - CURSOR_X
CURSOR_X_RIGHT = (FP_COL1 * 8 - CURSOR_LEFT_GAP_PX) & 0xFF

SONG_ID_ZP     = 0x009D
ACTIVE_MASK_ZP = 0x0080
SHADOW_4015    = 0x0738
APU_4015       = 0x4015

CFG_MUSIC   = 0x01
CFG_SPLASH  = 0x02
CFG_MASK    = 0x03
CFG_DEFAULT = (CFG_MUSIC | CFG_SPLASH)

CFG_S_SIG0  = 0x7FF0
CFG_S_SIG1  = 0x7FF1
CFG_S_FLAGS = 0x7FF2
SIG0_VAL    = 0xC7
SIG1_VAL    = 0x2A

ORIG_SPLASH = 0xC4D9
ORIG_NAV    = 0xECF3

SND_STOP_MUSIC = 0xFF36
SND_STOP_SFX   = 0xFF93
SND_PLAY_MUSIC = 0xFE0E

# SNAP_VALID flag: 1 = SUB_SNAP буферы заполнены корректно
# Размещаем рядом с SUBMENU_DIRTY (0x0697)
SNAP_VALID = 0x069B  # свободный байт после OPTIONS_PROMPT_BUF (0x069A..0x06AC)

# V5.30: 1-кадровый предвзвод спрайта prompt (буква S) — чтобы спрайт-буква
# появлялась одновременно с надписью, а не на кадр позже.
# $06F6 свободен в mode=2 (config.py V5.24.1: "$06F6..$06F7 свободны").
PROMPT_ARM_PENDING = 0x06F6

SUB_SNAP_SRAM = 0x7FA0
SUB_SNAP_FP1  = SUB_SNAP_SRAM + 20
SUB_SNAP_FP2  = SUB_SNAP_SRAM + 25
SUB_SNAP_EXIT = SUB_SNAP_SRAM + 30

# =============================================================================
# Overlay tile IDs
# =============================================================================
OVL_TILE_S = 0xF7
OVL_TILE_T = 0xF8
OVL_ATTR_PAL3 = 0x03

OVL_X_PX = 24 * 8   # 192 = $C0
OVL_Y_PROMPT = (PROMPT_ROW * 8 - 1) & 0xFF
OVL_Y_EXIT   = (EXIT_ROW   * 8 - 1) & 0xFF

OVL_OAM_S_Y = 0x02F8
OVL_OAM_T_Y = 0x02FC

ATTR_PROMPT_LO = 0xE5
ATTR_EXIT_LO   = 0xED

DESC_BASE = 0xD574

TO_TMP0 = TEXT_OVL_TMP0
TO_TMP1 = TEXT_OVL_TMP1
TO_TMP2 = TEXT_OVL_TMP2
TO_TMP3 = TEXT_OVL_TMP3

# =============================================================================
# build_service_overlay_init — без изменений
# =============================================================================
def build_service_overlay_init(origin_cpu: int) -> bytes:
    src = f"""
svc_overlay_init:
    lda ${CFG5007:04X}
    pha
    ora #$03
    sta $5007

    bit $2002
    lda #$01
    sta $2006
    lda #$D0
    sta $2006
    lda $2007
    ldx #$00
oi_rs_loop:
    lda $2007
    sta ${TMP_TILE_BUF:04X},x
    inx
    cpx #$10
    bne oi_rs_loop

    bit $2002
    lda #$1F
    sta $2006
    lda #$70
    sta $2006
    ldx #$00
oi_ws_loop:
    lda ${TMP_TILE_BUF:04X},x
    sta $2007
    inx
    cpx #$10
    bne oi_ws_loop

    bit $2002
    lda #$01
    sta $2006
    lda #$E0
    sta $2006
    lda $2007
    ldx #$00
oi_rt_loop:
    lda $2007
    sta ${TMP_TILE_BUF:04X},x
    inx
    cpx #$10
    bne oi_rt_loop

    bit $2002
    lda #$1F
    sta $2006
    lda #$80
    sta $2006
    ldx #$00
oi_wt_loop:
    lda ${TMP_TILE_BUF:04X},x
    sta $2007
    inx
    cpx #$10
    bne oi_wt_loop

    pla
    sta $5007

    lda #$01
    sta ${TEXT_OVL_INIT:04X}

    ; Сбросить SNAP_VALID — снапшот будет сделан при следующем
    ; построении nametable (JSR $C26C) или в vblank
    lda #$00
    sta ${SNAP_VALID:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

# =============================================================================
# V5.22.7: build_service_snapshot_submenu и build_service_snapshot_from_sram
# удалены как dead code — нигде не вызываются. Актуальный путь снапшота —
# build_service_snapshot_vblank (PPU read в vblank, без render off).
# =============================================================================

# =============================================================================
# build_service_snapshot_vblank
# Вызывается из NMI когда SUBMENU_DIRTY==4.
# PPU read внутри vblank — безопасно без render off (чтения фон не рвут).
# V5.29: после успеха SNAP_VALID=1, DIRTY=0x08 (старт пошаговой отрисовки).
# =============================================================================
def build_service_snapshot_vblank(origin_cpu: int) -> bytes:
    src = f"""
svc_snapshot_vblank:
    ; включить SRAM
    lda ${CFG5007:04X}
    ora #$01
    sta $5007

    ; MUSIC bg (10 байт)
    bit $2002
    lda #${(OPT_PPU1 >> 8) & 0xFF:02X}
    sta $2006
    lda #${OPT_PPU1 & 0xFF:02X}
    sta $2006
    lda $2007          ; dummy
    ldx #$00
sv_l1:
    lda $2007
    sta ${SUB_SNAP_SRAM:04X},x
    inx
    cpx #$0A
    bne sv_l1

    ; INTRO bg (10 байт)
    bit $2002
    lda #${(OPT_PPU2 >> 8) & 0xFF:02X}
    sta $2006
    lda #${OPT_PPU2 & 0xFF:02X}
    sta $2006
    lda $2007
    ldx #$00
sv_l2:
    lda $2007
    sta ${SUB_SNAP_SRAM + 10:04X},x
    inx
    cpx #$0A
    bne sv_l2

    ; FIRST (5 байт)
    bit $2002
    lda #${(FP_PPU1 >> 8) & 0xFF:02X}
    sta $2006
    lda #${FP_PPU1 & 0xFF:02X}
    sta $2006
    lda $2007
    ldx #$00
sv_fp1:
    lda $2007
    sta ${SUB_SNAP_FP1:04X},x
    inx
    cpx #${FP_WIDTH:02X}
    bne sv_fp1

    ; PAGE (5 байт)
    bit $2002
    lda #${(FP_PPU2 >> 8) & 0xFF:02X}
    sta $2006
    lda #${FP_PPU2 & 0xFF:02X}
    sta $2006
    lda $2007
    ldx #$00
sv_fp2:
    lda $2007
    sta ${SUB_SNAP_FP2:04X},x
    inx
    cpx #${FP_WIDTH:02X}
    bne sv_fp2

    ; EXIT (19 байт)
    bit $2002
    lda #${(EXIT_PPU >> 8) & 0xFF:02X}
    sta $2006
    lda #${EXIT_PPU & 0xFF:02X}
    sta $2006
    lda $2007
    ldx #$00
sv_ex:
    lda $2007
    sta ${SUB_SNAP_EXIT:04X},x
    inx
    cpx #${EXIT_LEN:02X}
    bne sv_ex

    ; восстановить $5007
    lda ${CFG5007:04X}
    sta $5007

    ; восстановить PPU scroll (PPU toggle сброшен через bit $2002)
    lda $2002
    lda #$00
    sta $2005
    sta $2005

    ; готово — снапшот собран, запускаем пошаговое рисование (V5.29 Level 3)
    lda #$01
    sta ${SNAP_VALID:04X}
    lda #$08
    sta ${SUBMENU_DIRTY:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

# =============================================================================
# build_service_menu_ui_nmi
# V5.30: двухстадийный предвзвод спрайта S (PROMPT_ARM_PENDING) в mu_prompt_chk.
# V5.29 (Level 3):
# - mu_sub_step: диспетчер фаз. DIRTY 0=покой, 0x04=снапшот(1 кадр),
#   0x05..0x08=фазы рисования по регионам. После снапшота — jsr text_overlay
#   (палитра S/T сразу корректна во время сборки submenu).
# - mu_tgl_intro / mu_set_dirty: запуск рисования с DIRTY=0x08 (а не 0x01).
# =============================================================================
def build_service_menu_ui_nmi(origin_cpu: int,
                              cfg_init_cpu: int,
                              cfg_store_cpu: int,
                              prompt_show_cpu: int,
                              prompt_hide_cpu: int,
                              submenu_draw_cpu: int,
                              music_start_cpu: int,
                              music_stop_cpu: int,
                              fetch_dir_cpu: int,
                              prompt_tick_cpu: int,
                              text_overlay_cpu: int,
                              snapshot_vblank_cpu: int = 0) -> bytes:
    """
    snapshot_vblank_cpu: адрес svc_snapshot_vblank в service bank.
    """
    # V5.22.7: в mode=1 marquee выключен (нет MARQ_*), kick — пустой stub.
    if NAME_MODE == 1:
        kick_marquee_body = "    rts"
    else:
        kick_marquee_body = f"""    lda #$00
    sta ${RESTORE_PENDING:04X}
    sta ${MARQ_POS:04X}
    sta ${MARQ_FRAME:04X}
    lda $12
    sta ${MARQ_ROW:04X}
    lda $12
    asl a
    tay
    lda ${PAGE_DIRIDX_BASE:04X},y
    sta ${DIR_INDEX_LO:04X}
    iny
    lda ${PAGE_DIRIDX_BASE:04X},y
    sta ${DIR_INDEX_HI:04X}
    jsr ${fetch_dir_cpu:04X}
    ldy #$00
mk_len_loop:
    lda ${DIR_ENTRY_BUF:04X},y
    beq mk_len_done
    iny
    cpy #${NAME_MAX:02X}
    bne mk_len_loop
mk_len_done:
    sty ${MARQ_LEN:04X}
    cpy #${NAME_VISIBLE + 1:02X}
    bcc mk_inactive
    lda #$01
    sta ${MARQ_ACTIVE:04X}
    rts
mk_inactive:
    lda #$00
    sta ${MARQ_ACTIVE:04X}
    rts"""

    src = f"""
svc_menu_ui_nmi:
    jsr ${cfg_init_cpu:04X}

    lda ${MENU_ACTIVE_FLAG:04X}
    bne mu_in_menu
    lda #$00
    sta ${SUBMENU_ACTIVE:04X}
    sta ${SUBMENU_DIRTY:04X}
    sta ${OPTIONS_PROMPT_SHOWN:04X}
    sta ${MUSIC_PENDING:04X}
    sta ${SUBMENU_EXIT_REQ:04X}
    sta ${RESET_CURSOR_REQ:04X}
    sta ${A_BLOCK_REQ:04X}
    sta ${PROMPT_ARM_PENDING:04X}
    rts

mu_in_menu:
    jsr ${prompt_tick_cpu:04X}

    ; mute если музыка выключена
    lda ${CFG_CACHE:04X}
    and #${CFG_MUSIC:02X}
    bne mu_sound_ok
    lda #$00
    sta ${APU_4015:04X}
    sta ${SHADOW_4015:04X}
    sta ${ACTIVE_MASK_ZP:04X}
mu_sound_ok:

    lda ${SUBMENU_ACTIVE:04X}
    bne mu_submenu

    ; secret held -> hide prompt
    lda $22
    and #${SECRET_MASK:02X}
    cmp #${SECRET_MASK:02X}
    bne mu_check_enter
    jsr ${prompt_hide_cpu:04X}
    rts

mu_check_enter:
    lda ${SUBMENU_EXIT_REQ:04X}
    bne mu_prompt
    lda $22
    and #${BTN_START:02X}
    beq mu_prompt
    lda $23
    and #${BTN_A:02X}
    beq mu_prompt

    jsr ${prompt_hide_cpu:04X}
    lda #$00
    sta ${OPTIONS_PROMPT_SHOWN:04X}
    sta ${PROMPT_ARM_PENDING:04X}
    sta ${SUBMENU_EXIT_REQ:04X}

    lda #$01
    sta ${SUBMENU_ACTIVE:04X}
    lda #$00
    sta ${SUBMENU_POS:04X}

    ; DIRTY=4: нужен snapshot
    lda #$04
    sta ${SUBMENU_DIRTY:04X}

    lda #$00
    sta ${MUSIC_PENDING:04X}
    rts

mu_prompt:
    lda ${SUBMENU_EXIT_REQ:04X}
    beq mu_prompt_chk
    lda $22
    and #${BTN_START:02X}
    bne mu_prompt_done
    lda #$00
    sta ${SUBMENU_EXIT_REQ:04X}
mu_prompt_chk:

    lda $22
    and #${BTN_START:02X}
    beq mu_hide_prompt
    lda ${OPTIONS_PROMPT_SHOWN:04X}
    bne mu_prompt_done
    ; V5.30: двухстадийный предвзвод спрайта S.
    ; кадр 1: взвести спрайт (через хук), надпись НЕ рисуем;
    ; кадр 2: нарисовать надпись — спрайт DMA'ится в начале этого кадра,
    ; поэтому буква и надпись появляются одновременно.
    lda ${PROMPT_ARM_PENDING:04X}
    bne mu_prompt_draw
    lda #$01
    sta ${PROMPT_ARM_PENDING:04X}
    rts
mu_prompt_draw:
    lda #$00
    sta ${PROMPT_ARM_PENDING:04X}
    jsr ${prompt_show_cpu:04X}
mu_prompt_done:
    ; V5.22.5: text_overlay после prompt_show — palette синхронна с текстом
    jsr ${text_overlay_cpu:04X}
    rts
mu_hide_prompt:
    lda #$00
    sta ${PROMPT_ARM_PENDING:04X}
    jsr ${prompt_hide_cpu:04X}
    jsr ${text_overlay_cpu:04X}
    rts

mu_submenu:
    ; exit on START edge
    lda $23
    and #${BTN_START:02X}
    beq mu_sub_step
    jsr mu_restore_submenu
    lda #$01
    sta ${SUBMENU_EXIT_REQ:04X}
    jsr mu_kick_marquee
    rts

mu_sub_step:
    ; V5.29 (Level 3): 0=покой, 0x04=снапшот(1 кадр), 0x05..0x08=фазы рисования
    lda ${SUBMENU_DIRTY:04X}
    beq mu_sub_input
    cmp #$04
    bne mu_draw_phase
"""

    # Снапшот: один кадр; внутри snapshot_vblank ставит DIRTY=0x08.
    # Сразу выставляем палитру S/T (кадр снапшота имеет большой запас по vblank).
    if snapshot_vblank_cpu:
        src += f"""    jsr ${snapshot_vblank_cpu:04X}
    jsr ${text_overlay_cpu:04X}
    rts
"""
    else:
        src += f"""    lda #$08
    sta ${SUBMENU_DIRTY:04X}
    rts
"""

    src += f"""
mu_draw_phase:
    ; V5.31 BURST: рисуем ВСЕ регионы submenu за ОДИН кадр (мгновенно, без
    ; «дорисовки»). Снапшот уже отдельным кадром (DIRTY=0x04), поэтому здесь
    ; только записи (~49 байт $2007). Вместе с OAM DMA (514) укладывается в
    ; один vblank (~1.6k из 2273). OAM DMA на этом кадре НЕ пропускаем —
    ; спрайт T взведён прошлым кадром и DMA'ится в начале → буква не «голая».
    jsr ${submenu_draw_cpu:04X}
    lda ${SUBMENU_DIRTY:04X}
    bne mu_draw_phase
    jmp mu_force_cursor          ; все регионы нарисованы → курсор + палитра

mu_sub_input:
    lda $23
    and #${BTN_LEFT:02X}
    beq mu_chk_right
    lda ${SUBMENU_POS:04X}
    bpl mu_chk_right
    and #$7F
    sta ${SUBMENU_POS:04X}
    jmp mu_force_cursor

mu_chk_right:
    lda $23
    and #${BTN_RIGHT:02X}
    beq mu_chk_ud
    lda ${SUBMENU_POS:04X}
    bmi mu_chk_ud
    ora #$80
    sta ${SUBMENU_POS:04X}
    jmp mu_force_cursor

mu_chk_ud:
    lda ${SUBMENU_POS:04X}
    bmi mu_chk_toggle

    lda $23
    and #${BTN_UP:02X}
    beq mu_chk_down
    lda #$00
    sta ${SUBMENU_POS:04X}
    jmp mu_force_cursor
mu_chk_down:
    lda $23
    and #${BTN_DOWN:02X}
    beq mu_chk_toggle
    lda #$01
    sta ${SUBMENU_POS:04X}
    jmp mu_force_cursor

mu_chk_toggle:
    lda $23
    and #${BTN_A:02X}
    beq mu_pending_tick

    lda ${SUBMENU_POS:04X}
    bmi mu_reset_to_start

    and #$01
    beq mu_tgl_music

mu_tgl_intro:
    lda ${CFG_CACHE:04X}
    eor #${CFG_SPLASH:02X}
    and #${CFG_MASK:02X}
    sta ${CFG_CACHE:04X}
    jsr ${cfg_store_cpu:04X}
    lda #$08
    sta ${SUBMENU_DIRTY:04X}
    jmp mu_force_cursor

mu_tgl_music:
    lda ${CFG_CACHE:04X}
    eor #${CFG_MUSIC:02X}
    and #${CFG_MASK:02X}
    sta ${CFG_CACHE:04X}
    jsr ${cfg_store_cpu:04X}
    lda ${CFG_CACHE:04X}
    and #${CFG_MUSIC:02X}
    bne mu_set_pending_on
    lda #$02
    sta ${MUSIC_PENDING:04X}
    jmp mu_set_dirty
mu_set_pending_on:
    lda #$01
    sta ${MUSIC_PENDING:04X}

mu_set_dirty:
    lda #$08
    sta ${SUBMENU_DIRTY:04X}
    jmp mu_force_cursor

mu_reset_to_start:
    jsr mu_restore_submenu

    lda #$01
    sta ${RESET_CURSOR_REQ:04X}
    sta ${A_BLOCK_REQ:04X}

    lda #$00
    sta $22
    sta $23

    lda #$01
    sta ${SUBMENU_EXIT_REQ:04X}
    jsr mu_kick_marquee
    rts

mu_pending_tick:
    lda ${MUSIC_PENDING:04X}
    beq mu_force_cursor
    cmp #$01
    beq mu_do_start
    jsr ${music_stop_cpu:04X}
    lda #$00
    sta ${MUSIC_PENDING:04X}
    jmp mu_force_cursor
mu_do_start:
    jsr ${music_start_cpu:04X}
    lda #$00
    sta ${MUSIC_PENDING:04X}

mu_force_cursor:
    lda ${SUBMENU_POS:04X}
    bmi mu_right

    lda #${CURSOR_X:02X}
    sta ${CUR_OBJ_X_LO:04X}
    lda #$00
    sta ${CUR_OBJ_X_HI:04X}

    lda ${SUBMENU_POS:04X}
    and #$01
    beq mu_y0
    lda #${Y_OPT1:02X}
    jmp mu_sety
mu_y0:
    lda #${Y_OPT0:02X}
mu_sety:
    sta ${CUR_OBJ_Y_LO:04X}
    lda #$00
    sta ${CUR_OBJ_Y_HI:04X}
    ; V5.22.5: palette T в submenu синхронно
    jsr ${text_overlay_cpu:04X}
    rts

mu_right:
    lda #${CURSOR_X_RIGHT:02X}
    sta ${CUR_OBJ_X_LO:04X}
    lda #$00
    sta ${CUR_OBJ_X_HI:04X}
    lda #${Y_FP:02X}
    sta ${CUR_OBJ_Y_LO:04X}
    lda #$00
    sta ${CUR_OBJ_Y_HI:04X}
    jsr ${text_overlay_cpu:04X}
    rts

mu_restore_submenu:
    lda ${CFG5007:04X}
    ora #$01
    sta $5007

    ; MUSIC
    bit $2002
    lda #${(OPT_PPU1 >> 8) & 0xFF:02X}
    sta $2006
    lda #${OPT_PPU1 & 0xFF:02X}
    sta $2006
    ldx #$00
mrs_l1:
    lda ${SUB_SNAP_SRAM:04X},x
    sta $2007
    inx
    cpx #$0A
    bne mrs_l1

    ; INTRO
    bit $2002
    lda #${(OPT_PPU2 >> 8) & 0xFF:02X}
    sta $2006
    lda #${OPT_PPU2 & 0xFF:02X}
    sta $2006
    ldx #$00
mrs_l2:
    lda ${SUB_SNAP_SRAM + 10:04X},x
    sta $2007
    inx
    cpx #$0A
    bne mrs_l2

    ; FIRST (5)
    bit $2002
    lda #${(FP_PPU1 >> 8) & 0xFF:02X}
    sta $2006
    lda #${FP_PPU1 & 0xFF:02X}
    sta $2006
    ldx #$00
mrs_fp1:
    lda ${SUB_SNAP_FP1:04X},x
    sta $2007
    inx
    cpx #${FP_WIDTH:02X}
    bne mrs_fp1

    ; PAGE (5)
    bit $2002
    lda #${(FP_PPU2 >> 8) & 0xFF:02X}
    sta $2006
    lda #${FP_PPU2 & 0xFF:02X}
    sta $2006
    ldx #$00
mrs_fp2:
    lda ${SUB_SNAP_FP2:04X},x
    sta $2007
    inx
    cpx #${FP_WIDTH:02X}
    bne mrs_fp2

    ; EXIT (19)
    bit $2002
    lda #${(EXIT_PPU >> 8) & 0xFF:02X}
    sta $2006
    lda #${EXIT_PPU & 0xFF:02X}
    sta $2006
    ldx #$00
mrs_ex:
    lda ${SUB_SNAP_EXIT:04X},x
    sta $2007
    inx
    cpx #${EXIT_LEN:02X}
    bne mrs_ex

    lda ${CFG5007:04X}
    sta $5007

    lda $2002
    lda #$00
    sta $2005
    sta $2005

    lda #$00
    sta ${SUBMENU_ACTIVE:04X}
    sta ${SUBMENU_DIRTY:04X}
    sta ${MUSIC_PENDING:04X}
    ; SNAP_VALID оставляем — снапшот ещё актуален до следующей смены nametable

    lda $12
    asl a
    asl a
    asl a
    clc
    adc #$0E
    sta ${CUR_OBJ_Y_LO:04X}
    lda #$00
    sta ${CUR_OBJ_Y_HI:04X}
    lda #${CURSOR_X:02X}
    sta ${CUR_OBJ_X_LO:04X}
    lda #$00
    sta ${CUR_OBJ_X_HI:04X}
    rts

mu_kick_marquee:
{kick_marquee_body}
"""
    return assemble_6502(src, origin_cpu)

# =============================================================================
# build_fixed_combined_nmi_hook
# V5.32: освобождаем vblank на «тяжёлых» кадрах фона:
#   1) submenu burst-draw (SUBMENU_DIRTY>=5) → пропуск OAM DMA (514 т.).
#      T-спрайт уже в PPU OAM с кадра снапшота, «голой» буквы нет.
#   2) кадр появления prompt (PROMPT_ARM_PENDING!=0) → пропуск marquee,
#      чтобы marquee+prompt_show не перебирали vblank (OAM DMA оставляем —
#      нужен для синхронности спрайта S с надписью).
# V5.30: спрайт S взводится при OPTIONS_PROMPT_SHOWN или PROMPT_ARM_PENDING.
# =============================================================================
def build_fixed_combined_nmi_hook(origin_cpu: int,
                                  ui_stub_cpu: int,
                                  sprite_engine_cpu: int = 0xC5C0,
                                  marquee_nmi_cpu=None) -> bytes:
    marq = f"    jsr ${marquee_nmi_cpu:04X}\n" if marquee_nmi_cpu is not None else ""
    src = f"""
combined_nmi_hook:
    pha
    txa
    pha
    tya
    pha
    ; V5.22.1: блокируем IRQ на время NMI (RTI восстановит I из стека).
    sei

    ; ===== OAM DMA в начале NMI — кроме кадра burst-отрисовки submenu =====
    ; V5.32: если SUBMENU_ACTIVE и SUBMENU_DIRTY>=5 — пропускаем OAM DMA,
    ; освобождая 514 тактов vblank под полную запись submenu (~49 байт $2007).
    ; Спрайты держат прежний OAM 1 кадр; T уже в PPU OAM с кадра снапшота.
    lda ${MENU_ACTIVE_FLAG:04X}
    beq cn_do_dma
    lda ${SUBMENU_ACTIVE:04X}
    beq cn_do_dma
    lda ${SUBMENU_DIRTY:04X}
    cmp #$05
    bcs cn_after_dma
cn_do_dma:
    lda #$00
    sta $2003
    lda #$02
    sta $4014            ; OAM DMA (514 тактов)
cn_after_dma:

    lda ${MENU_ACTIVE_FLAG:04X}
    beq cn_ui_done

    lda ${SUBMENU_ACTIVE:04X}
    bne cn_no_marq
    ; V5.32: на кадре появления prompt (взведён предвзвод S) пропускаем
    ; marquee — иначе marquee + prompt_show в одном vblank → моргание фона.
    lda ${PROMPT_ARM_PENDING:04X}
    bne cn_no_marq
{marq}
cn_no_marq:
    jsr ${ui_stub_cpu:04X}

cn_ui_done:
    ; ===== строим OAM (курсор/костёр) для DMA в СЛЕДУЮЩЕМ кадре =====
    lda ${MENU_ACTIVE_FLAG:04X}
    beq cn_skip_sprite
    jsr ${sprite_engine_cpu:04X}
cn_skip_sprite:

    ; ===== overlay OAM (2 спрайта) — попадут в PPU следующим DMA =====
    lda #$F8
    sta ${OVL_OAM_S_Y:04X}
    sta ${OVL_OAM_T_Y:04X}

    lda ${MENU_ACTIVE_FLAG:04X}
    beq cn_ovl_done

    lda ${SUBMENU_ACTIVE:04X}
    bne cn_show_exit

    ; V5.30: взводим S при показанной подсказке ИЛИ при предвзводе (1 кадр)
    lda ${OPTIONS_PROMPT_SHOWN:04X}
    bne cn_arm_s
    lda ${PROMPT_ARM_PENDING:04X}
    beq cn_ovl_done
cn_arm_s:
    lda #${OVL_Y_PROMPT:02X}
    sta ${OVL_OAM_S_Y:04X}
    lda #${OVL_TILE_S:02X}
    sta ${OVL_OAM_S_Y+1:04X}
    lda #${OVL_ATTR_PAL3:02X}
    sta ${OVL_OAM_S_Y+2:04X}
    lda #${OVL_X_PX:02X}
    sta ${OVL_OAM_S_Y+3:04X}
    jmp cn_ovl_done

cn_show_exit:
    lda #${OVL_Y_EXIT:02X}
    sta ${OVL_OAM_T_Y:04X}
    lda #${OVL_TILE_T:02X}
    sta ${OVL_OAM_T_Y+1:04X}
    lda #${OVL_ATTR_PAL3:02X}
    sta ${OVL_OAM_T_Y+2:04X}
    lda #${OVL_X_PX:02X}
    sta ${OVL_OAM_T_Y+3:04X}

cn_ovl_done:
    pla
    tay
    pla
    tax
    pla
    rts
"""
    return assemble_6502(src, origin_cpu)

# =============================================================================
# Остальные builders — без изменений относительно V5.16
# =============================================================================

def build_service_cfg_init(origin_cpu: int, enable_cpu: int, disable_cpu: int) -> bytes:
    src = f"""
svc_cfg_init:
    lda ${CFG_INIT_DONE:04X}
    bne cfi_exit
    jsr ${enable_cpu:04X}

    lda ${CFG_S_SIG0:04X}
    cmp #${SIG0_VAL:02X}
    bne cfi_default
    lda ${CFG_S_SIG1:04X}
    cmp #${SIG1_VAL:02X}
    bne cfi_default

    lda ${CFG_S_FLAGS:04X}
    and #${CFG_MASK:02X}
    sta ${CFG_CACHE:04X}
    lda #$01
    sta ${CFG_BACKEND:04X}
    jmp cfi_done

cfi_default:
    lda #${CFG_DEFAULT:02X}
    sta ${CFG_CACHE:04X}

    lda #${SIG0_VAL:02X}
    sta ${CFG_S_SIG0:04X}
    lda #${SIG1_VAL:02X}
    sta ${CFG_S_SIG1:04X}
    lda #${CFG_DEFAULT:02X}
    sta ${CFG_S_FLAGS:04X}

    lda ${CFG_S_SIG0:04X}
    cmp #${SIG0_VAL:02X}
    bne cfi_no_sram
    lda ${CFG_S_SIG1:04X}
    cmp #${SIG1_VAL:02X}
    bne cfi_no_sram

    lda #$01
    sta ${CFG_BACKEND:04X}
    jmp cfi_done

cfi_no_sram:
    lda #$00
    sta ${CFG_BACKEND:04X}

cfi_done:
    jsr ${disable_cpu:04X}
    lda #$01
    sta ${CFG_INIT_DONE:04X}
cfi_exit:
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_cfg_store(origin_cpu: int, enable_cpu: int, disable_cpu: int) -> bytes:
    src = f"""
svc_cfg_store:
    lda ${CFG_BACKEND:04X}
    bne cfs_do
    rts
cfs_do:
    jsr ${enable_cpu:04X}
    lda #${SIG0_VAL:02X}
    sta ${CFG_S_SIG0:04X}
    lda #${SIG1_VAL:02X}
    sta ${CFG_S_SIG1:04X}
    lda ${CFG_CACHE:04X}
    and #${CFG_MASK:02X}
    sta ${CFG_S_FLAGS:04X}
    jsr ${disable_cpu:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_submenu_draw(origin_cpu: int, alt_ensure_cpu: int = None) -> bytes:
    """
    V5.29 (Level 3): пошаговая отрисовка submenu — ОДИН регион за кадр.
    Шаг хранится в SUBMENU_DIRTY: 0x08=MUSIC, 0x07=INTRO, 0x06=FIRST+PAGE,
    0x05=EXIT, 0x00=готово. За кадр пишется <=19 байт в $2007 → вместе с
    OAM DMA всегда влезает в vblank (фон не рвётся).

    screen13 определяется по SCREEN_CACHE (==0x0D) в каждой фазе (sd_calc13),
    что соответствует поведению при alt_ensure_cpu=None. Параметр alt_ensure_cpu
    сохранён для совместимости сигнатуры, но не используется.

    Каждая фаза: включает SRAM, ставит $2006 на свой регион, пишет данные,
    затем восстанавливает $5007 и сбрасывает scroll ($2005=0,0) — обязательно,
    т.к. после NMI продолжится рендер.
    """
    src = f"""
svc_submenu_draw:
    lda ${CFG5007:04X}
    ora #$01
    sta $5007

    lda ${SUBMENU_DIRTY:04X}
    cmp #$08
    bne sd_disp_07
    jmp sd_ph_music
    sd_disp_07:
    cmp #$07
    bne sd_disp_06
    jmp sd_ph_intro
    sd_disp_06:
    cmp #$06
    bne sd_disp_05
    jmp sd_ph_fp
    sd_disp_05:
    jmp sd_ph_exit

; ------------------------------------------------------------ MUSIC (0x08)
sd_ph_music:
    jsr sd_calc13
    pha
    bit $2002
    lda #${(OPT_PPU1 >> 8) & 0xFF:02X}
    sta $2006
    lda #${OPT_PPU1 & 0xFF:02X}
    sta $2006

    lda ${CFG_CACHE:04X}
    and #${CFG_MUSIC:02X}
    bne sd_music_on

sd_music_off:
    pla
    pha
    beq sd_music_off_base
sd_music_off_13:
    ldx #$00
    ldy #$00
sd_moff13_loop:
    tya
    bne sd_moff13_bg
    lda sd_moff13_txt,x
    beq sd_moff13_setend
    sta $2007
    jmp sd_moff13_next
sd_moff13_setend:
    iny
sd_moff13_bg:
    lda ${SUB_SNAP_SRAM:04X},x
    sta $2007
sd_moff13_next:
    inx
    cpx #${OPT_LINE_LEN:02X}
    bne sd_moff13_loop
    jmp sd_music_done
sd_music_off_base:
    ldx #$00
    ldy #$00
sd_moff_loop:
    tya
    bne sd_moff_bg
    lda sd_moff_txt,x
    beq sd_moff_setend
    sta $2007
    jmp sd_moff_next
sd_moff_setend:
    iny
sd_moff_bg:
    lda ${SUB_SNAP_SRAM:04X},x
    sta $2007
sd_moff_next:
    inx
    cpx #${OPT_LINE_LEN:02X}
    bne sd_moff_loop
    jmp sd_music_done

sd_music_on:
    pla
    pha
    beq sd_music_on_base
sd_music_on_13:
    ldx #$00
    ldy #$00
sd_mon13_loop:
    tya
    bne sd_mon13_bg
    lda sd_mon13_txt,x
    beq sd_mon13_setend
    sta $2007
    jmp sd_mon13_next
sd_mon13_setend:
    iny
sd_mon13_bg:
    lda ${SUB_SNAP_SRAM:04X},x
    sta $2007
sd_mon13_next:
    inx
    cpx #${OPT_LINE_LEN:02X}
    bne sd_mon13_loop
    jmp sd_music_done
sd_music_on_base:
    ldx #$00
    ldy #$00
sd_mon_loop:
    tya
    bne sd_mon_bg
    lda sd_mon_txt,x
    beq sd_mon_setend
    sta $2007
    jmp sd_mon_next
sd_mon_setend:
    iny
sd_mon_bg:
    lda ${SUB_SNAP_SRAM:04X},x
    sta $2007
sd_mon_next:
    inx
    cpx #${OPT_LINE_LEN:02X}
    bne sd_mon_loop

sd_music_done:
    pla
    lda #$07
    sta ${SUBMENU_DIRTY:04X}
    jmp sd_draw_done

; ------------------------------------------------------------ INTRO (0x07)
sd_ph_intro:
    jsr sd_calc13
    pha
    bit $2002
    lda #${(OPT_PPU2 >> 8) & 0xFF:02X}
    sta $2006
    lda #${OPT_PPU2 & 0xFF:02X}
    sta $2006

    lda ${CFG_CACHE:04X}
    and #${CFG_SPLASH:02X}
    bne sd_intro_on

sd_intro_off:
    pla
    pha
    beq sd_intro_off_base
sd_intro_off_13:
    ldx #$00
    ldy #$00
sd_ioff13_loop:
    tya
    bne sd_ioff13_bg
    lda sd_ioff13_txt,x
    beq sd_ioff13_setend
    sta $2007
    jmp sd_ioff13_next
sd_ioff13_setend:
    iny
sd_ioff13_bg:
    lda ${SUB_SNAP_SRAM + 10:04X},x
    sta $2007
sd_ioff13_next:
    inx
    cpx #${OPT_LINE_LEN:02X}
    bne sd_ioff13_loop
    jmp sd_intro_done
sd_intro_off_base:
    ldx #$00
    ldy #$00
sd_ioff_loop:
    tya
    bne sd_ioff_bg
    lda sd_ioff_txt,x
    beq sd_ioff_setend
    sta $2007
    jmp sd_ioff_next
sd_ioff_setend:
    iny
sd_ioff_bg:
    lda ${SUB_SNAP_SRAM + 10:04X},x
    sta $2007
sd_ioff_next:
    inx
    cpx #${OPT_LINE_LEN:02X}
    bne sd_ioff_loop
    jmp sd_intro_done

sd_intro_on:
    pla
    pha
    beq sd_intro_on_base
sd_intro_on_13:
    ldx #$00
    ldy #$00
sd_ion13_loop:
    tya
    bne sd_ion13_bg
    lda sd_ion13_txt,x
    beq sd_ion13_setend
    sta $2007
    jmp sd_ion13_next
sd_ion13_setend:
    iny
sd_ion13_bg:
    lda ${SUB_SNAP_SRAM + 10:04X},x
    sta $2007
sd_ion13_next:
    inx
    cpx #${OPT_LINE_LEN:02X}
    bne sd_ion13_loop
    jmp sd_intro_done
sd_intro_on_base:
    ldx #$00
    ldy #$00
sd_ion_loop:
    tya
    bne sd_ion_bg
    lda sd_ion_txt,x
    beq sd_ion_setend
    sta $2007
    jmp sd_ion_next
sd_ion_setend:
    iny
sd_ion_bg:
    lda ${SUB_SNAP_SRAM + 10:04X},x
    sta $2007
sd_ion_next:
    inx
    cpx #${OPT_LINE_LEN:02X}
    bne sd_ion_loop

sd_intro_done:
    pla
    lda #$06
    sta ${SUBMENU_DIRTY:04X}
    jmp sd_draw_done

; ------------------------------------------------------------ FIRST + PAGE (0x06)
sd_ph_fp:
    bit $2002
    lda #${(FP_PPU1 >> 8) & 0xFF:02X}
    sta $2006
    lda #${FP_PPU1 & 0xFF:02X}
    sta $2006
    ldx #$00
sd_first_loop:
    lda sd_first,x
    sta $2007
    inx
    cpx #${FP_WIDTH:02X}
    bne sd_first_loop

    bit $2002
    lda #${(FP_PPU2 >> 8) & 0xFF:02X}
    sta $2006
    lda #${FP_PPU2 & 0xFF:02X}
    sta $2006
    ldx #$00
    ldy #$00
sd_page_loop:
    tya
    bne sd_page_bg
    lda sd_page_txt,x
    beq sd_page_setend
    sta $2007
    jmp sd_page_next
sd_page_setend:
    iny
sd_page_bg:
    lda ${SUB_SNAP_FP2:04X},x
    sta $2007
sd_page_next:
    inx
    cpx #${FP_WIDTH:02X}
    bne sd_page_loop

    lda #$05
    sta ${SUBMENU_DIRTY:04X}
    jmp sd_draw_done

; ------------------------------------------------------------ EXIT (0x05)
sd_ph_exit:
    jsr sd_calc13
    pha
    bit $2002
    lda #${(EXIT_PPU >> 8) & 0xFF:02X}
    sta $2006
    lda #${EXIT_PPU & 0xFF:02X}
    sta $2006
    pla
    beq sd_exit_base

sd_exit_13:
    ldx #$00
sd_exit13_loop:
    lda sd_exit13,x
    sta $2007
    inx
    cpx #${EXIT_LEN:02X}
    bne sd_exit13_loop
    jmp sd_exit_fin

sd_exit_base:
    ldx #$00
sd_exit_loop:
    lda sd_exit,x
    sta $2007
    inx
    cpx #${EXIT_LEN:02X}
    bne sd_exit_loop

sd_exit_fin:
    lda #$00
    sta ${SUBMENU_DIRTY:04X}
    jmp sd_draw_done

; ------------------------------------------------------------ helpers / tail
sd_calc13:
    lda ${SCREEN_CACHE:04X}
    cmp #$0D
    beq sd_c13_yes
    lda #$00
    rts
sd_c13_yes:
    lda #$01
    rts

sd_draw_done:
    lda ${CFG5007:04X}
    sta $5007
    lda $2002
    lda #$00
    sta $2005
    sta $2005
    rts

sd_mon_txt:
    .byte {asm_byte_list(LINE_MUSIC_ON_T)}
sd_moff_txt:
    .byte {asm_byte_list(LINE_MUSIC_OFF_T)}
sd_ion_txt:
    .byte {asm_byte_list(LINE_INTRO_ON_T)}
sd_ioff_txt:
    .byte {asm_byte_list(LINE_INTRO_OFF_T)}

sd_mon13_txt:
    .byte {asm_byte_list(LINE_MUSIC_ON_13_T)}
sd_moff13_txt:
    .byte {asm_byte_list(LINE_MUSIC_OFF_13_T)}
sd_ion13_txt:
    .byte {asm_byte_list(LINE_INTRO_ON_13_T)}
sd_ioff13_txt:
    .byte {asm_byte_list(LINE_INTRO_OFF_13_T)}

sd_first:
    .byte {asm_byte_list(FIRST_BYTES)}
sd_page_txt:
    .byte {asm_byte_list(LINE_PAGE_T)}

sd_exit:
    .byte {asm_byte_list(EXIT_BYTES)}
sd_exit13:
    .byte {asm_byte_list(EXIT_BYTES_13)}
"""
    return assemble_6502(src, origin_cpu)

def build_service_music_start(origin_cpu: int) -> bytes:
    src = f"""
svc_music_start:
    php
    sei
    lda #$00
    sta ${SONG_ID_ZP:04X}
    jsr ${SND_PLAY_MUSIC:04X}
    plp
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_music_stop(origin_cpu: int) -> bytes:
    src = f"""
svc_music_stop:
    php
    sei
    jsr ${SND_STOP_MUSIC:04X}
    jsr ${SND_STOP_SFX:04X}
    lda #$00
    sta ${SHADOW_4015:04X}
    sta ${APU_4015:04X}
    sta ${ACTIVE_MASK_ZP:04X}
    plp
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_fixed_splash_hook(origin_cpu: int, cfg_init_stub_cpu: int) -> bytes:
    src = f"""
fixed_splash_hook:
    jsr ${cfg_init_stub_cpu:04X}
    lda ${CFG_CACHE:04X}
    and #${CFG_SPLASH:02X}
    beq fsh_skip
    jsr ${ORIG_SPLASH:04X}
fsh_skip:
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_text_overlay_nmi(origin_cpu: int) -> bytes:
    if TEXT_OVL_INIT is None or TO_TMP0 is None:
        return b""

    src = f"""
svc_text_overlay_nmi:
    lda ${MENU_ACTIVE_FLAG:04X}
    beq to_exit

    lda ${SUBMENU_ACTIVE:04X}
    bne to_mode_exit

    lda ${OPTIONS_PROMPT_SHOWN:04X}
    beq to_exit

    lda #${ATTR_PROMPT_LO:02X}
    jsr to_update_pal_from_attr_lo
    jmp to_exit

to_mode_exit:
    lda #${ATTR_EXIT_LO:02X}
    jsr to_update_pal_from_attr_lo

to_exit:
    rts

to_restore_menu_ppu:
    lda $2002
    lda #$88
    sta $2000
    lda #$00
    sta $2005
    sta $2005
    rts

to_update_pal_from_attr_lo:
    sta ${TO_TMP0:04X}

    bit $2002
    lda #$23
    sta $2006
    lda ${TO_TMP0:04X}
    sta $2006
    lda $2007
    lda $2007

    and #$C0
    lsr a
    lsr a
    lsr a
    lsr a
    lsr a
    lsr a
    sta ${TO_TMP1:04X}

    lda ${SCREEN_CACHE:04X}
    asl a
    sta ${TO_TMP2:04X}
    lda ${SCREEN_CACHE:04X}
    asl a
    asl a
    asl a
    asl a
    clc
    adc ${TO_TMP2:04X}
    sta ${TO_TMP2:04X}

    lda ${TO_TMP2:04X}
    clc
    adc #$02
    sta ${TO_TMP3:04X}

    lda ${TO_TMP1:04X}
    asl a
    asl a
    clc
    adc ${TO_TMP3:04X}
    tay

    bit $2002
    lda #$3F
    sta $2006
    lda #$1C
    sta $2006

    lda #$0F
    sta $2007

    iny
    lda ${DESC_BASE:04X},y
    sta $2007
    iny
    lda ${DESC_BASE:04X},y
    sta $2007
    iny
    lda ${DESC_BASE:04X},y
    sta $2007

    jsr to_restore_menu_ppu
    rts
"""
    return assemble_6502(src, origin_cpu)