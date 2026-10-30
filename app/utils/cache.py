import json
from pathlib import Path


def write_cache(file_name, file_content):
    out_path = Path(__file__).resolve().parents[2] / f"cache_{file_name}.json"
    out_path.write_text(
        json.dumps(file_content, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
