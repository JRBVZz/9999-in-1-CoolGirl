# config.py
# Версия: V5.24.2
# V5.24.2 (V5.32-audit): зафиксирован PROMPT_ARM_PENDING в карте RAM (ранее жил
#          хардкодом в options_prompt.py с V5.30).
#          - mode=2: PROMPT_ARM_PENDING = 0x06F6 (был помечен как свободный).
#                    Свободным остаётся только $06F7.
#          - mode=1: PROMPT_ARM_PENDING = 0x0760 (в mode=1 $06F6 = ROW_TMP!),
#                    берём свободный $0760. Свободным остаётся $0761.
#          Рекомендуется удалить локальное определение PROMPT_ARM_PENDING
#          в options_prompt.py — теперь оно приходит из config (from config import *).
# V5.24.1: фикс критических багов RAM-разметки (mode=2 / NAME_MODE=2):
#          - Баг 1: DIR_HELPER_LIMIT был off-by-one (0x0695 = MUSIC_PENDING+1),
#            что разрешало dir-helper'у залезть на байт MUSIC_PENDING ($0694).
#            Теперь DIR_HELPER_LIMIT = MUSIC_PENDING (0x0694).
#          - Баг 2: PAGE_LAUNCH_BASE (0x0741, 16*12=192 байта → $0741..$0800)
#            перекрывал SND_BUSY($07F0)/MA_LOADED_SCREEN($07F1)/MA_BACKUP_DEPTH($07F2)
#            и весь MA_MAP_RAM($0782..$07DA). Это мёртвый код → выставлен в None.
# V5.24:   добавлен TILES_DIR — drop-in папка с PNG-тайлами кастомов.
#          Альтернатива multi_alt.ini для простых случаев (кинул PNG → собралось).
# V5.23:   добавлен MULTI_ALT_INI для multi_alt.ini (пользовательские кастомы).
# V5.22.6: добавлен слой user_config — пользовательские параметры теперь
#          читаются из settings.ini, архитектурные остаются здесь хардкодом.
# V5.22:   Добавлены SND_BUSY=0x07F0, APUFRAME=$4017 для APU frame IRQ-движка музыки
# V5.12:   Добавлен SNAP_VALID = 0x0740, PAGE_LAUNCH_BASE сдвинут на 0x0741

# === Пользовательские параметры (из settings.ini) ===
# При отсутствии файла или ключа — используется default из строки.
import os
import user_config as _uc

ROM_ORIG       = _uc.get_str("roms",   "source",    "9999 in 1.nes")
ROMS_DIR       = _uc.get_str("roms",   "dir",       "roms")
NES_FIXES_FILE = _uc.get_str("roms",   "nes_fixes", "nes-fixes.json")

# === V5.22.18: список игр для override порядка и имён ===
# Параметр "list" в [roms]:
#   none - не использовать (старое поведение)
#   txt  - использовать games.list
#   csv  - использовать games.csv (для Excel/LibreOffice)
# Файл всегда лежит рядом с settings.ini в корне проекта.
LIST_FORMAT = _uc.get_str("roms", "list", "none").strip().lower()
if LIST_FORMAT not in ("none", "txt", "csv"):
    LIST_FORMAT = "none"

if LIST_FORMAT == "txt":
    LIST_FILE = "games.list"
elif LIST_FORMAT == "csv":
    LIST_FILE = "games.csv"
else:
    LIST_FILE = None

OUTPUT_FORMAT = _uc.get_str("output", "format", "nes")
OUTPUT_BASE   = _uc.get_str("output", "name",   "coolgirl_multi")
OUTPUT = f"{OUTPUT_BASE}.nes" if OUTPUT_FORMAT == "nes" else f"{OUTPUT_BASE}.bin"

BUILD_STAGE = _uc.get_int("build", "stage", 2)

NAME_MODE    = _uc.get_int("menu", "name_mode",    2)
NAME_MAX     = _uc.get_int("menu", "name_max",     48)
NAME_VISIBLE = _uc.get_int("menu", "name_visible", 11)

MARQUEE_ENABLED  = _uc.get_bool("marquee", "enabled", True)
MARQ_STEP_FRAMES = _uc.get_int("marquee",  "speed",   15)

