"""Extract project sections from cleaned resume text."""

import re
from typing import Optional


_PROJECT_HEADINGS = {"项目经历", "项目经验", "projects", "project experience", "project highlights"}
_END_HEADINGS = {
    "教育背景", "教育经历", "education",
    "专业技能", "技能", "skills",
    "工作经历", "实习经历", "work experience",
    "自我评价", "获奖情况", "证书", "awards",
}
_HEADING = re.compile(r"(?:[0-9]+[.、]\s*|\([0-9]+\)\s*)?([^:：]+?)\s*[:：]?")


def extract_project_snippet(text: Optional[str]) -> str:
    """Return the original project-section bodies in document order."""
    if not text:
        return ""

    sections = []
    start = None
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _HEADING.fullmatch(line.strip())
        heading = match.group(1).casefold() if match else ""
        is_project = heading in _PROJECT_HEADINGS
        if is_project or heading in _END_HEADINGS:
            if start is not None:
                sections.append(text[start:offset].strip())
            start = offset + len(line) if is_project else None
        offset += len(line)

    if start is not None:
        sections.append(text[start:].strip())
    return "\n".join(section for section in sections if section)
