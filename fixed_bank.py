# fixed_bank.py
# Версия: 5.7
#
# Изменения относительно V5.6:
# - build_menu_init_wrapper: добавлен вызов overlay_init_stub_cpu
#   (копирование S/T из PT0 в PT1 при PPU off, один раз при инициализации меню).
# - build_maybe_flush_with_warning: без изменений.

from config import *
from utils import *
from asm6502 import *


def build_toggle_prg_ram_menu(origin_cpu):
    """
    Универсальная функция включения/выключения PRG RAM.
    Вход: A = $01 (enable) или A = $00 (disable)
    """
    src = f"""
toggle_prg_ram_menu:
    pha
    lda #$00
    sta $5005
    pla
    beq trm_disable
    lda ${CFG5007:04X}
    ora #$01
    sta ${CFG5007:04X}
    sta $5007
    rts
trm_disable:
    lda ${CFG5007:04X}
    and #$FE
    sta ${CFG5007:04X}
    sta $5007
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_enable_prg_ram_menu(origin_cpu):
    src = f"""
enable_prg_ram_menu:
    lda #$00
    sta $5005
    lda ${CFG5007:04X}
    ora #$01
    sta ${CFG5007:04X}
    sta $5007
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_disable_prg_ram_menu(origin_cpu):
    src = f"""
disable_prg_ram_menu:
    lda #$00
    sta $5005
    lda ${CFG5007:04X}
    and #$FE
    sta ${CFG5007:04X}
    sta $5007
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_set_service_bank(origin_cpu):
    src = """
set_service_bank:
    asl a
    asl a
    sta $5005
    rts
"""
    return assemble_6502(src, origin_cpu)


# V1.9: helper для multi_alt — переключает $5005 на data bank 2,
# читает 3 байта из ($5C),y → $60/$61/$62 (custom_idx, old_phys, new_phys),
# возвращает $5005 обратно на service bank. На выходе Y=3.
# КРИТИЧНО: вся работа происходит в fixed bank ($C000+), поэтому
# смена банкинга $8000-$BFFF не ломает текущий PC.
def build_db_read_header(origin_cpu):
    svc_idx   = (SERVICE_BANK_INDEX << 2) & 0xFF
    dbank_idx = (DATA_BANK_INDEX    << 2) & 0xFF
    src = f"""
db_read_header:
    lda #${dbank_idx:02X}
    sta $5005
    ldy #$00
    lda ($5C),y
    sta $60
    iny
    lda ($5C),y
    sta $61
    iny
    lda ($5C),y
    sta $62
    iny
    lda #${svc_idx:02X}
    sta $5005
    rts
"""
    return assemble_6502(src, origin_cpu)


# V1.9: helper для multi_alt — стримит 16 байт из ($5C),y в PPU $2007.
# Y на входе: стартовый offset (3 для orig_chr_old, 19 для orig_chr_new).
# Y на выходе: start + 16. Внутри $8000-$BFFF переключается на data bank,
# после стрима — обратно на service bank. PPU $2007 в CPU $2000-$3FFF
# (всегда доступен независимо от банкинга PRG).
def build_db_stream16(origin_cpu):
    """V1.9: стримит 16 байт из ($5C),Y data bank в PPU $2007.
    Y на входе: стартовый offset. Y на выходе: start+16.
    КАЛЛЕР ДОЛЖЕН ВЫСТАВИТЬ X=$10 (count) ПЕРЕД ВЫЗОВОМ —
    это сэкономило 2 байта в SAFE-2 (lda imm + ldx неявно)."""
    svc_idx   = (SERVICE_BANK_INDEX << 2) & 0xFF
    dbank_idx = (DATA_BANK_INDEX    << 2) & 0xFF
    src = f"""
db_stream16:
    lda #${dbank_idx:02X}
    sta $5005
db_s16_loop:
    lda ($5C),y
    sta $2007
    iny
    dex
    bne db_s16_loop
    lda #${svc_idx:02X}
    sta $5005
    rts
"""
    return assemble_6502(src, origin_cpu)


# V1.9: helper для multi_alt — читает байт count (длина swap-листа) из
# ($5C),y=0 в data bank → $5B, продвигает указатель ($5C/$5D) на +1.
# Используется один раз на каждом подгружаемом/откатываемом экране.
def build_db_read_count(origin_cpu):
    svc_idx   = (SERVICE_BANK_INDEX << 2) & 0xFF
    dbank_idx = (DATA_BANK_INDEX    << 2) & 0xFF
    src = f"""
db_read_count:
    lda #${dbank_idx:02X}
    sta $5005
    ldy #$00
    lda ($5C),y
    sta $5B
    lda #${svc_idx:02X}
    sta $5005
    inc $5C
    bne db_rc_done
    inc $5D
db_rc_done:
    rts