FONT_PATCH_ENABLED = _uc.get_bool("font", "patch", True)
FONT_PATCH_FILE    = _uc.get_str("font",  "file",  "font_patch.ini")

# V5.23.5: автогенерация шаблона multi_alt.ini при первой сборке.
# True (default)  — если multi_alt.ini нет, билдер создаёт подробный шаблон
# False           — не создаёт, работает с базовой таблицей кастомов
MULTI_ALT_AUTO_GENERATE = _uc.get_bool("font", "auto_generate_ini", True)

# V5.23: путь к multi_alt.ini для пользовательских кастомов.
# Если файл не существует — расширения нет (только базовая таблица из multi_alt.py).
MULTI_ALT_INI = _uc.get_str("font", "multi_alt", "multi_alt.ini")

# V5.24: drop-in папка с PNG-тайлами.
# Любой 8×8 ч/б PNG в этой папке автоматически становится кастомным тайлом.
# Имя файла = символ (★.png) или Unicode код-поинт (U+2665.png).
# Подсуффикс @N ограничивает экраны: ★@13.png, ★@5-10.png, ★@0,5,7.png.
# Пустая строка или несуществующая папка — drop-in выключен.
TILES_DIR = _uc.get_str("font", "tiles_dir", "tiles")

MAX_ROM_MB        = _uc.get_int ("hardware", "max_rom_mb",     128)
# V5.23: ROM_MIN_BYTES удалён.
# Минимально возможный размер ROM = GAMES_START ($C0000) + размер игры
# = ~768 KB, что при next_pow2 округляется до 1 MB. Параметр в 128 KB
# никогда не активировался.
ROM_ROUND_TO_POW2 = _uc.get_bool("hardware", "round_to_pow2",  False)
CONSOLE_DETECT_MODE = _uc.get_str("hardware", "console_detect", "reset")
# V5.22.10: console_detect всегда нужен в меню для определения частоты IRQ-handler.
# Старое значение "launch" больше не поддерживается — форсим reset.
if CONSOLE_DETECT_MODE != "reset":
    CONSOLE_DETECT_MODE = "reset"

# === V5.22.10: тип темпа музыки ===
# auto = совпадает с частотой кадров приставки
#        NTSC 60Hz, PAL 50Hz, Dendy 50Hz (как у экрана)
# 60   = принудительно 60Hz (на PAL: doubled tick каждый 5-й IRQ)
# 50   = принудительно 50Hz (на NTSC: skip каждый 6-й IRQ; Dendy: skip 1/6)
MUSIC_RATE = _uc.get_str("audio", "music_rate", "auto").strip().lower()
if MUSIC_RATE not in ("auto", "60", "50"):
    MUSIC_RATE = "auto"

# === V5.22.14: настройки детектора консоли ===
# detect_threshold:
#   "default" — порог PAL/Dendy по середине номиналов (рекоменд.)
#   "safe"    — порог чуть выше (PAL с дрейфом кварца); Dendy сохраняется
DETECT_THRESHOLD = _uc.get_str("audio", "detect_threshold", "default").strip().lower()
if DETECT_THRESHOLD not in ("default", "safe"):
    DETECT_THRESHOLD = "default"

# detect_median: делать 3 замера и брать медиану (защита от глитчей)
DETECT_MEDIAN = _uc.get_bool("audio", "detect_median", True)

# === V5.24: окно измерения детектора (кадров) ===
# Больше кадров -> больше разрыв между NTSC/PAL/Dendy и устойчивее
# классификация, но дольше чёрный экран на ресете (1 кадр = 20 мс).
# Допустимо 4..16; пороги пересчитываются автоматически.
CD_FRAMES = _uc.get_int("audio", "detect_frames", 8)
if CD_FRAMES < 4:
    CD_FRAMES = 4
if CD_FRAMES > 16:
    CD_FRAMES = 16

MAX_SAVE_GAMES = _uc.get_int("saves", "max_save_games", 255)

MAX_ROM_BYTES = MAX_ROM_MB * 1024 * 1024

# === Архитектурные константы (НЕ менять без понимания дела) ===

