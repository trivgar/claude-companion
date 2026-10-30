"""Nothing of the original owner's life ships in this repo.

The terms come from a file OUTSIDE the repo (one per line, case-insensitive),
so the list itself never ships: ~/.config/claude-companion/leak-terms, or
CLAUDE_COMPANION_LEAK_TERMS. Without it only the generic patterns run: tailnet
addresses, *.ts.net names and real-looking email addresses.
"""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERMS_FILE = Path(os.environ.get("CLAUDE_COMPANION_LEAK_TERMS")
                  or Path.home() / ".config" / "claude-companion" / "leak-terms")
PATTERNS = [
    re.compile(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),   # tailnet (CGNAT) addresses
    re.compile(r"\b[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b", re.I),
    re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.(com|org)\b|anthropic\.com\b|users\.noreply\.github\.com\b)"
               r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]
SKIP = {"tests/test_leaks.py"}
TEXT = {".py", ".js", ".html", ".css", ".md", ".toml", ".json", ".sh", ".txt", ".tsv", ".service", ".timer", ".cfg", ".yml", ".yaml", "",
        ".java", ".kt", ".xml", ".gradle", ".properties", ".pro"}


def _terms() -> list[re.Pattern]:
    try:
        lines = TERMS_FILE.read_text().splitlines()
    except OSError:
        return []
    return [re.compile(r"(?<![A-Za-z0-9])" + re.escape(t.strip()) + r"(?![A-Za-z0-9])", re.I)
            for t in lines if t.strip() and not t.startswith("#")]


def _files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=root, capture_output=True, text=True).stdout.split()
    return [root / f for f in out if f not in SKIP and Path(f).suffix in TEXT]


def find_leaks(root: Path, terms: list[re.Pattern]) -> list[str]:
    hits = []
    for f in _files(root):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for p in terms + PATTERNS:
                m = p.search(line)
                if m:
                    hits.append(f"{f.relative_to(root)}:{n}: {m.group(0)!r}")
    return hits


def test_nothing_personal_ships():
    hits = find_leaks(ROOT, _terms())
    assert not hits, "personal data in the repo:\n" + "\n".join(hits[:50])


def test_the_check_catches_a_planted_leak(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("HOST = 'box.tail1234.ts.net'\nIP = '100.64.1.2'\nWHO = 'Zebediah'\n")
    hits = find_leaks(tmp_path, [re.compile(r"(?<![A-Za-z0-9])zebediah(?![A-Za-z0-9])", re.I)])
    assert len(hits) == 3, hits