"""
    return assemble_6502(src, origin_cpu)


# =============================================================================
# V5.24: Унифицированный диспетчер service_call.
#
# Раньше каждый stub был 17 байт (php/sei/lda #idx/jsr set_service/jsr target/
# lda #00/jsr set_service/plp/rts). 14 таких стабов = 238 байт в fixed bank.
#
# Новая схема:
#   build_service_call_dispatcher() — общий диспетчер (30 байт) + jmp indirect
#       trampoline (3 байта) + таблица из 14 слов (28 байт) = 61 байт.
#   build_service_call_stub() — теперь 5 байт на stub:
#       lda #idx*2 ; jmp dispatcher
#
# Итого: 14 × 5 + 61 = 131 байт вместо 238 → экономия ~107 байт.
#
# Stub'ы остаются JSR-callable (адреса стабильны), идентично работают для
# bank0 patch'ей. Дискпатчер inlines `sta $5005` (вместо jsr set_service) —
# те же 3 байта, но без цикла на call/rts.
# =============================================================================

# Максимум 14 service-call stub'ов (14 слов = 28 байт таблицы).
SERVICE_CALL_TABLE_MAX = 14


def build_service_call_dispatcher(origin_cpu):
    """V5.24: общий диспетчер для service_call_stub.

    Возвращает (code_bytes, scd_targets_cpu).
    scd_targets — 28-байтная таблица в КОНЦЕ возвращённого блока (placeholder
    нулей). Caller должен залатать её фактическими адресами целей.

    Layout:
      origin+0..29   — диспетчер (30 байт)
      origin+30..32  — jmp ($TMP_LO) trampoline (3 байта)
      origin+33..60  — scd_targets таблица (28 байт, 14 word entries)
    """
    svc_idx_shifted = (SERVICE_BANK_INDEX << 2) & 0xFF
    DISP_SIZE   = 30
    TRAMP_SIZE  = 3
    trampoline_cpu = origin_cpu + DISP_SIZE
    targets_cpu    = origin_cpu + DISP_SIZE + TRAMP_SIZE
    src = f"""
    php
    sei
    tax
    lda ${targets_cpu:04X},x
    sta ${TMP_LO:04X}
    lda ${targets_cpu + 1:04X},x
    sta ${TMP_HI:04X}
    lda #${svc_idx_shifted:02X}
    sta $5005
    jsr ${trampoline_cpu:04X}
    lda #$00
    sta $5005
    plp
    rts
    .byte $6C, ${TMP_LO & 0xFF:02X}, ${(TMP_LO >> 8) & 0xFF:02X}
"""
    code = assemble_6502(src, origin_cpu)
    assert len(code) == DISP_SIZE + TRAMP_SIZE, (
        f"service_call_dispatcher size mismatch: {len(code)} "
        f"!= {DISP_SIZE + TRAMP_SIZE}")
    code += bytes(SERVICE_CALL_TABLE_MAX * 2)
    return code, targets_cpu


def build_service_call_stub(origin_cpu, dispatcher_cpu, target_idx):
    """V5.24: 5-байтный stub: lda #idx*2 ; jmp dispatcher.

    target_idx — 0..(SERVICE_CALL_TABLE_MAX-1).
    Stub пре-удваивает idx (экономит 1 байт в диспетчере: убирает asl).
    PHP/SEI и переключение банка выполняются диспетчером.
    """
    if not (0 <= target_idx < SERVICE_CALL_TABLE_MAX):
        raise ValueError(f"service_call target_idx out of range: {target_idx}")
    src = f"""
service_call_stub:
    lda #${(target_idx * 2) & 0xFF:02X}
    jmp ${dispatcher_cpu:04X}
"""
    return assemble_6502(src, origin_cpu)


# build_service_load_stub / build_service_flush_stub удалены — структурно
# совпадают с service_call_stub, использовать его напрямую.


def build_reset_init_v43(origin_cpu, console_detect_cpu, cfg_init_stub_cpu=None,
                          init_ma_map_ram_cpu=None):
    """
    V5.22: после init_all включаем APU frame counter IRQ:
      STA $4017 = $00  → 4-step mode, IRQ enabled (60 Гц на NTSC, 50 Гц на PAL/Dendy)
    После этого CLI разрешает обработку IRQ. snd_update теперь тикает из IRQ-handler'а
    независимо от состояния PPU и положения в коде меню.

    V5.22.4: после init_all (где snd_init включает APU $4015=$0F и $4000/$4004=$8F)
    читаем CFG_CACHE.MUSIC и при выключенной музыке немедленно мут'аем APU.
    Это устраняет "доли секунды звука" при reset когда пользователь выключил музыку.
    cfg_init_stub_cpu обязательно нужно передать чтобы инициализировать CFG_CACHE.

    V5.23 Шаг 1: init_ma_map_ram_cpu — инициализирует MA_MAP_RAM из data-блока.
    """
    if CONSOLE_DETECT_MODE == "reset":
        detect_block = f"    jsr ${console_detect_cpu:04X}\n"
    else:
        detect_block = f"    lda #$00\n    sta ${CONSOLE_TYPE_RAM:04X}\n"

    # V5.22.4: блок APU-mute если CFG.MUSIC=0
    apu_mute_block = ""
    if cfg_init_stub_cpu is not None:
        apu_mute_block = f"""
    ; --- V5.22.4: APU mute если музыка выключена в CFG ---
    jsr ${cfg_init_stub_cpu:04X}    ; гарантируем CFG_CACHE заполнен
    lda ${CFG_CACHE:04X}
    and #$01                  ; CFG_MUSIC bit
    bne ri_apu_keep
    lda #$00
    sta $4015                 ; mute все каналы
ri_apu_keep:
"""

    # V5.23 Шаг 1: инициализация MA_MAP_RAM
    ma_init_block = ""
    if init_ma_map_ram_cpu is not None:
        ma_init_block = f"    jsr ${init_ma_map_ram_cpu:04X}    ; V5.23 Шаг 1: MA_MAP_RAM\n"

    src = f"""
reset_init:
    lda #$02
    sta $5007
    bit $2002
    lda #$00
    sta $2006
    sta $2006
    lda #$00
    sta $00
    lda #$80
    sta $01
    ldy #$00
    ldx #$20