FIXED_BANK_OFFSET = 0x1C000
UNIQUE_DIR_START  = 0x20000
MAX_UNIQUE_GAMES  = 9999

ROM_ALIGN     = 0x4000

if NAME_MODE == 1:
    UNIQUE_ENTRY_SIZE   = 32
    UNIQUE_NAME_STORE   = 11
    UNIQUE_DIR_RESERVED = 0x50000
else:
    UNIQUE_ENTRY_SIZE   = 64
    UNIQUE_NAME_STORE   = NAME_MAX
    UNIQUE_DIR_RESERVED = 0xA0000

UNIQUE_DIR_LIMIT = UNIQUE_DIR_START + UNIQUE_DIR_RESERVED
GAMES_START      = UNIQUE_DIR_LIMIT

SERVICE_BANK_SIZE   = 0x4000
SERVICE_BANK_INDEX  = 1
SERVICE_BANK_OFFSET = 0x04000

# V1.9: data bank для swap_lists multi_alt.
# Маппится в то же CPU-окно $8000-$BFFF, что и service bank.
# Переключение банков делается ТОЛЬКО из fixed bank ($C000+).
# helper'ы db_read_header / db_stream16 живут в SAFE-2.
DATA_BANK_SIZE   = 0x4000
DATA_BANK_INDEX  = 2
DATA_BANK_OFFSET = 0x08000
DATA_BANK_CPU    = 0x8000   # тот же CPU-адрес что и service bank

RAM_HELPER_ADDR = 0x0400

SAFE     = 0x31CD  # Расширено: включает Flash polling (16б) + стёртый Flash (227б) = +243 байта (было 0x32E0)
SAFE_END = 0x39BF

# === V5.22.3: SAFE-2 — дополнительное окно в bank0 ===
# Адрес $EECC..$EFBF — мёртвая game-id таблица оригинального ROM (244 байта,
# 0 ссылок). Используется для размещения вспомогательного кода когда
# основное SAFE-окно ($31CD..$39BF) заполняется (сейчас 98.7%).
# Сейчас здесь будут жить функции быстрого деления (binary long division)
# для устранения O(N) задержек в init_page_state и print_repeat.
SAFE_2     = 0x2ECC   # bank0 offset = $EECC - $C000
SAFE_2_END = 0x2FBF   # bank0 offset = $EFBF - $C000
SAFE_2_CPU = 0xEECC   # CPU-адрес начала SAFE-2 окна

