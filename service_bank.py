# service_bank.py
# Версия: 5.7
#
# V5.7 (фикс бага #2 — поломанные тайлы на warning/incompat экранах):
#   - build_show_saving_warning, build_show_incompatible_console:
#     заменён параметр multi_alt_swap_stub_cpu (вызов через fixed-bank stub)
#     на multi_alt_swap_cpu (прямой JSR на svc_multi_alt_swap внутри
#     service bank, без переключения банков).
#   - Это позволяет передать актуальный адрес из builder.py: ранее stub
#     создавался в fixed bank ПОСЛЕ build_show_*, и параметр всегда был None.
#   - Внутри service bank переключение $5005 не нужно (мы уже там).
#
# V5.6:
#   - build_show_saving_warning: добавлен параметр alt_restore_cpu.
#     Если передан — перед показом вызывается svc_prompt_alt_restore,
#     чтобы гарантированно снять ALT-паттерны до загрузки screen09.
#   - build_show_incompatible_console: аналогично добавлен alt_restore_cpu.

from config import *
from utils import *
from asm6502 import *

def build_service_enable_prg_ram(origin_cpu):
    svc5005 = (SERVICE_BANK_INDEX << 2) & 0xFF
    src = f"""
service_enable_prg_ram:
    lda #${svc5005:02X}
    sta $5005
    lda ${CFG5007:04X}
    ora #$01
    sta ${CFG5007:04X}
    sta $5007
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_disable_prg_ram(origin_cpu):
    svc5005 = (SERVICE_BANK_INDEX << 2) & 0xFF
    src = f"""
service_disable_prg_ram:
    lda #${svc5005:02X}
    sta $5005
    lda ${CFG5007:04X}
    and #$FE
    sta ${CFG5007:04X}
    sta $5007
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_store_last_save(origin_cpu, enable_cpu, disable_cpu):
    src = f"""
service_store_last_save:
    jsr ${enable_cpu:04X}
    lda ${PENDING_SAVE_ID:04X}
    sta ${SRAM_LAST_SAVE:04X}
    jsr ${disable_cpu:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_console_detect(origin_cpu):
    if CONSOLE_DETECT_MODE == "reset":
        return _build_console_detect_reset(origin_cpu)
    else:
        return _build_console_detect_launch(origin_cpu)

def _build_console_detect_reset(origin_cpu):
    """
    V5.24: CPU-loop детектор консоли с расширенным окном измерения.

    Что изменилось против V5.22.x:
      * Окно измерения = CD_FRAMES кадров (по умолчанию 8 вместо 4).
        Абсолютный разрыв между NTSC/PAL/Dendy растёт ->
        классификация устойчивее к джиттеру и дрейфу кварца.
      * Пороги и sanity-границы вычисляются из точной модели тактов
        (cpu_cycles_per_frame / 12 / 256 * CD_FRAMES), а не зашиты вручную.
      * Пороги центрированы по серединам между номиналами. Раньше
        режим "safe" задирал порог так, что Dendy попадал в PAL.
      * Медианный фильтр (3 замера) берётся из config.DETECT_MEDIAN.

    ТАКТЫ CPU НА КАДР (rendering off): NTSC 29780.5 | PAL 33247 | Dendy 35464
    НОМИНАЛЬНЫЙ Y (CD_FRAMES=8): NTSC ~78 | PAL ~87 | Dendy ~92

    CONSOLE_TYPE_RAM: bit0=NTSC bit1=PAL bit2=Dendy bit3=new Dendy, $00=UNDETECTED
    """
    _CPF = {"ntsc": 29780.5, "pal": 33247.0, "dendy": 35464.0}
    _y = {k: v * CD_FRAMES / 12.0 / 256.0 for k, v in _CPF.items()}
    ntsc_pal_threshold = int(round((_y["ntsc"] + _y["pal"]) / 2.0))
    _pal_dendy_mid = int(round((_y["pal"] + _y["dendy"]) / 2.0))
    _pal_dendy_safe = min(_pal_dendy_mid + 2, int(_y["dendy"]) - 1)
    pal_dendy_threshold = _pal_dendy_safe if DETECT_THRESHOLD == "safe" else _pal_dendy_mid
    y_lo = int(_y["ntsc"]) - 8
    y_hi = int(_y["dendy"]) + 8

    sei_blackout = f"""
console_detect:
    sei
    lda #$00
    sta $2000
    sta $2001
    bit $2002
cd_pal_wait:
    bit $2002
    bpl cd_pal_wait
    lda #$3F
    sta $2006
    lda #$00
    sta $2006
    lda #$0F
    sta $2007
    sta $2007
    sta $2007
    sta $2007
    lda #$00
    sta $2006
    sta $2006
"""

    if DETECT_MEDIAN:
        measure = f"""
    jsr do_measure
    sty ${CD_TMP0:04X}
    jsr do_measure
    sty ${CD_TMP1:04X}
    jsr do_measure
    sty ${CD_TMP2:04X}

    lda ${CD_TMP0:04X}
    cmp ${CD_TMP1:04X}
    bcc cd_med_s12
    lda ${CD_TMP0:04X}
    ldx ${CD_TMP1:04X}
    sta ${CD_TMP1:04X}
    stx ${CD_TMP0:04X}
cd_med_s12:
    lda ${CD_TMP1:04X}
    cmp ${CD_TMP2:04X}
    bcc cd_med_s23
    lda ${CD_TMP1:04X}
    ldx ${CD_TMP2:04X}
    sta ${CD_TMP2:04X}
    stx ${CD_TMP1:04X}