ri_copy:
    lda ($00),y
    sta $2007
    iny
    bne ri_copy
    inc $01
    dex
    bne ri_copy
    lda #$08
    sta $5007
    lda #$00
    sta $5005
    sta ${MENU_ACTIVE_FLAG:04X}
    jsr $C44B
    lda #$08
    sta ${CFG5007:04X}
{detect_block}
{apu_mute_block}
    ; --- V5.22: APU frame counter IRQ для непрерывного звука ---
    lda #$00
    sta ${SND_BUSY:04X}
    sta ${IRQ_DIV_COUNTER:04X}     ; V5.22.7: счётчик делителя темпа музыки
    sta ${MA_BACKUP_DEPTH:04X}     ; V5.23.1: multi_alt swap depth = 0
    sta $06E7                      ; V5.23.2: ALT_ACTIVE_FLAG = 0 (чистое состояние)
    lda #$FF
    sta ${MA_LOADED_SCREEN:04X}    ; V5.23.1: ни один экран не загружен
{ma_init_block}    lda #$40
    sta $4017            ; сначала отключаем (страховка от спорадического IRQ)
    bit $4015            ; ACK любого pending IRQ
    lda #$00
    sta $4017            ; включаем 4-step mode с IRQ
    cli                  ; разрешаем IRQ (снимаем флаг I после SEI в reset)
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_irq_handler(origin_cpu, music_rate="auto"):
    """
    V5.22: IRQ-обработчик для APU frame counter.
    V5.22.10: упрощённая версия — три режима, всегда читает CONSOLE_TYPE_RAM.

    Алгоритм:
      auto = частота кадров экрана:
             NTSC  → 60 Hz (тик каждый IRQ)
             PAL   → 50 Hz (тик каждый IRQ — APU уже 50)
             Dendy → 50 Hz (skip каждый 6-й IRQ от 60 нативных)

      "60" = принудительно 60 Hz:
             NTSC/Dendy: тик каждый IRQ (≈60)
             PAL: 1 тик + double-tick каждый 5-й = 50+50/5 = 60

      "50" = принудительно 50 Hz:
             PAL: тик каждый IRQ (≈50)
             NTSC/Dendy: skip каждый 6-й = 60-60/6 = 50

    CONSOLE_TYPE_RAM bits (выставляется console_detect в reset_init):
      bit0 = NTSC   bit1 = PAL   bit2 = Dendy   bit3 = new Dendy

    Используем единую логику «нужен делитель?»:
      auto: нужен на Dendy (bit2 OR bit3) — тогда skip 1/6
      "60": нужен на PAL (bit1) — тогда double-tick каждый 5-й
      "50": нужен на NTSC OR Dendy (bit0 OR bit2 OR bit3) — тогда skip 1/6
            эквивалент: НЕ PAL → skip
    """
    # V5.22.x: пролог cross-validation в console_detect (метод B) удалён в V5.23.
    # CD_DETECT_MODE никогда не выставлялся в 1 после V5.22.10 — это было
    # 6 лишних циклов на каждом IRQ.

    if music_rate == "auto":
        # Тик каждый IRQ. На Dendy (bit2 или bit3) добавляем skip 1/6.
        # V5.22.16: при UNDETECTED (CONSOLE_TYPE_RAM=0) — тик каждый IRQ
        # без делителя (native APU rate).
        src = f"""
irq_handler:
    pha
    txa
    pha
    tya
    pha
    bit $4015
    lda ${SND_BUSY:04X}
    bne irq_skip
    lda $9E
    bne irq_skip
    ; проверка типа консоли
    lda ${CONSOLE_TYPE_RAM:04X}
    beq irq_tick_auto    ; UNDETECTED → тикаем без делителя
    and #$0C             ; bit2 = Dendy, bit3 = new Dendy
    beq irq_tick_auto    ; не Dendy — тикаем
    ; Dendy: skip каждый 6-й
    inc ${IRQ_DIV_COUNTER:04X}
    lda ${IRQ_DIV_COUNTER:04X}
    cmp #$06
    bne irq_tick_auto
    lda #$00
    sta ${IRQ_DIV_COUNTER:04X}
    jmp irq_skip         ; пропуск (Dendy 60→50)
irq_tick_auto:
    lda #$01
    sta ${SND_BUSY:04X}
    jsr $FA67
    lda #$00
    sta ${SND_BUSY:04X}
irq_skip:
    pla
    tay
    pla
    tax
    pla
    rti
"""

    elif music_rate == "60":
        # V5.22.16: при UNDETECTED — тикаем без double-tick (native).
        src = f"""
irq_handler:
    pha
    txa
    pha
    tya
    pha
    bit $4015
    lda ${SND_BUSY:04X}
    bne irq_skip
    lda $9E
    bne irq_skip
    lda #$01
    sta ${SND_BUSY:04X}
    jsr $FA67            ; первый тик (всегда)
    lda ${CONSOLE_TYPE_RAM:04X}
    beq irq_done60       ; UNDETECTED → выход без double-tick
    and #$02             ; bit1 = PAL
    beq irq_done60       ; не PAL — выход
    ; PAL: double-tick каждый 5-й
    inc ${IRQ_DIV_COUNTER:04X}
    lda ${IRQ_DIV_COUNTER:04X}
    cmp #$05
    bne irq_done60
    lda #$00
    sta ${IRQ_DIV_COUNTER:04X}
    jsr $FA67            ; второй тик (PAL 50→60)
irq_done60:
    lda #$00
    sta ${SND_BUSY:04X}
irq_skip:
    pla
    tay
    pla
    tax
    pla
    rti
"""

    elif music_rate == "50":
        # V5.22.16: при UNDETECTED — тикаем без skip (native).
        src = f"""
irq_handler:
    pha
    txa
    pha
    tya
    pha
    bit $4015
    lda ${SND_BUSY:04X}
    bne irq_skip
    lda $9E
    bne irq_skip
    ; проверка типа консоли
    lda ${CONSOLE_TYPE_RAM:04X}
    beq irq_tick50       ; UNDETECTED → тикаем без skip
    and #$02
    bne irq_tick50       ; PAL → всегда тикать
    ; не PAL → skip 1/6 (NTSC 60→50, Dendy 60→50)
    inc ${IRQ_DIV_COUNTER:04X}
    lda ${IRQ_DIV_COUNTER:04X}
    cmp #$06
    bne irq_tick50
    lda #$00
    sta ${IRQ_DIV_COUNTER:04X}
    jmp irq_skip
irq_tick50:
    lda #$01
    sta ${SND_BUSY:04X}
    jsr $FA67
    lda #$00
    sta ${SND_BUSY:04X}
irq_skip:
    pla
    tay
    pla
    tax
    pla
    rti
"""
    else:
        raise ValueError(f"unknown music_rate: {music_rate}")

    return assemble_6502(src, origin_cpu)