if NAME_MODE == 1:
    DIR_ENTRY_BUF       = 0x0600
    DIR_INDEX_LO        = 0x0620
    DIR_INDEX_HI        = 0x0621
    RAM_DIR_HELPER_ADDR = 0x0622
    PAGE_COMPAT_BASE  = 0x06B0
    PAGE_SAVEID_BASE  = 0x06C0
    DIR_HELPER_LIMIT  = PAGE_COMPAT_BASE

    PENDING_SAVE_ID = 0x06D0
    SAVE_MODE       = 0x06D1
    SAVE_TMP1       = 0x06D2
    SAVE_TMP2       = 0x06D3
    SAVE_TMP3       = 0x06D4
    SAVE_TMP4       = 0x06D5
    SAVE_TMP5       = 0x06D6
    SAVE_TMP6       = 0x06D7

    MENU_ACTIVE_FLAG = 0x06DF
    CFG5007          = 0x06E0
    CONSOLE_TYPE_RAM = 0x06E1

    CUR_BASE_LO = 0x06F0
    CUR_BASE_HI = 0x06F1
    CUR_REPEAT  = 0x06F2
    CUR_BANK    = 0x06F3
    TMP_LO      = 0x06F4
    TMP_HI      = 0x06F5
    ROW_TMP     = 0x06F6
    TMP2        = 0x06F7

    # === V5.22.7: mode=1 разметка дополнительных RAM-адресов ===
    # Свободный диапазон $073A..$07EF (звуковой движок не трогает $073A+)
    # Marquee выключен в mode=1: эти константы остаются None.
    MARQ_ACTIVE = MARQ_ROW = MARQ_POS = MARQ_LEN = MARQ_FRAME = None
    RESTORE_PENDING = RESTORE_ROW = None
    MARQ_MUL_LO = MARQ_MUL_HI = MARQ_PPU_LO = MARQ_PPU_HI = None
    MARQ_LEN_TMP = RESTORE_BUF = None

    # Submenu / prompt / config / overlay — все используются в mode=1
    SCREEN_CACHE         = 0x073A
    TEXT_OVL_INIT        = 0x073B
    TEXT_OVL_TMP0        = 0x073C
    TEXT_OVL_TMP1        = 0x073D
    TEXT_OVL_TMP2        = 0x073E
    TEXT_OVL_TMP3        = 0x073F
    SNAP_VALID           = 0x0740
    MUSIC_PENDING        = 0x0741
    SUBMENU_ACTIVE       = 0x0742
    SUBMENU_POS          = 0x0743
    SUBMENU_DIRTY        = 0x0744
    SUBMENU_EXIT_REQ     = 0x0745
    OPTIONS_PROMPT_SHOWN = 0x0746
    OPTIONS_PROMPT_BUF   = 0x0747   # 19 bytes: $0747-$0759
    CFG_CACHE            = 0x075A
    CFG_BACKEND          = 0x075B
    CFG_INIT_DONE        = 0x075C
    RESET_CURSOR_REQ     = 0x075D
    A_BLOCK_REQ          = 0x075E
    # V5.22.7: SCR_CHG_PENDING/DESC удалены вместе с двухфазной сменой экрана.
    # Адрес $075F переиспользован под IRQ_DIV_COUNTER (счётчик делителя
    # темпа музыки в irq_handler). Адрес $0761 — свободен.
    IRQ_DIV_COUNTER      = 0x075F

    # V5.32-audit: предвзвод спрайта-буквы S подсказки на 1 кадр (см.
    # options_prompt.py). В mode=1 $06F6 занят ROW_TMP, поэтому берём
    # свободный $0760.
    PROMPT_ARM_PENDING   = 0x0760

    # PAGE_DIRIDX_BASE: 32 байта (16 row × 2 байта индекса)
    # Используется ext_launch_game для определения позиции запуска.
    PAGE_DIRIDX_BASE = 0x0762        # $0762..$0781

    PAGE_LAUNCH_BASE = None          # не используется ни в mode=1, ни в mode=2

