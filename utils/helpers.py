import os
import json

def get_data_dirs() -> dict[str, str]:
    """Returns absolute paths to input, output, and secure directories."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs = {
        "input": os.path.join(base_dir, "data", "input"),
        "output": os.path.join(base_dir, "data", "output"),
        "secure": os.path.join(base_dir, "data", "secure")
    }
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs

def load_json(filepath: str) -> dict | list | None:
    """Loads JSON data from file."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_json(data: dict | list, filepath: str) -> bool:
    """Saves data as formatted JSON."""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False