def build_safe_snd_stop_music(origin_cpu):
    """
    V5.22.1: обёртка-страховка над snd_stop_music ($FF36).

    snd_stop_music в оригинальном коде НЕ выставляет $9E busy_flag, поэтому
    может быть прерван нашим APU IRQ → snd_update попадёт на полу-обновлённые
    shadow registers / stream pointers и зацикливается.

    Обёртка делает PHP/SEI на время вызова, что физически блокирует IRQ.
    """
    src = """
safe_snd_stop_music:
    php
    sei
    jsr $FF36
    plp
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_safe_snd_stop_sfx(origin_cpu):
    src = """
safe_snd_stop_sfx:
    php
    sei
    jsr $FF93
    plp
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_safe_snd_play_music(origin_cpu):
    src = """
safe_snd_play_music:
    php
    sei
    jsr $FE0E
    plp
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_safe_snd_play_sfx(origin_cpu):
    src = """
safe_snd_play_sfx:
    php
    sei
    jsr $FE9B
    plp
    rts
"""
    return assemble_6502(src, origin_cpu)




def build_maybe_flush_with_warning(origin_cpu, enable_ram_cpu, disable_ram_cpu,
                                    show_warning_cpu, flush_stub_cpu):
    SIG0_VAL  = 0xC7
    SIG1_VAL  = 0x2A
    CFG_MASK  = 0x03
    CFG_S_SIG0  = 0x7FF0
    CFG_S_SIG1  = 0x7FF1
    CFG_S_FLAGS = 0x7FF2

    src = f"""
maybe_flush_with_warning:
    jsr ${enable_ram_cpu:04X}
    lda #$43
    cmp ${SRAM_SIG0 + 0:04X}
    bne mfw_no_sig
    lda #$4F
    cmp ${SRAM_SIG0 + 1:04X}
    bne mfw_no_sig
    lda #$4F
    cmp ${SRAM_SIG0 + 2:04X}
    bne mfw_no_sig
    lda #$4C
    cmp ${SRAM_SIG0 + 3:04X}
    bne mfw_no_sig
    lda #$53
    cmp ${SRAM_SIG0 + 4:04X}
    bne mfw_no_sig
    lda #$41
    cmp ${SRAM_SIG0 + 5:04X}
    bne mfw_no_sig
    lda #$56
    cmp ${SRAM_SIG0 + 6:04X}
    bne mfw_no_sig
    lda #$45
    cmp ${SRAM_SIG0 + 7:04X}
    bne mfw_no_sig

    lda ${SRAM_LAST_SAVE:04X}
    beq mfw_none

    jsr ${disable_ram_cpu:04X}
    jsr ${show_warning_cpu:04X}
    jsr ${flush_stub_cpu:04X}

    lda ${CFG_BACKEND:04X}
    beq mfw_cfg_done
    jsr ${enable_ram_cpu:04X}
    lda #${SIG0_VAL:02X}
    sta ${CFG_S_SIG0:04X}
    lda #${SIG1_VAL:02X}
    sta ${CFG_S_SIG1:04X}
    lda ${CFG_CACHE:04X}
    and #${CFG_MASK:02X}
    sta ${CFG_S_FLAGS:04X}
    jsr ${disable_ram_cpu:04X}
mfw_cfg_done:

    jsr $C1AB
    rts
mfw_none:
mfw_no_sig:
    jsr ${disable_ram_cpu:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)


# V5.22.7: первое (V5.20) определение build_menu_init_wrapper удалено как dead code.
# В Python второе определение перекрывает первое, использовалась только версия с
# APU IRQ инициализацией (см. ниже).


def build_nav_wrapper(origin_cpu, name, original_cpu, save_state_cpu):
    src = f"""
{name}:
    jsr ${original_cpu:04X}
    jsr ${save_state_cpu:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_load_state(origin_cpu, enable_ram_cpu, disable_ram_cpu):
    src = f"""