cd_med_s23:
    lda ${CD_TMP0:04X}
    cmp ${CD_TMP1:04X}
    bcc cd_med_done
    lda ${CD_TMP0:04X}
    ldx ${CD_TMP1:04X}
    sta ${CD_TMP1:04X}
    stx ${CD_TMP0:04X}
cd_med_done:
    lda ${CD_TMP1:04X}
    sta ${CD_TMP0:04X}
"""
    else:
        measure = f"""
    jsr do_measure
    sty ${CD_TMP0:04X}
"""

    restore = """
    lda #$88
    sta $2000
    cli
"""

    classification = f"""
    lda ${CD_TMP0:04X}
    cmp #${y_lo:02X}
    bcc cd_undetected
    cmp #${y_hi + 1:02X}
    bcs cd_undetected

    cmp #${ntsc_pal_threshold:02X}
    bcs cd_slow
    lda #$01
    jmp cd_check_new_dendy

cd_slow:
    cmp #${pal_dendy_threshold:02X}
    bcs cd_dendy
    lda #$02
    jmp cd_store

cd_dendy:
    lda #$04

cd_check_new_dendy:
    pha
    lda $5000
    and #$01
    beq cd_no_new_d
    pla
    ora #$08
    jmp cd_store
cd_no_new_d:
    pla
cd_store:
    sta ${CONSOLE_TYPE_RAM:04X}
    rts

cd_undetected:
    lda #$00
    sta ${CONSOLE_TYPE_RAM:04X}
    rts
"""

    measure_subroutine = f"""
do_measure:
    bit $2002
cd_sync_off:
    bit $2002
    bmi cd_sync_off
cd_sync_on:
    bit $2002
    bpl cd_sync_on

    ldx #$00
    ldy #$00
    lda #${CD_FRAMES:02X}
    sta $5C

cd_count_loop:
    inx
    bne cd_count_check
    iny
cd_count_check:
    bit $2002
    bpl cd_count_loop
    dec $5C
    beq cd_count_done

cd_skip_vblank:
    inx
    bne cd_skip_check
    iny
cd_skip_check:
    bit $2002
    bmi cd_skip_vblank
    jmp cd_count_loop

cd_count_done:
    rts
"""

    body = sei_blackout + measure + restore
    return assemble_6502(body + classification + measure_subroutine, origin_cpu)

def _build_console_detect_launch(origin_cpu):
    src = f"""
console_detect:
    lda #$00
    sta $2000
    sta $2001

cd_wait_active_end:
    lda $2002
    bmi cd_wait_active_end

cd_wait_vb_start:
    lda $2002
    bpl cd_wait_vb_start

    ldx #$00
    ldy #$00
cd_m0_vb_loop:
    inx
    bne cd_m0_vb_check
    iny
cd_m0_vb_check:
    lda $2002
    bmi cd_m0_vb_loop
    stx ${CD_XTMP:04X}

    ldx #$00
    ldy #$00
cd_m0_act_loop:
    inx
    bne cd_m0_act_check
    iny
cd_m0_act_check:
    lda $2002
    bpl cd_m0_act_loop
    sty ${CD_TMP0:04X}

    ldx #$00
    ldy #$00
cd_m1_vb_loop:
    inx
    bne cd_m1_vb_check
    iny
cd_m1_vb_check:
    lda $2002
    bmi cd_m1_vb_loop
    ldx #$00
    ldy #$00
cd_m1_act_loop:
    inx
    bne cd_m1_act_check
    iny
cd_m1_act_check:
    lda $2002
    bpl cd_m1_act_loop
    sty ${CD_TMP1:04X}

    ldx #$00
    ldy #$00
cd_m2_vb_loop:
    inx
    bne cd_m2_vb_check
    iny
cd_m2_vb_check:
    lda $2002
    bmi cd_m2_vb_loop
    ldx #$00
    ldy #$00
cd_m2_act_loop:
    inx
    bne cd_m2_act_check
    iny
cd_m2_act_check:
    lda $2002
    bpl cd_m2_act_loop
    sty ${CD_TMP2:04X}

    lda ${CD_TMP0:04X}
    cmp ${CD_TMP1:04X}
    bne cd_cmp_02
    jmp cd_classify

cd_cmp_02:
    lda ${CD_TMP0:04X}
    cmp ${CD_TMP2:04X}
    bne cd_cmp_12
    jmp cd_classify

cd_cmp_12:
    lda ${CD_TMP1:04X}
    cmp ${CD_TMP2:04X}
    bne cd_all_diff
    lda ${CD_TMP1:04X}
    jmp cd_classify

cd_all_diff:
    lda ${CD_TMP0:04X}
    cmp ${CD_TMP1:04X}
    bcs cd_cmp_max_02
    lda ${CD_TMP1:04X}
cd_cmp_max_02:
    cmp ${CD_TMP2:04X}
    bcs cd_classify
    lda ${CD_TMP2:04X}

cd_classify:
    cmp #$0A
    bcc cd_ntsc
    cmp #$0C
    bcc cd_pal_or_dendy
    lda #$04
    jmp cd_check_new_dendy

cd_ntsc:
    lda #$01
    jmp cd_store

cd_pal_or_dendy:
    lda ${CD_TMP0:04X}
    cmp ${CD_TMP1:04X}
    bcs pd_cmp2
    lda ${CD_TMP1:04X}
pd_cmp2:
    cmp ${CD_TMP2:04X}
    bcs pd_got_max
    lda ${CD_TMP2:04X}
pd_got_max:
    cmp #$0B
    bcs pd_dendy
    lda #$02
    jmp cd_store
pd_dendy:
    lda #$04
    jmp cd_check_new_dendy

