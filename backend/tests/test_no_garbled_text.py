"""No garbled characters anywhere in the app's source - text like "â‚¹" (a ₹
saved through the wrong encoding) would show on screen as-is."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "frontend" / "src", ROOT / "frontend" / "index.html", ROOT / "backend" / "app"]
GARBLED = re.compile("â‚|â€|â†|âœ|âˆ|Â[·°±×]|Ã[—\u0080-¿]|ðŸ")


def test_source_text_is_not_garbled():
    bad = []
    for base in SOURCES:
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.suffix in {".ts", ".tsx", ".css", ".html", ".py", ".json"}]
        for path in files:
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if GARBLED.search(line):
                    bad.append(f"{path.relative_to(ROOT)}:{no}: {line.strip()[:80]}")
    assert not bad, "Garbled characters:\n" + "\n".join(bad)