load_state:
    jsr ${enable_ram_cpu:04X}
    lda #$43
    cmp ${SRAM_SIG0 + 0:04X}
    bne ls_bad
    lda #$4F
    cmp ${SRAM_SIG0 + 1:04X}
    bne ls_bad
    lda #$4F
    cmp ${SRAM_SIG0 + 2:04X}
    bne ls_bad
    lda #$4C
    cmp ${SRAM_SIG0 + 3:04X}
    bne ls_bad
    lda #$53
    cmp ${SRAM_SIG0 + 4:04X}
    bne ls_bad
    lda #$41
    cmp ${SRAM_SIG0 + 5:04X}
    bne ls_bad
    lda #$56
    cmp ${SRAM_SIG0 + 6:04X}
    bne ls_bad
    lda #$45
    cmp ${SRAM_SIG0 + 7:04X}
    bne ls_bad
    lda ${SRAM_MENU_LO:04X}
    sta $18
    lda ${SRAM_MENU_MID:04X}
    sta $19
    lda ${SRAM_MENU_HI:04X}
    sta $17
    jsr ${disable_ram_cpu:04X}
    lda $17
    beq ls_chk_mid
    jmp ls_clear
ls_chk_mid:
    lda $19
    cmp #$27
    bcc ls_ok
    bne ls_clear
    lda $18
    cmp #$0F
    bcs ls_clear
ls_ok:
    rts
ls_bad:
    jsr ${disable_ram_cpu:04X}
ls_clear:
    lda #$00
    sta $17
    sta $18
    sta $19
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_save_state(origin_cpu, enable_ram_cpu, disable_ram_cpu):
    src = f"""
save_state:
    jsr ${enable_ram_cpu:04X}
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
    jsr ${disable_ram_cpu:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_div24_16_safe2(origin_cpu):
    """
    V5.22.3: 24-bit / 16-bit binary long division.

    Размещается в SAFE-2 (мёртвая зона $EECC..$EFBF, 244 байта).

    Вход:
      $5A:$5B:$5C  = dividend (24-bit, low to high)
      $5D:$5E      = divisor  (16-bit, low to high)
    Выход:
      $5A:$5B:$5C  = quotient (24-bit)
      $5F:$60      = remainder (16-bit)

    Время: гарантированно ~24 итерации × ~30 циклов = ~720 циклов.
    Заменяет наивный subtract-loop в init_page_state, который на slot=9990
    + unique_count=2 крутился 4995 раз = ~150 000 циклов = 4.5 кадра.

    Алгоритм: shift-and-subtract.
      remainder = 0
      for 24 bits:
        shift dividend left, MSB into remainder (lowest bit)
        if remainder >= divisor:
          remainder -= divisor
          set quotient.lowbit = 1
    """
    src = """
div24_16:
    lda #$00
    sta $5F             ; remainder lo
    sta $60             ; remainder hi
    ldx #$18            ; 24 bits
d24_loop:
    asl $5A
    rol $5B
    rol $5C
    rol $5F
    rol $60
    lda $5F
    sec
    sbc $5D
    tay
    lda $60
    sbc $5E
    bcc d24_no_sub
    sty $5F
    sta $60
    inc $5A             ; set quotient.bit0 (lowest)