else:
    DIR_ENTRY_BUF       = 0x05E0
    DIR_INDEX_LO        = 0x0620
    DIR_INDEX_HI        = 0x0621
    RAM_DIR_HELPER_ADDR = 0x0622

    # V5.24.1 (Баг 1): MUSIC_PENDING объявлен ДО DIR_HELPER_LIMIT, чтобы
    # лимит можно было задать по имени, а не магическим числом.
    MUSIC_PENDING = 0x0694

    # V5.24.1 (Баг 1): было 0x0695 (= MUSIC_PENDING + 1) — off-by-one.
    # Прежний лимит разрешал dir-helper'у вырасти до $0694 включительно и
    # затереть MUSIC_PENDING (он реально используется в options_prompt.py:
    # значения 1=старт / 2=стоп музыки). Теперь лимит = сам MUSIC_PENDING,
    # т.е. хелпер обязан уместиться в $0622..$0693 (114 байт).
    DIR_HELPER_LIMIT    = MUSIC_PENDING

    SUBMENU_ACTIVE   = 0x0695
    SUBMENU_POS      = 0x0696
    SUBMENU_DIRTY    = 0x0697
    SUBMENU_EXIT_REQ = 0x0698

    OPTIONS_PROMPT_SHOWN = 0x0699
    OPTIONS_PROMPT_BUF   = 0x069A  # 19 bytes: $069A-$06AC

    CFG_CACHE     = 0x06AD
    CFG_BACKEND   = 0x06AE
    CFG_INIT_DONE = 0x06AF

    PAGE_DIRIDX_BASE  = 0x06B0

    PENDING_SAVE_ID = 0x06D0
    SAVE_MODE       = 0x06D1
    SAVE_TMP1       = 0x06D2
    SAVE_TMP2       = 0x06D3
    SAVE_TMP3       = 0x06D4
    SAVE_TMP4       = 0x06D5
    SAVE_TMP5       = 0x06D6
    SAVE_TMP6       = 0x06D7

    MARQ_ACTIVE     = 0x06D8
    MARQ_ROW        = 0x06D9
    MARQ_POS        = 0x06DA
    MARQ_LEN        = 0x06DB
    MARQ_FRAME      = 0x06DC
    RESTORE_PENDING = 0x06DD
    RESTORE_ROW     = 0x06DE

    MENU_ACTIVE_FLAG= 0x06DF

    MARQ_MUL_LO  = 0x06E0
    MARQ_MUL_HI  = 0x06E1
    MARQ_PPU_LO  = 0x06E2
    MARQ_PPU_HI  = 0x06E3
    MARQ_LEN_TMP = 0x06E4
    RESTORE_BUF  = 0x06E8

    RESET_CURSOR_REQ = 0x06E5
    A_BLOCK_REQ      = 0x06E6
    # ALT_ACTIVE_FLAG  = 0x06E7 (в prompt_attr_fix.py)

    CFG5007          = 0x06F3
    CONSOLE_TYPE_RAM = 0x06F4

    CUR_BASE_LO = 0x06F8
    CUR_BASE_HI = 0x06F9
    CUR_REPEAT  = 0x06FA
    CUR_BANK    = 0x06FB
    TMP_LO      = 0x06FC
    TMP_HI      = 0x06FD
    ROW_TMP     = 0x06FE
    TMP2        = 0x06FF

    PAGE_COMPAT_BASE = None
    PAGE_SAVEID_BASE = None

    # SNAP_VALID: флаг актуальности SUB_SNAP буферов
    # 0 = нужен новый snapshot, 1 = снапшот актуален
    SNAP_VALID = 0x0740

    # V5.24.1 (Баг 2): было PAGE_LAUNCH_BASE = 0x0741 (16*12 = 192 байта →
    # $0741..$0800). Этот диапазон перекрывал SND_BUSY($07F0),
    # MA_LOADED_SCREEN($07F1), MA_BACKUP_DEPTH($07F2) и весь
    # MA_MAP_RAM($0782..$07DA). Сам PAGE_LAUNCH_BASE нигде не читается —
    # параметры запуска идут через fetch_dir → $0060..$006B / PAGE_DIRIDX_BASE.
    # Поэтому мёртвую зону убираем (None), чтобы исключить скрытое пересечение.
    PAGE_LAUNCH_BASE = None

    SCREEN_CACHE = 0x073A

    # V5.22.7: SCR_CHG_PENDING/DESC_LO/DESC_HI удалены — двухфазный механизм
    # смены экрана больше не используется. Адрес $06F5 переиспользован под
    # IRQ_DIV_COUNTER (счётчик делителя темпа музыки).
    # V5.32-audit: $06F6 занят PROMPT_ARM_PENDING (ниже). Свободен только $06F7.
    IRQ_DIV_COUNTER = 0x06F5

    # V5.30 / V5.32-audit: 1-кадровый предвзвод спрайта-буквы S подсказки
    # «PRESS A FOR OPTIONS» — чтобы спрайт-буква появлялась одновременно с
    # надписью, а не на кадр позже (см. build_service_menu_ui_nmi и
    # build_fixed_combined_nmi_hook в options_prompt.py). Ранее жил локальным
    # хардкодом в options_prompt.py; зафиксирован здесь.
    PROMPT_ARM_PENDING = 0x06F6

    TEXT_OVL_INIT = 0x073B

    TEXT_OVL_TMP0 = 0x073C
    TEXT_OVL_TMP1 = 0x073D
    TEXT_OVL_TMP2 = 0x073E
    TEXT_OVL_TMP3 = 0x073F

# --- SRAM layout ---
SRAM_SIG0      = 0x6000
SRAM_MENU_LO   = 0x6008
SRAM_MENU_MID  = 0x6009
SRAM_MENU_HI   = 0x600A
SRAM_RESERVED  = 0x600B
SRAM_LAST_SAVE = 0x600C

ZP_PTR0 = 0x00
ZP_PTR1 = 0x02

PPUDATA   = 0x2007
PPUCTRL   = 0x2000
PPUMASK   = 0x2001
PPUSTATUS = 0x2002
APUSTATUS = 0x4015
APUFRAME  = 0x4017

