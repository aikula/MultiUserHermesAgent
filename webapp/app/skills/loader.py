"""Skills library loader (spec 13: Hermes Skills Usage).

Loads the markdown skills in `webapp/app/skills/library/`, parses the required
sections, and exposes:

- `list_skills()` — compact list for the system prompt (name + one-line hint)
- `get_skill(name)` — full markdown content for one skill
- `extract_hint(markdown)` — pulls the first non-heading line as a routing hint

The skill file format (from spec 13):
```md
# Skill name

## When to use
...

## Inputs
...

## Output format
...

## Quality checklist
...

## Example prompt
...
```

`# Skill name` may be a human-readable title; the file's stem is the canonical
name (e.g. `meeting_followup.md` → name = `meeting_followup`).
"""
import re
from dataclasses import dataclass
from pathlib import Path

LIBRARY_DIR = Path(__file__).parent / "library"


@dataclass(frozen=True)
class SkillMeta:
    """Compact view of a skill for the system prompt and skills list."""
    name: str
    title: str
    hint: str  # one-line description (first paragraph of "When to use" or hint line)

    def to_dict(self) -> dict:
        return {"name": self.name, "title": self.title, "hint": self.hint}


def list_skills() -> list[SkillMeta]:
    """Return all skills sorted by name. Missing/unreadable files are skipped."""
    if not LIBRARY_DIR.exists():
        return []
    skills: list[SkillMeta] = []
    for path in sorted(LIBRARY_DIR.glob("*.md")):
        try:
            md = path.read_text(encoding="utf-8")
        except OSError:
            continue
        skills.append(_parse(path.stem, md))
    return skills


def get_skill(name: str) -> str | None:
    """Return full markdown for a skill by name, or None if not found.

    Names are matched against the file stem. Directory traversal is blocked
    by resolving the requested file under LIBRARY_DIR and confirming it
    stays inside.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    candidate = (LIBRARY_DIR / f"{name}.md").resolve()
    try:
        candidate.relative_to(LIBRARY_DIR.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return None


def render_compact_list() -> str:
    """Render the skills library as a compact block for the system prompt.

    One line per skill: `- meeting_followup — Meeting Follow-up: ...`
    """
    skills = list_skills()
    if not skills:
        return ""
    lines = ["## Доступные навыки (используй, если подходит по смыслу)",
             "Когда юзер явно выбирает навык через UI, его полный текст будет добавлен отдельно — не дублируй."]
    for s in skills:
        lines.append(f"- `{s.name}` — {s.title}: {s.hint}")
    return "\n".join(lines)


# --- parsing ---

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2_WHEN_RE = re.compile(
    r"^##\s+When to use\s*\n(.*?)(?=^##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _parse(name: str, markdown: str) -> SkillMeta:
    """Extract (name, title, hint) from a skill markdown body."""
    title = name.replace("_", " ").strip().title()
    m = _H1_RE.search(markdown)
    if m:
        title = m.group(1).strip()
    hint = ""
    m2 = _H2_WHEN_RE.search(markdown)
    if m2:
        first_para = m2.group(1).strip().split("\n\n", 1)[0]
        # Take the first non-empty line, capped at 200 chars
        for line in first_para.splitlines():
            line = line.strip()
            if line:
                hint = line[:200].rstrip()
                if len(line) > 200:
                    hint += "…"
                break
    return SkillMeta(name=name, title=title, hint=hint)