d24_no_sub:
    dex
    bne d24_loop
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_ext_draw_list_line(origin_cpu, unique_count, fetch_stub_cpu,
                              div24_16_cpu=None, ma_translate_cpu=None):
    """
    V5.22.3: init_page_state теперь использует div24_16 (binary long division)
    из SAFE-2, заменяя наивный O(N) subtract-loop. На slot=9990 + unique=2
    выигрыш ~150 000 циклов = 4.5 кадра.

    V5.22.7: поддержка NAME_MODE=1 (короткие имена 11 символов фикс).
    В mode=1 marquee всегда выключен (отсутствуют MARQ_* RAM-адреса).
    
    V5.23 Шаг 1: ma_translate_cpu — адрес helper'а подмены virtual byte → phys.
    Вызывается перед записью каждого байта имени в PPU $2007.
    """
    # marquee активен только в mode=2
    use_marquee = (NAME_MODE == 2) and MARQUEE_ENABLED

    unique_lo  = unique_count & 0xFF
    unique_hi  = (unique_count >> 8) & 0xFF

    if use_marquee:
        extra_init = f"""
    lda #$00
    sta ${MARQ_ACTIVE:04X}
    sta ${RESTORE_PENDING:04X}
    lda #$FF
    sta ${MARQ_ROW:04X}
    lda #$00
    ldx #$00
ips_clr_cache:
    sta ${PAGE_DIRIDX_BASE:04X},x
    inx
    cpx #$20
    bne ips_clr_cache
"""
    else:
        extra_init = f"""
    lda #$00
    ldx #$00
ips_clr_cache:
    sta ${PAGE_DIRIDX_BASE:04X},x
    inx
    cpx #$20
    bne ips_clr_cache
"""

    src = f"""
ext_draw_list_line:
    pha
    txa
    pha
    tya
    pha

    lda $1C
    and #$0F
    bne draw_noinit
    jsr init_page_state
draw_noinit:

    lda $1C
    sta $56
    lda $1D
    sta $57
    lda $1F
    sta $58
    inc $56
    bne disp_ok1
    inc $57
    bne disp_ok1
    inc $58
disp_ok1:
    ldy #$00
    ; V5.22.3: инкрементальный BCD.
    ; bcd_convert делает до 63 итераций subtract-loop на номере 9999, что
    ; растягивает per-line с 245 до 2500 циклов. Для page rebuild это +1 кадр.
    ; Используем TMP2 как флаг: 0 = нужен полный BCD (первая строка page),
    ; 1 = инкрементируем результат предыдущей строки.
    lda ${TMP2:04X}
    bne bcd_inc
    jsr $C3B4              ; полный BCD на первой строке
    lda #$01
    sta ${TMP2:04X}
    jmp bcd_done
bcd_inc:
    ; цифры в $6C..$6F, тайл-коды $01..$0A ('0'..'9').
    ; цикл по индексам: X=4 -> $6F (единицы), X=1 -> $6C (тысячи)
    ldx #$04
inc_loop:
    inc $6B,x
    lda $6B,x
    cmp #$0B
    bne bcd_done
    lda #$01
    sta $6B,x
    dex
    bne inc_loop
    ; wrap 9999 -> 0000: все цифры уже в '0', $6B не трогаем
    ; (init_page_state перевыставит TMP2=0 для следующего page rebuild)
bcd_done:
    lda $6C
    sta ${PPUDATA:04X}
    lda $6D
    sta ${PPUDATA:04X}
    lda $6E
    sta ${PPUDATA:04X}
    lda $6F
    sta ${PPUDATA:04X}
    lda #$25
    sta ${PPUDATA:04X}

    lda $1C
    and #$0F
    sta ${ROW_TMP:04X}

    lda ${CUR_BASE_LO:04X}
    sta ${DIR_INDEX_LO:04X}
    lda ${CUR_BASE_HI:04X}
    sta ${DIR_INDEX_HI:04X}
    jsr ${fetch_stub_cpu:04X}

    ldy #$00
name_loop:
    lda ${DIR_ENTRY_BUF:04X},y
    bne nm_ok
    lda #$26
    bne nm_write           ; всегда non-zero ($26)
nm_ok:
    {("jsr $%04X" % ma_translate_cpu) if ma_translate_cpu else ""}
nm_write:
    sta ${PPUDATA:04X}
    iny
    cpy #${NAME_VISIBLE:02X}
    bne name_loop

    lda #$26
    sta ${PPUDATA:04X}

    lda ${CUR_REPEAT:04X}
    jsr print_repeat

    lda ${ROW_TMP:04X}
    asl a
    tay
    lda ${CUR_BASE_LO:04X}
    sta ${PAGE_DIRIDX_BASE:04X},y
    iny
    lda ${CUR_BASE_HI:04X}
    sta ${PAGE_DIRIDX_BASE:04X},y

    inc ${CUR_BASE_LO:04X}
    bne adv_cmp
    inc ${CUR_BASE_HI:04X}
adv_cmp:
    lda ${CUR_BASE_LO:04X}
    cmp #${unique_lo:02X}
    bne draw_done
    lda ${CUR_BASE_HI:04X}
    cmp #${unique_hi:02X}
    bne draw_done
    lda #$00
    sta ${CUR_BASE_LO:04X}
    sta ${CUR_BASE_HI:04X}
    inc ${CUR_REPEAT:04X}
    lda ${CUR_REPEAT:04X}
    cmp #100
    bne draw_done
    lda #$00
    sta ${CUR_REPEAT:04X}

draw_done:
    pla
    tay
    pla
    tax
    pla
    rts

init_page_state:
    ; V5.22.3: сбросить флаг инкрементального BCD - первая строка page будет
    ; делать полный bcd_convert, остальные 15 - инкремент.
    lda #$00
    sta ${TMP2:04X}
{extra_init}
    ; V5.22.3: O(1) деление через div24_16 в SAFE-2.
    ; Старый код: subtract-loop, до 4995 итераций (slot=9990, uniq=2).
    ; Новый: 24 итерации binary long division (~720 циклов фикс).
    ;
    ; div24_16 контракт:
    ;   in:  $5A:$5B:$5C dividend, $5D:$5E divisor
    ;   out: $5A:$5B:$5C quotient,  $5F:$60 remainder
    ;
    ; ШАГ 1: slot ($1C:$1D:$1F) / unique_count -> quot, page_slot
    lda $1C
    sta $5A
    lda $1D
    sta $5B
    lda $1F
    sta $5C
    lda #${unique_lo:02X}
    sta $5D
    lda #${unique_hi:02X}
    sta $5E
    jsr ${div24_16_cpu:04X}
    ; remainder ($5F:$60) -> page slot (CUR_BASE)
    lda $5F
    sta ${CUR_BASE_LO:04X}
    lda $60
    sta ${CUR_BASE_HI:04X}
    ;
    ; ШАГ 2: quotient ($5A:$5B:$5C) % 100 -> CUR_REPEAT
    ; quotient уже в $5A:$5B:$5C, dividend для второго деления.
    ; divisor = 100 = $0064:$0000
    lda #100
    sta $5D
    lda #$00
    sta $5E
    jsr ${div24_16_cpu:04X}
    ; remainder lo ($5F) = quotient % 100 = CUR_REPEAT
    lda $5F
    sta ${CUR_REPEAT:04X}
    rts

print_repeat:
    sta ${TMP_LO:04X}
    lda #$01
    sta ${TMP_HI:04X}
pr_loop:
    lda ${TMP_LO:04X}
    cmp #10
    bcc pr_done
    sec
    sbc #10
    sta ${TMP_LO:04X}
    inc ${TMP_HI:04X}
    jmp pr_loop
pr_done:
    lda ${TMP_HI:04X}
    sta ${PPUDATA:04X}
    lda ${TMP_LO:04X}
    clc
    adc #$01
    sta ${PPUDATA:04X}
    rts
"""
    return assemble_6502(src, origin_cpu)


