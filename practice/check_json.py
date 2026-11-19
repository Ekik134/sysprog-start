import json
import shutil
import re
import csv
from pathlib import Path
from datetime import datetime

BASE_DIR = Path.cwd()
INCOMING_DIR = BASE_DIR / "incoming"
SORTED_DIR = BASE_DIR / "sorted"
QUARANTINE_DIR = BASE_DIR / "quarantine"
LOG_FILE = BASE_DIR / "processing_log.csv"

EXPECTED_STRUCTURE = {
    "document_id": str,
    "order_id": str,
    "document_type": str,
    "date": str,
    "customer": str,
    "amount": (int, float)
}

CYRILLIC_PATTERN = re.compile(r'[а-яА-ЯёЁ]')

def has_cyrillic_in_path(file_path: Path) -> bool:
    try:
        rel_path = file_path.relative_to(INCOMING_DIR)
    except ValueError:
        rel_path = file_path
    return bool(CYRILLIC_PATTERN.search(str(rel_path)))

def get_unique_path(target_dir: Path, filename: str) -> Path:
    target_path = target_dir / filename
    if not target_path.exists():
        return target_path
    
    stem = target_path.stem
    suffix = target_path.suffix
    counter = 1
    while True:
        new_path = target_dir / f"{stem}_{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1

def validate_json(data: dict) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "Корневой элемент не является объектом JSON"

    actual_keys = set(data.keys())
    expected_keys = set(EXPECTED_STRUCTURE.keys())
    
    if actual_keys != expected_keys:
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        reasons = []
        if missing:
            reasons.append(f"отсутствуют поля: {missing}")
        if extra:
            reasons.append(f"лишние поля: {extra}")
        return False, "; ".join(reasons)

    for key, expected_type in EXPECTED_STRUCTURE.items():
        value = data[key]
        if not isinstance(value, expected_type):
            return False, f"поле '{key}' имеет тип {type(value).__name__}, ожидался {expected_type}"

    try:
        datetime.strptime(data["date"], "%Y-%m-%d")
    except ValueError:
        return False, f"поле 'date' имеет неверный формат: {data['date']} (ожидается YYYY-MM-DD)"

    return True, ""

def load_processed_files() -> dict:
    """
    Читает лог и возвращает словарь {source_path: destination_path}.
    Если destination пустой (например, ошибка), он нам не нужен для проверки существования.
    """
    processed = {}
    if not LOG_FILE.exists():
        return processed

    with open(LOG_FILE, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            source = row.get('source_path', '').strip()
            dest = row.get('destination', '').strip()
            if source and dest and row.get('status') in ('SORTED', 'QUARANTINE'):
                processed[source] = dest
    return processed

def log_result(source_path: Path, status: str, destination: str, reason: str = ""):
    file_exists = LOG_FILE.exists()
    
    with open(LOG_FILE, 'a', encoding='utf-8', newline='') as f:
        fieldnames = ['timestamp', 'filename', 'source_path', 'status', 'destination', 'reason']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
            
        writer.writerow({
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'filename': source_path.name,
            'source_path': str(source_path),
            'status': status,
            'destination': destination,
            'reason': reason
        })

def process_files():
    SORTED_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)

    json_files = list(INCOMING_DIR.rglob("*.json"))
    
    if not json_files:
        with open(LOG_FILE, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "", "", "INFO", "", "В папке incoming не найдено json-файлов"])
        return

    processed_files = load_processed_files()

    success_count = 0
    quarantine_count = 0
    skipped_count = 0

    for file_path in json_files:
        source_path_str = str(file_path)
        
        if source_path_str in processed_files:
            destination_path = processed_files[source_path_str]
            if Path(destination_path).exists():
                skipped_count += 1
                continue

        if has_cyrillic_in_path(file_path):
            dest = get_unique_path(QUARANTINE_DIR, file_path.name)
            shutil.copy2(file_path, dest)
            log_result(file_path, "QUARANTINE", str(dest), "кириллица в пути")
            quarantine_count += 1
            continue
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except UnicodeDecodeError as e:
            dest = get_unique_path(QUARANTINE_DIR, file_path.name)
            shutil.copy2(file_path, dest)
            log_result(file_path, "QUARANTINE", str(dest), f"не UTF-8: {e}")
            quarantine_count += 1
            continue
        except json.JSONDecodeError as e:
            dest = get_unique_path(QUARANTINE_DIR, file_path.name)
            shutil.copy2(file_path, dest)
            log_result(file_path, "QUARANTINE", str(dest), f"Ошибка парсинга JSON: {e}")
            quarantine_count += 1
            continue
        except Exception as e:
            dest = get_unique_path(QUARANTINE_DIR, file_path.name)
            shutil.copy2(file_path, dest)
            log_result(file_path, "QUARANTINE", str(dest), f"Ошибка чтения: {e}")
            quarantine_count += 1
            continue

        is_valid, error_msg = validate_json(data)

        if is_valid:
            try:
                dt = datetime.strptime(data["date"], "%Y-%m-%d")
                year_month = dt.strftime("%Y-%m")
                doc_type = data["document_type"]
                
                target_dir = SORTED_DIR / year_month / doc_type
                target_dir.mkdir(parents=True, exist_ok=True)
                
                dest = get_unique_path(target_dir, file_path.name)
                shutil.copy2(file_path, dest)
                log_result(file_path, "SORTED", str(dest))
                success_count += 1
            except Exception as e:
                log_result(file_path, "ERROR", "", f"Ошибка при копировании: {e}")
        else:
            dest = get_unique_path(QUARANTINE_DIR, file_path.name)
            shutil.copy2(file_path, dest)
            log_result(file_path, "QUARANTINE", str(dest), error_msg)
            quarantine_count += 1

    with open(LOG_FILE, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "", "", "SUMMARY", "", 
                         f"Успешно: {success_count}, В карантин: {quarantine_count}, Пропущено (уже было): {skipped_count}"])

if __name__ == "__main__":
    process_files()