# roms_list.py
# Версия: V5.22.18
#
# Чтение и генерация списка игр для override порядка и имён в меню.
#
# Поддерживаемые форматы:
#   txt - построчный список с разделителем "|"
#         filename | menu_name        (имя в меню задано)
#         filename                    (имя = filename без .nes)
#         ; comment                   (комментарий)
#         - | TEXT                    (разделитель/категория, не игра)
#
#   csv - стандартный CSV (запятая или ;), кодировка UTF-8
#         filename,menu_name
#         filename,                   (пустое имя = filename без .nes)

import os
import csv
from utils import log


def _strip_nes_ext(name: str) -> str:
    """'Adventure Island.nes' -> 'Adventure Island'"""
    if name.lower().endswith(".nes"):
        return name[:-4]
    return name


def _scan_roms_dir(roms_dir: str):
    """Возвращает отсортированный список .nes файлов в roms_dir."""
    if not os.path.isdir(roms_dir):
        return []
    out = []
    for fn in sorted(os.listdir(roms_dir)):
        if fn.lower().endswith(".nes"):
            out.append(fn)
    return out


# =============================================================================
# Чтение списка
# =============================================================================

def read_list(list_path: str, list_format: str):
    """
    Возвращает список словарей в порядке записи:
      [
        {"filename": "Adventure Island.nes", "menu_name": "ОСТРОВ"},
        ...
      ]

    None если файл не существует.
    Пустой список если файл существует но пуст / не парсится.
    """
    if not os.path.exists(list_path):
        return None

    if list_format == "txt":
        return _read_txt(list_path)
    elif list_format == "csv":
        return _read_csv(list_path)
    return None


def _read_txt(path: str):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(";") or stripped.startswith("#"):
                continue

            # Разделитель/категория — пропускаем (в текущей сборке отдельной
            # строки-разделителя в меню нет, просто игнорим)
            if stripped.startswith("-"):
                continue

            if "|" in stripped:
                fn, name = stripped.split("|", 1)
                fn = fn.strip()
                name = name.strip()
            else:
                fn = stripped
                name = ""

            if not fn:
                continue

            out.append({
                "filename": fn,
                "menu_name": name or _strip_nes_ext(os.path.basename(fn)),
            })
    return out


def _read_csv(path: str):
    """
    Читает CSV. Стандартный разделитель — точка с запятой ';'.
    Запятая ',' может встречаться в именах игр (например '!(),@^_.nes'),
    поэтому она НЕ используется как разделитель.
    Для совместимости с старыми файлами есть fallback на ',' но только если
    в первой строке точно нет ';' и есть ','.
    """
    out = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        first_line = f.readline()
        f.seek(0)
        if ";" in first_line:
            delim = ";"
        elif "\t" in first_line:
            delim = "\t"
        elif "," in first_line:
            delim = ","
        else:
            delim = ";"

        reader = csv.reader(f, delimiter=delim, quotechar='"')
        first = True
        for row in reader:
            if not row:
                continue
            if first:
                first = False
                head_lc = [c.strip().lower() for c in row[:2]]
                if head_lc and head_lc[0] in ("filename", "file", "имя файла", "файл"):
                    continue
            if len(row) == 0:
                continue
            fn = row[0].strip()
            name = row[1].strip() if len(row) > 1 else ""
            if not fn:
                continue
            out.append({
                "filename": fn,
                "menu_name": name or _strip_nes_ext(os.path.basename(fn)),
            })
    return out


# =============================================================================
# Генерация шаблона
# =============================================================================

def generate_template(list_path: str, list_format: str, roms_dir: str):
    """
    Создаёт файл-шаблон по содержимому roms_dir.
    Файл создаётся только если его ещё нет.
    """
    if os.path.exists(list_path):
        return False

    files = _scan_roms_dir(roms_dir)
    if not files:
        log(f"  ⚠️ Папка {roms_dir}/ пуста, шаблон списка не создан")
        return False

    if list_format == "txt":
        _write_txt_template(list_path, files, roms_dir)
    elif list_format == "csv":
        _write_csv_template(list_path, files, roms_dir)
    else:
        return False

    log(f"  📝 Создан шаблон списка: {list_path} ({len(files)} игр)")
    log(f"     Отредактируй порядок строк / имена в меню и запусти билдер снова.")
    return True


_TXT_HEADER = """; Список игр для CoolGirl multi-ROM сборки
;
; Формат строки:
;   filename                    — имя файла, имя в меню = имя файла без .nes
;   filename | MENU NAME        — указать кастомное имя в меню
;   ; ...                       — комментарий
;   - | TEXT                    — разделитель (игнорируется при сборке)
;
; Игры идут в меню в том порядке, в котором они здесь перечислены.
; Чтобы исключить игру — закомментируй её строку через ; в начале.
; Если файл .nes отсутствует физически — игра пропускается с варнингом.
;
; Этот шаблон создан автоматически. Отредактируй и пересобери.
"""

def _write_txt_template(path: str, files, roms_dir: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(_TXT_HEADER)
        f.write("\n")
        for fn in files:
            menu = _strip_nes_ext(fn).upper()
            f.write(f"{fn} | {menu}\n")


def _write_csv_template(path: str, files, roms_dir: str):
    # V5.22.19: используем ';' как разделитель — запятая может встречаться
    # в именах игр (например '!(),@^_.nes') и ломает парсинг при ручном
    # редактировании в Excel (если потеряются кавычки). Excel-европейский
    # открывает ';' напрямую, для US-Excel есть мастер импорта.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";", quotechar='"',
                            quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["filename", "menu_name"])
        for fn in files:
            menu = _strip_nes_ext(fn).upper()
            writer.writerow([fn, menu])


# =============================================================================
# Применение списка к набору игр
# =============================================================================

def apply_list(games, list_entries, roms_dir: str):
    """
    Принимает:
      games         — список объектов NESRom уже загруженных билдером
      list_entries  — результат read_list(): [{filename, menu_name}, ...]
      roms_dir      — для красивого варнинга

    Возвращает новый список games в порядке list_entries, с обновлёнными
    .name. Игры из games которых нет в list_entries — игнорируются (это
    позволяет складывать в roms/ "запас", но включать только нужные).

    Игры из list_entries которых нет в games — пропускаются с варнингом.
    """
    # Индекс: filename (case-insensitive, basename) → game
    by_name = {}
    for g in games:
        key = os.path.basename(g.path).lower()
        by_name[key] = g

    used = set()
    result = []
    for entry in list_entries:
        key = os.path.basename(entry["filename"]).lower()
        g = by_name.get(key)
        if g is None:
            log(f"  ⚠️ В списке указан файл '{entry['filename']}' — "
                f"не найден в {roms_dir}/, пропуск")
            continue
        # Override имени для меню
        g.name = entry["menu_name"]
        used.add(key)
        result.append(g)

    # Файлы из roms/ которые не упомянуты в списке — варнинг и пропуск
    extras = [g for g in games if os.path.basename(g.path).lower() not in used]
    if extras:
        log(f"  ℹ️ В {roms_dir}/ есть {len(extras)} файлов не из списка, "
            f"они не войдут в сборку:")
        for g in extras[:5]:
            log(f"      {os.path.basename(g.path)}")
        if len(extras) > 5:
            log(f"      ... и ещё {len(extras) - 5}")

    return result