def build_ext_launch_game(origin_cpu, trampoline_cpu, save_state_cpu,
                           load_stub_cpu, incompat_stub_cpu, menu_init_cpu,
                           fetch_stub_cpu=None, console_detect_cpu=None,
                           pre_launch_clear_cpu=None):
    """
    V5.22.7: поддержка NAME_MODE=1 (entry size 32 байта, layout другой).
    V5.24:   добавлен pre_launch_clear_cpu — очистка PPU/APU состояния
             (OAM, nametables, APU regs $4000..$4013) перед передачей
             управления launcher'у. Аналог `compatible_console:` у Cluster.
    """
    if fetch_stub_cpu is None:
        raise ValueError("fetch_stub_cpu обязателен")
    if CONSOLE_DETECT_MODE == "launch" and console_detect_cpu is None:
        raise ValueError("console_detect_cpu обязателен при CONSOLE_DETECT_MODE='launch'")

    # Offsets внутри entry в DIR_ENTRY_BUF
    if NAME_MODE == 1:
        # Layout 32 байта:
        # [0..10] name (11 байт), [11] 0x00, [12..23] launch12,
        # [24] save_id, [25] compat, [26..31] zeros
        launch_ofs = 12
        saveid_ofs = 24
        compat_ofs = 25
    else:
        # Layout 64 байта:
        # [0..47] name48, [48..59] launch12, [60] save_id, [61] compat, [62..63] zeros
        launch_ofs = NAME_MAX
        saveid_ofs = NAME_MAX + 12
        compat_ofs = NAME_MAX + 13

    if CONSOLE_DETECT_MODE == "launch":
        detect_block = f"""
    lda ${DIR_ENTRY_BUF + compat_ofs:04X}
    beq launch_compatible
    jsr ${console_detect_cpu:04X}
    lda ${DIR_ENTRY_BUF + compat_ofs:04X}
    and ${CONSOLE_TYPE_RAM:04X}
    beq launch_compatible
    jsr ${incompat_stub_cpu:04X}
    ; V5.24: A_BLOCK_REQ=1 — fixed_nav_hook замаскирует A в меню пока
    ; пользователь её не отпустит, иначе мгновенный повторный launch.
    lda #$01
    sta ${A_BLOCK_REQ:04X}
    sta ${MENU_ACTIVE_FLAG:04X}
    jmp ${menu_init_cpu:04X}
"""
    else:
        detect_block = f"""
    lda ${DIR_ENTRY_BUF + compat_ofs:04X}
    and ${CONSOLE_TYPE_RAM:04X}
    beq launch_compatible
    jsr ${incompat_stub_cpu:04X}
    ; V5.24: A_BLOCK_REQ=1 — fixed_nav_hook замаскирует A в меню пока
    ; пользователь её не отпустит, иначе мгновенный повторный launch.
    lda #$01
    sta ${A_BLOCK_REQ:04X}
    sta ${MENU_ACTIVE_FLAG:04X}
    jmp ${menu_init_cpu:04X}
"""

    # V5.24: блок pre_launch_clear подставляется опционально (если
    # pre_launch_clear_cpu == None — старое поведение без очистки).
    if pre_launch_clear_cpu is not None:
        clear_block = f"    jsr ${pre_launch_clear_cpu:04X}\n"
    else:
        clear_block = ""

    src = f"""
ext_launch_game:
    sei
    cld
    ; V5.22: явно отключаем APU frame IRQ перед запуском игры.
    ; SEI уже блокирует IRQ для нас, но игра позже сделает CLI.
    ; Без этой строки игра ловила бы наш отложенный IRQ и улетала бы в $C001.
    lda #$40
    sta $4017            ; mode 1, IRQ disabled
    bit $4015            ; ACK любого pending frame IRQ
    jsr $C1AB

    lda #$00
    sta ${MENU_ACTIVE_FLAG:04X}

    lda $18
    and #$0F
    tax

    txa
    asl a
    tay
    lda ${PAGE_DIRIDX_BASE:04X},y
    sta ${DIR_INDEX_LO:04X}
    iny
    lda ${PAGE_DIRIDX_BASE:04X},y
    sta ${DIR_INDEX_HI:04X}

    jsr ${fetch_stub_cpu:04X}

    ldy #$00
launch_copy_loop:
    lda ${DIR_ENTRY_BUF + launch_ofs:04X},y
    sta $0060,y
    iny
    cpy #$0C
    bne launch_copy_loop

    lda ${DIR_ENTRY_BUF + saveid_ofs:04X}
    sta ${PENDING_SAVE_ID:04X}
{detect_block}
launch_compatible:
    ; V5.24: чистим PPU (OAM + nametable) и APU regs ДО загрузки сейва
    ; и прыжка в trampoline. После этого вызывающий уже не контролирует PPU.
{clear_block}    jsr ${load_stub_cpu:04X}
    jsr ${save_state_cpu:04X}
    jmp ${trampoline_cpu:04X}
"""
    return assemble_6502(src, origin_cpu)


# =============================================================================
# V5.22.7: build_scr_chg_trigger удалён.
# Двухфазный механизм смены экрана был нужен в V5.21 чтобы не пропускать тики
# music-update во время busy-wait $C1A1. После миграции snd_update на APU
# frame counter IRQ (V5.22) музыка тикает независимо от main thread, и
# двухфазный механизм стал избыточным. Парный builder
# build_svc_load_palette_for_screen из service_bank.py также удалён.
# =============================================================================


