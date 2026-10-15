import json
from pathlib import Path


def writeCache(fileName, fileContent):
    out_path = Path(__file__).resolve().parents[2] / f"cache_{fileName}.json"
    out_path.write_text(
        json.dumps(fileContent, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
