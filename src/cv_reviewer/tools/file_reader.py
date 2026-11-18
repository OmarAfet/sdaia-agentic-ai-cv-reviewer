"""File reader tool: extracts CV text from PDF, DOCX or TXT.

It separates visible text from hidden text. Hidden text is text a human
reviewer cannot see: white or near-white fill, font size under 3 pt, PDF text
render mode 3 (invisible), Word "hidden" runs, and zero-width characters.
Candidates use hidden text to plant instructions for AI screeners, so the
agents receive only the visible text and the hidden text goes to the
injection scanner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ZERO_WIDTH = re.compile("[​‌‍⁠﻿‪-‮⁦-⁩]")
MIN_VISIBLE_PT = 3.0


@dataclass
class ExtractedDocument:
    visible_text: str
    hidden_text: str = ""
    zero_width_chars: int = 0
    pages: int = 0
    notes: list[str] = field(default_factory=list)


def _is_white(op: bytes, args: list[float]) -> bool:
    if op in (b"rg", b"RG") and len(args) == 3:
        return min(args) >= 0.95
    if op in (b"g", b"G") and len(args) == 1:
        return args[0] >= 0.95
    if op in (b"k", b"K") and len(args) == 4:
        return max(args) <= 0.05
    return False


def _read_pdf(path: Path) -> ExtractedDocument:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    visible: list[str] = []
    hidden: list[str] = []
    for page in reader.pages:
        # Graphics state that decides visibility. q/Q save and restore it.
        state = {"white": False, "invisible": False}
        stack: list[dict] = []

        def before(op, args, cm, tm, state=state, stack=stack):
            if op == b"q":
                stack.append(dict(state))
            elif op == b"Q" and stack:
                state.update(stack.pop())
            elif op in (b"rg", b"g", b"k"):
                try:
                    state["white"] = _is_white(op, [float(a) for a in args])
                except (TypeError, ValueError):
                    pass
            elif op == b"Tr" and args:
                state["invisible"] = int(args[0]) == 3

        def on_text(text, cm, tm, font_dict, font_size, state=state):
            if not text:
                return
            if text.strip() == "":
                visible.append(text)
                return
            scale = abs(tm[0] * cm[0]) if tm and cm else 1.0
            effective_pt = (font_size or 0) * (scale or 1.0)
            if state["white"] or state["invisible"] or effective_pt < MIN_VISIBLE_PT:
                hidden.append(text)
                visible.append("\n")
            else:
                visible.append(text)

        page.extract_text(visitor_operand_before=before, visitor_text=on_text)
        visible.append("\n")
    return ExtractedDocument("".join(visible), "\n".join(hidden), pages=len(reader.pages))


def _read_docx(path: Path) -> ExtractedDocument:
    import docx
    from docx.shared import Pt

    document = docx.Document(str(path))
    paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)

    visible_lines: list[str] = []
    hidden: list[str] = []
    for para in paragraphs:
        line: list[str] = []
        for run in para.runs:
            color = run.font.color.rgb if run.font.color is not None and run.font.color.type else None
            is_hidden = bool(run.font.hidden) or (
                color is not None and str(color).upper() in {"FFFFFF", "FEFEFE", "FDFDFD"}
            ) or (run.font.size is not None and run.font.size < Pt(MIN_VISIBLE_PT))
            (hidden if is_hidden else line).append(run.text)
        visible_lines.append("".join(line))
    return ExtractedDocument("\n".join(visible_lines), "\n".join(h for h in hidden if h.strip()))


def read_cv(path: str | Path) -> ExtractedDocument:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        doc = _read_pdf(path)
    elif suffix == ".docx":
        doc = _read_docx(path)
    elif suffix == ".txt":
        doc = ExtractedDocument(path.read_text(encoding="utf-8", errors="replace"))
    else:
        raise ValueError(f"unsupported file type: {suffix}")

    doc.zero_width_chars = len(ZERO_WIDTH.findall(doc.visible_text))
    doc.visible_text = ZERO_WIDTH.sub("", doc.visible_text)
    doc.visible_text = re.sub(r"[ \t]+\n", "\n", doc.visible_text)
    doc.visible_text = re.sub(r"\n{3,}", "\n\n", doc.visible_text).strip()
    return doc