def build_menu_init_wrapper(origin_cpu,
                             maybe_flush_cpu, load_state_cpu, save_state_cpu,
                             clear_work_cpu, recalc_cpu,
                             marquee_reset_cpu=None,
                             overlay_init_stub_cpu=None,
                             alt_ensure_stub_cpu=None,
                             enable_ram_cpu=None,
                             disable_ram_cpu=None,
                             init_ma_map_ram_cpu=None,
                             multi_alt_swap_stub_cpu=None):
    """
    V5.7: добавлен overlay_init_stub_cpu.
    Вызывается с PPU off (сразу после $C1AB) — безопасно для CHR copy.
    TEXT_OVL_INIT сбрасывается в 0 чтобы init выполнился заново при следующем входе в меню.
    
    V5.20: добавлен alt_ensure_stub_cpu.
    Вызывается после $C26C (построение nametable) для загрузки ALT-тайлов на screen13.
    
    V5.22 (фикс паузы музыки): добавлен STA $9E перед JSR $FE0E для сброса busy_flag.
    
    V5.23 Шаг 1: init_ma_map_ram_cpu — повторная инициализация MA_MAP_RAM
    при возврате в меню после игры.
    """
    extra_marquee = ""
    if NAME_MODE == 2 and marquee_reset_cpu is not None and MARQUEE_ENABLED:
        extra_marquee = f"    jsr ${marquee_reset_cpu:04X}\n"

    # overlay init: вызываем пока PPU off (после $C1AB но до $C1B4)
    overlay_block = ""
    if overlay_init_stub_cpu is not None:
        overlay_block = f"""
    ; сброс флага + init overlay тайлов (S/T) пока PPU выключен
    lda #$00
    sta ${TEXT_OVL_INIT:04X}
    jsr ${overlay_init_stub_cpu:04X}
"""

    # V5.23 Шаг 1: восстановление MA_MAP_RAM
    ma_init_block = ""
    if init_ma_map_ram_cpu is not None:
        ma_init_block = f"    jsr ${init_ma_map_ram_cpu:04X}    ; V5.23 Шаг 1: восстановить MA_MAP_RAM\n"

    # alt ensure: вызываем ПЕРЕД $C26C если screen==13
    # ОПТИМИЗАЦИЯ: загрузка ДО построения nametable
    # V5.23.4: если есть multi_alt_swap_stub — вызываем его (универсальный swap),
    # иначе используем старый alt_ensure_stub (только для screen13).
    alt_block_before = ""
    if multi_alt_swap_stub_cpu is not None:
        alt_block_before = f"""
    ; V5.23.4: вычислить текущий screen и сделать ПОЛНЫЙ multi_alt swap
    ; ДО первого построения nametable. Это критично после reset на любом
    ; экране где нужны кастомы (screen13 в первую очередь).
    jsr $C0EE
    sta ${SCREEN_CACHE:04X}
    jsr ${multi_alt_swap_stub_cpu:04X}
"""
    elif alt_ensure_stub_cpu is not None:
        alt_block_before = f"""
    ; вычислить текущий screen и проверить ==13
    jsr $C0EE
    sta ${SCREEN_CACHE:04X}
    cmp #$0D
    bne miw_skip_alt
    ; screen==13: загрузить ALT-тайлы ДО построения nametable (PPU off)
    jsr ${enable_ram_cpu:04X}
    jsr ${alt_ensure_stub_cpu:04X}
    jsr ${disable_ram_cpu:04X}
miw_skip_alt:
"""

    src = f"""
menu_init_wrapper:
    jsr $C1AB
    ; V5.22: гарантируем включённый APU frame IRQ (на случай возврата из incompat-ветки
    ; или повторного входа в меню после launch).
    sei
    lda #$40
    sta $4017            ; mode 1, IRQ disabled (страховка от спорадического)
    bit $4015            ; ACK pending IRQ
    lda #$00
    sta ${SND_BUSY:04X}
    sta ${IRQ_DIV_COUNTER:04X}    ; V5.22.7: сброс делителя темпа музыки
    sta ${MA_BACKUP_DEPTH:04X}    ; V5.23.1: multi_alt swap depth = 0
    sta $06E7                     ; V5.23.2: ALT_ACTIVE_FLAG=0 (чистое состояние)
    lda #$FF
    sta ${MA_LOADED_SCREEN:04X}   ; V5.23.1: forced reload swap при следующем экране
    lda #$00
    sta $4017            ; mode 0, IRQ enabled
    cli
{ma_init_block}{overlay_block}
    lda #$01
    sta ${MENU_ACTIVE_FLAG:04X}
{extra_marquee}
    jsr ${load_state_cpu:04X}
    jsr ${maybe_flush_cpu:04X}
    jsr ${save_state_cpu:04X}
    jsr ${clear_work_cpu:04X}
    jsr ${recalc_cpu:04X}
    jsr $C0EE
    ldx #$00
    jsr $C219
{alt_block_before}
    jsr $C26C
    jsr $C1B4
    ; V5.22.4: условный запуск музыки.
    ; Если CFG.MUSIC=0, не вызываем play_music совсем — иначе слышен короткий
    ; "бзик" от запуска движка перед тем как NMI tick его заглушит.
    lda ${CFG_CACHE:04X}
    and #$01            ; CFG_MUSIC bit
    beq miw_skip_music
    lda #$00
    sta $9D
    sta $9E        ; сброс busy_flag звукового движка перед рестартом музыки
    jsr $FE0E
    jmp miw_after_music
miw_skip_music:
    ; музыка отключена в CFG: явно мут'нем APU чтобы не слышать остатки snd_init
    lda #$00
    sta $4015
    sta $0738            ; SHADOW_4015 (определён в options_prompt.py)
miw_after_music:
    lda #$B6
    sta $0E
    lda #$CB
    sta $0F
    jsr $C5B5
    jmp $C05A
"""
    return assemble_6502(src, origin_cpu)