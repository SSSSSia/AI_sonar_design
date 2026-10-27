"""Sync/check canonical Mermaid blocks in current reference Markdown; never edit archive.

Run: python scripts/reference_diagrams.py [--check]
Re-render SVG exports with mmdc after changing sources (see reference/README.md).
"""
from pathlib import Path
import argparse
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "docs" / "reference"
PATTERN = re.compile(r"(<!-- diagram: (D\d{2}) -->\n)```mermaid\n.*?\n```", re.DOTALL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    sources = {p.stem: p for p in (REF / "diagrams").glob("D*.mmd")}
    errors, count, used = [], 0, set()
    if not sources:
        print("FAIL: no canonical sources found")
        return 1
    for path in sorted(REF.glob("*.md")):
        text = path.read_text(encoding="utf-8")

        def replace(match):
            nonlocal count
            count += 1
            key = match[2]
            used.add(key)
            if key not in sources:
                errors.append(f"{path.name}: unknown diagram {key}")
                return match[0]
            source = sources[key].read_text(encoding="utf-8").strip()
            expected = match[1] + "```mermaid\n" + source + "\n```"
            if args.check and expected != match[0]:
                errors.append(f"{path.name}: {key} differs from canonical source")
            return expected

        revised = PATTERN.sub(replace, text)
        blocks = len(re.findall(r"^```mermaid$", text, re.MULTILINE))
        marked = len(list(PATTERN.finditer(text)))
        if blocks != marked:
            errors.append(f"{path.name}: {blocks - marked} unmarked Mermaid blocks")
        if not args.check and revised != text:
            path.write_text(revised, encoding="utf-8", newline="\n")
    for key in sources.keys() - used:
        errors.append(f"{key}: unused canonical source")
    print(f"Canonical sources: {len(sources)}; document occurrences: {count}")
    for error in errors:
        print(error)
    if errors:
        return 1
    print("PASS: all current Mermaid diagrams match their canonical sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())