# V5.22: APU frame counter IRQ-driven sound engine
# SND_BUSY = 1 во время выполнения snd_update в любом контексте (NMI/IRQ/main).
# Защищает от reentrancy: если NMI/IRQ срабатывает посреди уже идущего snd_update,
# второй вызов просто выходит. Размещаем в самом конце страницы RAM, чтобы
# не пересекаться с зарезервированными зонами проекта.
SND_BUSY = 0x07F0

# V5.23: CD_DETECT_MODE / CD_IRQ_COUNT удалены.
# Это был остаток от метода B (cross-validation) в console_detect, который
# использовался в V5.22.9 и был убран в V5.22.10. После этого пролог в
# irq_handler читал $07F1 каждый IRQ впустую — 6 лишних циклов на тик.
# Адреса $07F1 и $07F2 теперь свободны.

CD_TMP0 = 0x06C0
CD_TMP1 = 0x06C1
CD_TMP2 = 0x06C2
CD_XTMP = 0x06C3

# === V5.23: multi_alt swap-механизм ===
# CURRENT_SCREEN_LOADED — какой экран сейчас активен с точки зрения swap.
# 0xFF = меню не было загружено / swap пуст.
# 0..13 = swap'ы для этого экрана накачены в CHR, backup в SRAM.
# Адрес: $07F1 (свободный после удаления CD_DETECT_MODE).
MA_LOADED_SCREEN = 0x07F1

# MA_BACKUP_DEPTH — сколько слотов сейчас в SRAM backup стеке.
# Адрес: $07F2 (свободный после удаления CD_IRQ_COUNT).
MA_BACKUP_DEPTH = 0x07F2

# MA_BACKUP_BASE — начало SRAM backup-зоны (256 байт = 16 × 16).
# При входе на экран ASM пушит сохранённые CHR в стек:
#   stack[0..15] = (phys_tile, chr_16bytes) — но CHR удобнее хранить плотно.
# Используем layout: 17 байт на запись (1 phys + 16 chr).
# 16 записей × 17 = 272 байта, округляем до 288 = 16 × 18 (с padding для align).
# Размещение: $7C00..$7D1F (288 байт) — в свободной зоне SRAM.
MA_BACKUP_BASE   = 0x7C00
MA_BACKUP_STRIDE = 17    # 1 phys_tile + 16 CHR
MA_BACKUP_SIZE   = 16 * MA_BACKUP_STRIDE   # = 272 байта

# MA_SWAP_DATA_BANK — какой service-banked адрес содержит SWAP_HEADER.
# Заполняется на этапе сборки.

# === V5.23 Шаг 1: виртуальные индексы ===
# MA_MAP_RAM — таблица custom_idx → phys_tile для текущего экрана.
# При смене экрана (Шаг 2) — обновляется.
# При печати в name_loop / marquee_nmi: если byte >= $80, читаем
# MA_MAP_RAM[byte & $7F] и используем как phys_tile.
#
# КРИТИЧНО (V5.7): этот буфер 50 байт. Размещаем в $0782..$07B3 — далеко
# от sound engine ($0700..$0739) и от других подсистем меню.
#   - mode=1 PAGE_DIRIDX_BASE заканчивается на $0781, наш буфер начинается с $0782
#   - в mode=2 эта зона полностью свободна
#   - не пересекается с SND_BUSY/MA_LOADED_SCREEN/MA_BACKUP_DEPTH ($07F0+)
#
# История: в V5.7 был на $0700, но затирал init music ptr ch0 ($0730/$0731)
# звукового движка → музыка ломалась через 30-80 секунд после смены экрана.
MA_MAP_RAM       = 0x0782
MA_MAP_RAM_SIZE  = 89  # синхронно с MA_MAP_PER_SCREEN в multi_alt.py
# 89 = реальный физический потолок: free[13] на оригинальном "9999 in 1.nes".
# Раньше было 96 — но screen 13 (самый забитый фон меню) даёт только 89
# свободных CHR-слотов, поэтому RAM выше 89 байт никогда не используется.
# Уменьшение делает лимит честным: пользователь упирается в RAM-предел тогда же,
# когда упирается и в CHR-предел screen 13.