cd_check_new_dendy:
    pha
    lda ${CD_XTMP:04X}
    cmp #$60
    pla
    bcc cd_store
    ora #$08

cd_store:
    sta ${CONSOLE_TYPE_RAM:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_clear_menu_work_ram(origin_cpu):
    src = """
clear_menu_work_ram:
    lda #$00
    ldx #$00
cmw_loop:
    sta $0300,x
    sta $0400,x
    sta $0500,x
    inx
    bne cmw_loop
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_pre_launch_clear(origin_cpu):
    """
    V5.24: очистка PPU и APU состояния перед запуском игры.
    Аналог фрагмента ClusterM `compatible_console:` (preloader.asm).

    Ожидается что вызывается после ppu_off (PPU rendering выключен).
    На входе $5007 — конфигурация меню. Внутри функция временно включает
    chr_wr (на самом деле здесь это не нужно, NT в CIRAM а не CHR-RAM,
    но мы оставляем cfg как есть).

    Делает:
      1. Заливает OAM ($0200..$02FF) значением $FF (Y=255 — спрайты вне экрана)
      2. Очищает все 4 NT-страницы CIRAM ($2000/$2400/$2800/$2C00) нулями.
         32×30 видимых тайлов + атрибуты = 1024 байт каждая.
         На любом mirroring (V/H/1Sa/1Sb) этот цикл накрывает всю CIRAM.
      3. Сбрасывает APU regs $4000..$4013 в 0 (length counters/sweep/envelope/DMC).
         $4015 уже был обнулён вызывающим (ext_launch_game).
         $4017 уже был установлен в $40 (mode 1, IRQ off) вызывающим.

    После возврата PPU adress reset'ится в $0000, scroll = 0.
    Регистры в неопределённом состоянии (вызывающий уже на пути к JMP $0400).
    """
    src = """
pre_launch_clear:
    ; ── 1. OAM clear ($0200..$02FF) = $FF ──
    ; Y=255 уводит все спрайты за нижний край экрана (256 строк скрыто)
    ; до того как игра при reset запустит свой OAM DMA с собственным буфером.
    lda #$FF
    ldx #$00
plc_oam:
    sta $0200,x
    inx
    bne plc_oam

    ; ── 1b. Скопировать очищенную RAM в реальную PPU OAM через DMA ──
    ; БЕЗ ЭТОГО заливка $0200..$02FF не имеет эффекта: PPU OAM содержит
    ; то что было записано последним NMI-OAM DMA (спрайты меню — курсоры,
    ; чайки на screen 9 и т.п.). После $C1AB рендер выключен ($2001=0),
    ; но реальная PPU OAM не очищена. Игра при init может включить PPU
    ; до своего первого OAM DMA → видны спрайты меню на 1-2 кадра.
    ;
    ; DMA: 514 циклов. Безопасно при $2001=0 (нет рендеринга, нет конфликта).
    lda #$00
    sta $2003           ; OAMADDR = 0
    lda #$02
    sta $4014           ; DMA $0200..$02FF -> PPU OAM

    ; ── 2. NT clear ($2000..$2FFF) = $00 ──
    ; PPU уже off, $2002 latch неважен — но bit $2002 всё равно ставим
    ; для надёжности (сбросить write toggle на случай если кто-то его взвёл).
    bit $2002
    lda #$20
    sta $2006
    lda #$00
    sta $2006
    ; 4 страницы × 1024 байта = 4096
    ; считаем 16 × 256: внешний счётчик X, внутренний Y
    lda #$00
    ldx #$10
plc_nt_outer:
    ldy #$00
plc_nt_inner:
    sta $2007
    iny
    bne plc_nt_inner
    dex
    bne plc_nt_outer

    ; reset PPU addr к $0000 чтобы не мешать последующему CHR-copy в launcher
    lda #$00
    sta $2006
    sta $2006

    ; ── 3. APU regs $4000..$4013 = $00 ──
    ; Cluster делает то же в reset_sound. Игра при reset перепишет своими
    ; значениями, но некоторые игры читают APU status / включают каналы
    ; до полной инициализации — лучше отдать им чистое APU.
    ;
    ; $4017 = $40 (mode 1, IRQ off) — вызывающий уже выставил.
    ; $4014 (OAM DMA) трогать не нужно.
    ; Сначала глушим $4015 — иначе при затирании $4000-$400C сразу
    ; могут быть слышны щелчки от полу-обновлённых регистров.
    lda #$00
    sta $4015
    sta $4000
    sta $4001
    sta $4002
    sta $4003
    sta $4004
    sta $4005
    sta $4006
    sta $4007
    sta $4008
    sta $4009
    sta $400A
    sta $400B
    sta $400C
    sta $400D
    sta $400E
    sta $400F
    sta $4010
    sta $4011
    sta $4012
    sta $4013
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_recalc_page_row(origin_cpu):
    src = f"""
recalc_page_row:
    lda $18
    and #$0F
    sta $12
    lda $18
    lsr a
    lsr a
    lsr a
    lsr a
    sta $10
    lda $19
    and #$0F
    asl a
    asl a
    asl a
    asl a
    ora $10
    sta $10
    lda $19
    lsr a
    lsr a
    lsr a
    lsr a
    sta $11
    lda $17
    and #$0F
    asl a
    asl a
    asl a
    asl a
    ora $11
    sta $11
    lda $17
    lsr a
    lsr a
    lsr a
    lsr a
    sta $13
    jsr $C0EE
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_show_saving_warning(origin_cpu, alt_restore_cpu=None,
                               multi_alt_swap_cpu=None):
    """
    alt_restore_cpu: если передан — перед показом вызывается svc_prompt_alt_restore
                     (старая система ALT-restore).
    multi_alt_swap_cpu: V5.7 — прямой адрес svc_multi_alt_swap в service bank.
                     Вызывается с A=9 для отката всех кастомов CHR на state
                     screen 9 (фон с пальмами/огнём + спрайты). Не нужен
                     stub-обёртка с PHP/SEI/bank-switch — мы уже в service bank.
                     Заменяет alt_restore_cpu при включенной multi_alt системе.

                     Примечание (V5.8): обратный swap к screen 0 ПОСЛЕ показа
                     не делается. SAVING — это экран начала меню, в menu_init_wrapper
                     дальше всё равно идёт `c26c_wrapper` который вычислит
                     текущий screen и сделает корректный swap. Лишний обратный
                     swap ввёл бы черное мигание на доли секунды.
    """
    line1 = bytes([encode_orig_menu_char(c) for c in "SAVING..."]) + b"\x00"
    line2 = bytes([encode_orig_menu_char(c) for c in "DONT TURN OFF"]) + b"\x00"

    # V5.7: предпочитаем multi_alt swap если он есть (откатит все кастомы
    # текущего экрана и загрузит правильный CHR для screen 9 фона).
    pre_block = ""
    if multi_alt_swap_cpu is not None:
        pre_block = f"""
    ; V5.7: прямой JSR на svc_multi_alt_swap (мы уже в service bank)
    ; A=9 → swap CHR к state screen 9
    lda #$09
    jsr ${multi_alt_swap_cpu:04X}
"""
    elif alt_restore_cpu is not None:
        pre_block = f"    jsr ${alt_restore_cpu:04X}\n"

    src = f"""
show_saving_warning:
{pre_block}
    php
    sei
    jsr $FF36
    jsr $FF93
    plp
    lda #$00
    sta $4015
    jsr $C1AB

    ; ── V5.24: чёрный backdrop сразу после render off ──
    ; Между $C1AB и $C219 виден backdrop предыдущего экрана меню (1 кадр).
    ; Заливаем только BG-палитру $3F00..$3F0F цветом $0F (NES-чёрный).
    ; Спрайтовую $3F10..$3F1F не трогаем — load_screen 9 ($C219) грузит
    ; только 16 байт BG-палитры, и затирание спрайтовой убрало бы цвет
    ; огней которые показываются как часть warning-экрана.
    bit $2002
    lda #$3F
    sta $2006
    lda #$00
    sta $2006
    ldx #$10
    lda #$0F
ssw_pal_black:
    sta $2007
    dex
    bne ssw_pal_black
    lda #$00
    sta $2006
    sta $2006

    lda #$09
    ldx #$00
    jsr $C219
    lda #$F8
    ldx #$00
ssw_hide_oam:
    sta $0200,x
    inx
    bne ssw_hide_oam
    lda #$BE
    sta $0200
    lda #$19
    sta $0201
    lda #$00
    sta $0202
    lda #$89
    sta $0203
    lda #$BE
    sta $0204
    lda #$1A
    sta $0205
    lda #$00
    sta $0206
    lda #$91
    sta $0207
    lda #$C6
    sta $0208
    lda #$1B
    sta $0209
    lda #$00
    sta $020A
    lda #$89
    sta $020B
    lda #$C6
    sta $020C
    lda #$1C
    sta $020D
    lda #$00
    sta $020E
    lda #$91
    sta $020F
    bit $2002
    lda #$21
    sta $2006
    lda #$AA
    sta $2006
    ldy #$00
ssw_line1_loop:
    lda ssw_line1,y
    beq ssw_line1_done
    sta $2007
    iny
    bne ssw_line1_loop
ssw_line1_done:
    bit $2002
    lda #$21
    sta $2006
    lda #$C9
    sta $2006
    ldy #$00
ssw_line2_loop:
    lda ssw_line2,y
    beq ssw_line2_done
    sta $2007
    iny
    bne ssw_line2_loop
ssw_line2_done:
    ; ── V5.24: OAM DMA с подготовленным буфером (chrome character) ──
    lda #$00
    sta $2003
    lda #$02
    sta $4014

    ; ── V5.24: дождаться vblank перед включением render ──
    ; Без sync $2001=$1E может попасть в середину кадра — рендер начнёт
    ; с произвольной scanline до того как scroll выставлен в 0.
    ; Получается мерцание/сдвиг на 1 кадр.
    bit $2002
ssw_wait_vbl:
    lda $2002
    bpl ssw_wait_vbl

    lda #$08
    sta $2000
    lda #$00
    sta $2005
    sta $2005
    lda #$1E
    sta $2001
    rts
ssw_line1:
    .byte {asm_byte_list(line1)}
ssw_line2:
    .byte {asm_byte_list(line2)}
"""
    return assemble_6502(src, origin_cpu)

def build_show_incompatible_console(origin_cpu, alt_restore_cpu=None,
                                     multi_alt_swap_cpu=None):
    """
    alt_restore_cpu: если передан — перед показом вызывается svc_prompt_alt_restore.
    multi_alt_swap_cpu: V5.7 — прямой адрес svc_multi_alt_swap в service bank.
                     Вызывается с A=9, чтобы откатить кастомы и загрузить
                     screen 9 CHR. Имеет приоритет над alt_restore_cpu.

                     V5.8: после нажатия кнопки выполняется ОБРАТНЫЙ swap к
                     screen 0, чтобы CHR вернулся в initial state перед
                     menu_init_wrapper (иначе на следующих экранах меню
                     остаются артефакты screen 9 фона).
    """
    line1 = bytes([encode_orig_menu_char(c) for c in "INCOMPATIBLE"]) + b"\x00"
    line2 = bytes([encode_orig_menu_char(c) for c in "CONSOLE"])      + b"\x00"
    line3 = bytes([encode_orig_menu_char(c) for c in "PRESS BUTTON"]) + b"\x00"

    pre_block = ""
    post_block = ""
    if multi_alt_swap_cpu is not None:
        pre_block = f"""
    ; V5.7: прямой JSR на svc_multi_alt_swap для отката кастомов
    lda #$09
    jsr ${multi_alt_swap_cpu:04X}
"""
        post_block = f"""
    ; V5.8: откат CHR в initial state (screen 0) ПЕРЕД возвратом в menu_init.
    lda #$00
    jsr ${multi_alt_swap_cpu:04X}
"""
    elif alt_restore_cpu is not None:
        pre_block = f"    jsr ${alt_restore_cpu:04X}\n"

    src = f"""
show_incompatible_console:
{pre_block}
    php
    sei
    jsr $FF36
    jsr $FF93
    plp
    lda #$00
    sta $4015
    jsr $C1AB

    ; ── V5.24: чёрный backdrop сразу после render off ──
    ; Без этого между $C1AB и $C219 виден backdrop ПРЕДЫДУЩЕГО экрана меню
    ; (1 кадр окрашенного фона). Заливаем ТОЛЬКО BG-палитру $3F00..$3F0F
    ; цветом $0F (NES-чёрный). Спрайтовую $3F10..$3F1F не трогаем —
    ; load_screen 9 ($C219) грузит только 16 байт BG-палитры,
    ; и если бы мы затёрли спрайтовую — огни/курсоры стали бы чёрными.
    bit $2002
    lda #$3F
    sta $2006
    lda #$00
    sta $2006
    ldx #$10
    lda #$0F
sic_pal_black:
    sta $2007
    dex
    bne sic_pal_black
    lda #$00
    sta $2006
    sta $2006

    lda #$09
    ldx #$00
    jsr $C219
    lda #$F8
    ldx #$00
sic_hide_oam:
    sta $0200,x
    inx
    bne sic_hide_oam
    bit $2002
    lda #$21
    sta $2006
    lda #$4A
    sta $2006
    ldy #$00
sic_l1_loop:
    lda sic_line1,y
    beq sic_l1_done
    sta $2007
    iny
    bne sic_l1_loop
sic_l1_done:
    bit $2002
    lda #$21
    sta $2006
    lda #$8C
    sta $2006
    ldy #$00
sic_l2_loop:
    lda sic_line2,y
    beq sic_l2_done
    sta $2007
    iny
    bne sic_l2_loop
sic_l2_done:
    bit $2002
    lda #$21
    sta $2006
    lda #$EA
    sta $2006
    ldy #$00
sic_l3_loop:
    lda sic_line3,y
    beq sic_l3_done
    sta $2007
    iny
    bne sic_l3_loop
sic_l3_done:
    ; ── V5.24: dummy OAM DMA с очищенной RAM ──
    ; OAM в PPU всё ещё содержит спрайты предыдущего экрана меню.
    ; Загоним $F8 (Y=248, скрыто) до первого включения render.
    lda #$00
    sta $2003
    lda #$02
    sta $4014

    ; ── V5.24: дождаться vblank перед включением render ──
    ; Без этого STA $2001=$1E может попасть в середину кадра — рендер
    ; начнёт с произвольной scanline до того как scroll выставлен в 0.
    ; Получается мерцание/сдвиг на 1 кадр.
    bit $2002
sic_wait_vbl:
    lda $2002
    bpl sic_wait_vbl

    lda #$08
    sta $2000
    lda #$00
    sta $2005
    sta $2005
    lda #$1E
    sta $2001
    lda #$00
    sta ${SAVE_TMP1:04X}
sic_wait_release:
    jsr sic_update_fire
    jsr $C41B
    lda #$00
    sta $2003
    lda #$02
    sta $4014
    bit $2002
    lda #$00
    sta $2005
    sta $2005
    lda #$00
    jsr $C428
    lda $22
    bne sic_wait_release
sic_wait_press:
    jsr sic_update_fire
    jsr $C41B
    lda #$00
    sta $2003
    lda #$02
    sta $4014
    bit $2002
    lda #$00
    sta $2005
    sta $2005
    lda #$00
    jsr $C428
    lda $22
    beq sic_wait_press
    jsr $C1AB
{post_block}
    rts
sic_update_fire:
    inc ${SAVE_TMP1:04X}
    lda ${SAVE_TMP1:04X}
    and #$10
    beq sic_fire_frame0
sic_fire_frame1:
    lda #$BE
    sta $0200
    lda #$1D
    sta $0201
    lda #$00
    sta $0202
    lda #$89
    sta $0203
    lda #$BE
    sta $0204
    lda #$1E
    sta $0205
    lda #$00
    sta $0206
    lda #$91
    sta $0207
    lda #$C6
    sta $0208
    lda #$1F
    sta $0209
    lda #$00
    sta $020A
    lda #$89
    sta $020B
    lda #$C6
    sta $020C
    lda #$20
    sta $020D
    lda #$00
    sta $020E
    lda #$91
    sta $020F
    rts
sic_fire_frame0:
    lda #$BE
    sta $0200
    lda #$19
    sta $0201
    lda #$00
    sta $0202
    lda #$89
    sta $0203
    lda #$BE
    sta $0204
    lda #$1A
    sta $0205
    lda #$00
    sta $0206
    lda #$91
    sta $0207
    lda #$C6
    sta $0208
    lda #$1B
    sta $0209
    lda #$00
    sta $020A
    lda #$89
    sta $020B
    lda #$C6
    sta $020C
    lda #$1C
    sta $020D
    lda #$00
    sta $020E
    lda #$91
    sta $020F
    rts
sic_line1:
    .byte {asm_byte_list(line1)}
sic_line2:
    .byte {asm_byte_list(line2)}
sic_line3:
    .byte {asm_byte_list(line3)}
"""
    return assemble_6502(src, origin_cpu)

def build_service_load_state(origin_cpu, service_enable_cpu, service_disable_cpu):
    src = f"""
service_load_state:
    jsr ${service_enable_cpu:04X}
    lda #$43
    cmp ${SRAM_SIG0 + 0:04X}
    bne sls_bad
    lda #$4F
    cmp ${SRAM_SIG0 + 1:04X}
    bne sls_bad
    lda #$4F
    cmp ${SRAM_SIG0 + 2:04X}
    bne sls_bad
    lda #$4C
    cmp ${SRAM_SIG0 + 3:04X}
    bne sls_bad
    lda #$53
    cmp ${SRAM_SIG0 + 4:04X}
    bne sls_bad
    lda #$41
    cmp ${SRAM_SIG0 + 5:04X}
    bne sls_bad
    lda #$56
    cmp ${SRAM_SIG0 + 6:04X}
    bne sls_bad
    lda #$45
    cmp ${SRAM_SIG0 + 7:04X}
    bne sls_bad
    lda ${SRAM_MENU_LO:04X}
    sta $18
    lda ${SRAM_MENU_MID:04X}
    sta $19
    lda ${SRAM_MENU_HI:04X}
    sta $17
    jsr ${service_disable_cpu:04X}
    lda $17
    beq sls_chk_mid
    jmp sls_clear
sls_chk_mid:
    lda $19
    cmp #$27
    bcc sls_ok
    bne sls_clear
    lda $18
    cmp #$0F
    bcs sls_clear
sls_ok:
    rts
sls_bad:
    jsr ${service_disable_cpu:04X}
sls_clear:
    lda #$00
    sta $17
    sta $18
    sta $19
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_save_state(origin_cpu, service_enable_cpu, service_disable_cpu):
    src = f"""
service_save_state:
    jsr ${service_enable_cpu:04X}
    lda #$43
    sta ${SRAM_SIG0 + 0:04X}
    lda #$4F
    sta ${SRAM_SIG0 + 1:04X}
    lda #$4F
    sta ${SRAM_SIG0 + 2:04X}
    lda #$4C
    sta ${SRAM_SIG0 + 3:04X}
    lda #$53
    sta ${SRAM_SIG0 + 4:04X}
    lda #$41
    sta ${SRAM_SIG0 + 5:04X}
    lda #$56
    sta ${SRAM_SIG0 + 6:04X}
    lda #$45
    sta ${SRAM_SIG0 + 7:04X}
    lda $18
    sta ${SRAM_MENU_LO:04X}
    lda $19
    sta ${SRAM_MENU_MID:04X}
    lda $17
    sta ${SRAM_MENU_HI:04X}
    lda #$00
    sta ${SRAM_RESERVED:04X}
    lda ${PENDING_SAVE_ID:04X}
    sta ${SRAM_LAST_SAVE:04X}
    jsr ${service_disable_cpu:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_run_ram_helper(origin_cpu, helper_src_cpu, helper_size):
    src = f"""
run_ram_helper:
    sta ${SAVE_MODE:04X}
    lda #${helper_src_cpu & 0xFF:02X}
    sta $00
    lda #${(helper_src_cpu >> 8) & 0xFF:02X}
    sta $01
    lda #$00
    sta $02
    lda #$04
    sta $03
    lda #${helper_size & 0xFF:02X}
    sta $04
    lda #${(helper_size >> 8) & 0xFF:02X}
    sta $05
    ldy #$00
rrh_copy:
    lda ($00),y
    sta ($02),y
    iny
    bne rrh_after_page
    inc $01
    inc $03
rrh_after_page:
    lda $04
    sec
    sbc #$01
    sta $04
    lda $05
    sbc #$00
    sta $05
    lda $04
    ora $05
    bne rrh_copy
    jsr ${RAM_HELPER_ADDR:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_load_save(origin_cpu, run_ram_helper_cpu, store_last_cpu):
    src = f"""
service_load_save:
    php
    sei
    jsr $FF36
    jsr $FF93
    plp
    lda #$00
    sta $4015
    lda ${PENDING_SAVE_ID:04X}
    beq sls_done
    lda #$01
    jsr ${run_ram_helper_cpu:04X}
    jsr ${store_last_cpu:04X}
sls_done:
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_flush_pending(origin_cpu, enable_cpu, disable_cpu,
                                run_ram_helper_cpu, store_last_cpu):
    src = f"""
service_flush_pending:
    jsr ${enable_cpu:04X}
    lda #$43
    cmp ${SRAM_SIG0 + 0:04X}
    bne sfp_no_sig
    lda #$4F
    cmp ${SRAM_SIG0 + 1:04X}
    bne sfp_no_sig
    lda #$4F
    cmp ${SRAM_SIG0 + 2:04X}
    bne sfp_no_sig
    lda #$4C
    cmp ${SRAM_SIG0 + 3:04X}
    bne sfp_no_sig
    lda #$53
    cmp ${SRAM_SIG0 + 4:04X}
    bne sfp_no_sig
    lda #$41
    cmp ${SRAM_SIG0 + 5:04X}
    bne sfp_no_sig
    lda #$56
    cmp ${SRAM_SIG0 + 6:04X}
    bne sfp_no_sig
    lda #$45
    cmp ${SRAM_SIG0 + 7:04X}
    bne sfp_no_sig
    lda ${SRAM_LAST_SAVE:04X}
    sta ${PENDING_SAVE_ID:04X}
    jmp sfp_after_read
sfp_no_sig:
    lda #$00
    sta ${PENDING_SAVE_ID:04X}
sfp_after_read:
    jsr ${disable_cpu:04X}
    lda ${PENDING_SAVE_ID:04X}
    beq sfp_done
    lda #$02
    jsr ${run_ram_helper_cpu:04X}
    lda #$00
    sta ${PENDING_SAVE_ID:04X}
    jsr ${store_last_cpu:04X}
sfp_done:
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_service_fetch_dir_entry(origin_cpu, helper_src_cpu, helper_size):
    if helper_size >= 256:
        raise ValueError("dir helper too large for single-byte loop")

    # V5.7.1 (Баг 1): хелпер копируется в RAM_DIR_HELPER_ADDR ($0622) и
    # выполняется там же. Он НЕ должен залезать на MUSIC_PENDING ($0694).
    # DIR_HELPER_LIMIT теперь равен MUSIC_PENDING (см. config.py V5.24.1),
    # поэтому допустимый размер = DIR_HELPER_LIMIT - RAM_DIR_HELPER_ADDR.
    room = DIR_HELPER_LIMIT - RAM_DIR_HELPER_ADDR
    print(f"[dir helper] {helper_size} Б, лимит {room} Б "
          f"(${RAM_DIR_HELPER_ADDR:04X}..${DIR_HELPER_LIMIT - 1:04X}, "
          f"до MUSIC_PENDING ${MUSIC_PENDING:04X})")
    if helper_size > room:
        raise ValueError(
            f"dir helper {helper_size} Б перекрывает MUSIC_PENDING "
            f"(${MUSIC_PENDING:04X}): помещается только {room} Б "
            f"от ${RAM_DIR_HELPER_ADDR:04X}"
        )

    src = f"""
service_fetch_dir_entry:
    ldx #$00
sfde_copy:
    lda ${helper_src_cpu:04X},x
    sta ${RAM_DIR_HELPER_ADDR:04X},x
    inx
    cpx #${helper_size:02X}
    bne sfde_copy
    jsr ${RAM_DIR_HELPER_ADDR:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)

def build_ram_fetch_dir_helper(ram_origin):
    dir_first_bank16 = UNIQUE_DIR_START >> 14
    svc_bank16  = SERVICE_BANK_OFFSET >> 14
    svc_base16  = svc_bank16 & ~0x07
    svc_slot    = svc_bank16 & 0x07

    if NAME_MODE == 1:
        page_calc = f"""
    lda ${DIR_INDEX_HI:04X}
    lsr a
"""
        lowbits_init = f"""
    lda ${DIR_INDEX_LO:04X}
    sta $00
    lda ${DIR_INDEX_HI:04X}
    and #$01
    sta $01
"""
        shift_count = 5
        copy_count  = 0x20
    else:
        page_calc = f"""
    lda ${DIR_INDEX_HI:04X}
"""
        lowbits_init = f"""
    lda ${DIR_INDEX_LO:04X}
    sta $00
    lda #$00
    sta $01
"""
        shift_count = 6
        copy_count  = 0x40

    shifts = "\n".join(["    asl $00\n    rol $01"] * shift_count)

    src = f"""
ram_fetch_dir_entry:
{page_calc}
    clc
    adc #${dir_first_bank16 & 0xFF:02X}
    sta $06
    lda #${(dir_first_bank16 >> 8) & 0xFF:02X}
    adc #$00
    sta $07
    lda $06
    and #$F8
    sta $5001
    lda $07
    sta $5000
    lda $06
    and #$07
    asl a
    asl a
    sta $5005
{lowbits_init}
{shifts}
    lda $00
    sta $02
    lda $01
    ora #$80
    sta $03
    lda #${DIR_ENTRY_BUF & 0xFF:02X}
    sta $04
    lda #${(DIR_ENTRY_BUF >> 8) & 0xFF:02X}
    sta $05
    ldy #$00
rfd_copy:
    lda ($02),y
    sta ($04),y
    iny
    cpy #${copy_count:02X}
    bne rfd_copy
    lda #${svc_base16 & 0xFF:02X}
    sta $5001
    lda #${(svc_base16 >> 8) & 0xFF:02X}
    sta $5000
    lda #${(svc_slot << 2) & 0xFF:02X}
    sta $5005
    rts
"""
    return assemble_6502(src, ram_origin)

def build_ram_save_helper(ram_origin, restore_bank_value):
    src = f"""
ram_save_helper:
    lda ${PENDING_SAVE_ID:04X}
    bne helper_have_id
    jmp helper_restore
helper_have_id:
    lda ${SAVE_MODE:04X}
    cmp #$01
    bne helper_chk_mode2
    jmp mode_load
helper_chk_mode2:
    cmp #$02
    bne helper_bad_mode
    jmp mode_flush
helper_bad_mode:
    jmp helper_fail
mode_load:
    lda ${PENDING_SAVE_ID:04X}
    sta $11
    jsr set_save_superbank
    lda #$02
    jsr map_save_page
    jsr copy_flash_to_wram
    jmp helper_restore
mode_flush:
    lda ${PENDING_SAVE_ID:04X}
    sta ${SAVE_TMP3:04X}
    jsr calc_group_first
    lda #$00
    sta ${SAVE_TMP6:04X}
mf_load_loop:
    lda ${SAVE_TMP6:04X}
    cmp #$04
    beq mf_load_done
    lda ${SAVE_TMP6:04X}
    cmp #$02
    bne mf_page_ok
    inc ${SAVE_TMP6:04X}
    jmp mf_load_loop
mf_page_ok:
    lda ${SAVE_TMP5:04X}
    cmp ${PENDING_SAVE_ID:04X}
    bne mf_id_ok
    inc ${SAVE_TMP5:04X}
    jmp mf_load_loop
mf_id_ok:
    lda ${SAVE_TMP5:04X}
    ldx ${SAVE_TMP6:04X}
    sta ${SAVE_TMP1:04X},x
    sta $11
    jsr set_save_superbank
    lda ${SAVE_TMP6:04X}
    jsr map_save_page
    jsr copy_flash_to_wram
    inc ${SAVE_TMP5:04X}
    inc ${SAVE_TMP6:04X}
    jmp mf_load_loop
mf_load_done:
    jsr set_sector_superbank
    lda #$00
    jsr map_save_page
    jsr erase_sector
    lda #$00
    sta ${SAVE_TMP6:04X}
mf_write_loop:
    lda ${SAVE_TMP6:04X}
    cmp #$04
    beq mf_write_done
    tax
    lda ${SAVE_TMP1:04X},x
    sta $11
    jsr set_save_superbank
    lda ${SAVE_TMP6:04X}
    jsr map_save_page
    jsr copy_wram_to_flash
    inc ${SAVE_TMP6:04X}
    jmp mf_write_loop
mf_write_done:
    jmp helper_restore
calc_group_first:
    lda ${PENDING_SAVE_ID:04X}
    sec
    sbc #$01
    and #$FC
    clc
    adc #$01
    sta ${SAVE_TMP5:04X}
    rts
set_save_superbank:
    lda $11
    sec
    sbc #$01
    tax
    inx
    lda #$00
    sta $08
    sta $09
ssb_loop:
    sec
    lda $08
    sbc #$02
    sta $08
    lda $09
    sbc #$00
    sta $09
    dex
    bne ssb_loop
    rts
set_sector_superbank:
    lda ${PENDING_SAVE_ID:04X}
    sec
    sbc #$01
    ora #$03
    tax
    inx
    lda #$00
    sta $08
    sta $09
sts_loop:
    sec
    lda $08
    sbc #$02
    sta $08
    lda $09
    sbc #$00
    sta $09
    dex
    bne sts_loop
    rts
map_save_page:
    sta $07
    lda $08
    sta $5001
    lda $09
    sta $5000
    lda $07
    sta $5005
    rts
copy_flash_to_wram:
    lda ${CFG5007:04X}
    ora #$01
    sta $5007
    lda #$00
    sta $00
    lda #$80
    sta $01
    lda #$00
    sta $02
    lda #$60
    sta $03
    ldy #$00
    ldx #$20
cft_loop:
    lda ($00),y
    sta ($02),y
    iny
    bne cft_loop
    inc $01
    inc $03
    dex
    bne cft_loop
    rts
erase_sector:
    lda ${CFG5007:04X}
    ora #$05
    sta $5007
    lda #$F0
    sta $8000
    lda #$AA
    sta $8AAA
    lda #$55
    sta $8555
    lda #$80
    sta $8AAA
    lda #$AA
    sta $8AAA
    lda #$55
    sta $8555
    lda #$30
    sta $8000
    lda #$FF
es_wait:
    cmp $8000
    bne es_wait
    cmp $8000
    bne es_wait
    rts
copy_wram_to_flash:
    lda ${CFG5007:04X}
    ora #$05
    sta $5007
    lda #$00
    sta $00
    lda #$60
    sta $01
    lda #$00
    sta $02
    lda #$80
    sta $03
    ldy #$00
    ldx #$20
cpf_loop:
    lda #$F0
    sta $8000
    lda #$AA
    sta $8AAA
    lda #$55
    sta $8555
    lda #$A0
    sta $8AAA
    lda ($00),y
    sta ($02),y
cpf_wait:
    cmp ($02),y
    bne cpf_wait
    cmp ($02),y
    bne cpf_wait
    iny
    bne cpf_loop
    inc $01
    inc $03
    dex
    bne cpf_loop
    rts
helper_fail:
    lda #$00
    sta ${PENDING_SAVE_ID:04X}
    jmp helper_restore
helper_restore:
    lda #$00
    sta $5001
    sta $5000
    lda #${restore_bank_value:02X}
    sta $5005
    lda ${CFG5007:04X}
    sta $5007
    rts
"""
    return assemble_6502(src, ram_origin)


# ============================================================
# V5.22.7: УДАЛЁН блок SUBMENU SERVICES (новое).
# 18 builder-функций (build_submenu_*) — legacy V5.16, перешли на новую
# архитектуру через build_service_menu_ui_nmi/build_service_submenu_draw в
# options_prompt.py. Ни одна из этих функций не вызывалась в builder.py.
# Также ссылались на несуществующие константы (CFG_SIG0_VAL, BUSY_FLAG_ZP,
# SM_*, ORIG_NAMETAB, ORIG_CURSOR) — даже если бы их вызвать, билд упал бы.
# ============================================================


# V5.22.7: build_svc_load_palette_for_screen удалён.
# Вызывался из NMI как часть двухфазной смены экрана. После миграции музыки
# на APU frame counter IRQ механизм перестал быть нужен. Парный код
# (build_scr_chg_trigger, SCR_CHG_PENDING/DESC, проверка фазы 1 в
# combined_nmi_hook) тоже удалён — см. fixed_bank.py и options_prompt.py.
# =============================================================================