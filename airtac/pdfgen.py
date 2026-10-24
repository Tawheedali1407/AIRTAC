"""PDF building in two steps, so the content can be tested without reading PDF bytes:

1. A *builder* (solutions_sections, study_sections, notes_sections) turns data into a
   plain list of sections like ("heading", "Two Sum") or ("code", "def f(): ...").
2. render() turns that list into an A4 PDF with fpdf2.

Fonts: DejaVu (assets/fonts) is used when present, so symbols like ≤ × → print.
Without it we fall back to the built-in fonts and swap unsupported characters for
plain ones. Either way, a strange character must never crash a PDF.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import MethodReturnValue

from airtac import config

log = logging.getLogger("airtac.pdf")

SOLUTIONS_FOOTER = "AI-generated solutions. Test before submitting. For practice, not for live contests."
STUDY_FOOTER = "AI-generated study material. Check it against your textbook and notes."
NOTES_FOOTER = "Made by AIRTAC."

MAX_IMAGES_PER_PDF = 5
SITE_NAMES = {"leetcode": "LeetCode", "codechef": "CodeChef", "geeksforgeeks": "GeeksforGeeks"}


def site_name(site: str) -> str:
    return SITE_NAMES.get(site, (site or "").title())

# Used only with the built-in fonts, which cover Latin-1 and nothing else.
_PLAIN = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "•": "-", "…": "...",
          "≤": "<=", "≥": ">=", "≠": "!=", "×": "x", "÷": "/", "→": "->", "←": "<-", "↔": "<->",
          "∞": "inf", "√": "sqrt", "π": "pi", "∑": "sum", "≈": "~", "±": "+/-", "₹": "Rs ",
          " ": " ", "\t": "    "}


def to_latin1(text: str) -> str:
    for a, b in _PLAIN.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


# ---------------------------------------------------------------- builders

def _problem_block(p: dict, mode: str) -> list[tuple]:
    sections = [("meta", f"{site_name(p.get('site', ''))} · {p.get('difficulty') or 'difficulty not set'}"),
                ("heading", p.get("title") or "Untitled problem")]
    if p.get("url"):
        sections.append(("meta", p["url"]))
    if p.get("tags"):
        sections.append(("meta", f"Tags: {p['tags']}"))
    # Shareable mode never includes the statement: problem statements belong to their sites.
    if mode == "personal" and p.get("statement"):
        sections += [("label", "Question"), ("text", p["statement"])]
    elif p.get("summary"):
        sections += [("label", "Summary (my own words)"), ("text", p["summary"])]
    if p.get("approach"):
        sections += [("label", "Approach"), ("text", p["approach"])]
    steps = _steps(p.get("steps_json"))
    if steps:
        sections.append(("label", "Step by step"))
        for n, s in enumerate(steps.get("steps", []), 1):
            body = s.get("explanation", "")
            if s.get("small_example"):
                body += f"\nExample: {s['small_example']}"
            sections.append(("step", f"{n}. {s.get('title', '')}", body))
        if steps.get("edge_cases"):
            sections.append(("text", "Edge cases: " + "; ".join(map(str, steps["edge_cases"]))))
    if p.get("code"):
        sections += [("label", f"Solution ({p.get('language') or 'code'})"), ("code", p["code"])]
    if p.get("complexity"):
        sections += [("label", "Complexity"), ("text", p["complexity"])]
    sections.append(("rule",))
    return sections


def _steps(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def solutions_sections(problems: list[dict], mode: str = "personal") -> list[tuple]:
    sites = sorted({p.get("site", "") for p in problems})
    sections = [("title", "Solutions"),
                ("meta", f"Sites: {', '.join(site_name(s) for s in sites) or 'none'}"),
                ("meta", f"Date: {datetime.now():%d %b %Y}"),
                ("meta", f"Problems: {len(problems)}"),
                ("meta", "Mode: personal (includes statements, keep private)" if mode == "personal"
                 else "Mode: shareable (no problem statements)"),
                ("pagebreak",)]
    for site in sites:
        sections.append(("site", site_name(site)))
        for p in problems:
            if p.get("site", "") == site:
                sections += _problem_block(p, mode)
    return sections


def study_sections(subject: str, unit: str, questions: list[dict], mode: str = "questions_answers",
                   images: list[Path] | None = None) -> list[tuple]:
    title = f"{subject}" + (f" · {unit}" if unit else "")
    sections = [("title", title), ("meta", f"Study questions · {datetime.now():%d %b %Y}"),
                ("meta", f"{len(questions)} questions · " +
                 ("questions with answers" if mode == "questions_answers" else "answers only")),
                ("rule",)]
    for n, q in enumerate(questions, 1):
        if mode == "questions_answers":
            sections.append(("heading", f"Q{n}. {q.get('question', '')}"))
            for opt in _options(q.get("options")):
                sections.append(("text", f"   {opt}"))
            sections.append(("text", f"Answer: {q.get('answer', '')}"))
            if q.get("explanation"):
                sections.append(("text", f"Why: {q['explanation']}"))
            if q.get("topic"):
                sections.append(("meta", f"Topic: {q['topic']}"))
        else:
            sections.append(("text", f"{n}. {q.get('answer', '')}"))
    for path in (images or [])[:MAX_IMAGES_PER_PDF]:
        # The caption travels with the image so it never ends up alone at the bottom of a page.
        sections.append(("image", str(path), "Figure (AI-generated or drawn by AIRTAC)"))
    return sections


def _options(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(o) for o in raw]
    try:
        data = json.loads(raw)
        return [str(o) for o in data] if isinstance(data, list) else [str(raw)]
    except (TypeError, ValueError):
        return [str(raw)]


def notes_sections(title: str, body: str) -> list[tuple]:
    return [("title", title), ("meta", f"Made by AIRTAC on {datetime.now():%d %b %Y}"), ("text", body)]


# ---------------------------------------------------------------- renderer

class _PDF(FPDF):
    footer_text = ""
    unicode_ok = False

    def footer(self):
        self.set_y(-12)
        self.set_font(self.sans, size=8)
        self.set_text_color(110)
        text = f"{self.footer_text}   Page {self.page_no()}"
        self.cell(0, 6, text if self.unicode_ok else to_latin1(text), align="C")
        self.set_text_color(0)


def _setup_fonts(pdf: _PDF) -> None:
    fonts = config.FONTS_DIR
    regular, bold, mono = fonts / "DejaVuSans.ttf", fonts / "DejaVuSans-Bold.ttf", fonts / "DejaVuSansMono.ttf"
    if regular.exists() and mono.exists():
        try:
            pdf.add_font("DejaVu", "", str(regular))
            pdf.add_font("DejaVu", "B", str(bold if bold.exists() else regular))
            pdf.add_font("DejaVuMono", "", str(mono))
            pdf.sans, pdf.mono, pdf.unicode_ok = "DejaVu", "DejaVuMono", True
            return
        except Exception:  # a broken font file must not stop the PDF
            log.warning("could not load DejaVu fonts, using built-in fonts")
    pdf.sans, pdf.mono, pdf.unicode_ok = "Helvetica", "Courier", False


def _fits_on_page(pdf: _PDF, height: float) -> bool:
    return pdf.get_y() + height <= pdf.h - pdf.b_margin


def render(sections: list[tuple], path: Path, footer: str = NOTES_FOOTER) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = _PDF(format="A4")
    pdf.footer_text = footer
    _setup_fonts(pdf)
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    txt = (lambda s: str(s)) if pdf.unicode_ok else (lambda s: to_latin1(str(s)))

    def write(text, size=11, style="", font=None, h=6, color=0):
        pdf.set_font(font or pdf.sans, style, size)
        pdf.set_text_color(color)
        pdf.multi_cell(0, h, txt(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0)

    images = 0
    for section in sections:
        kind = section[0]
        if kind == "title":
            write(section[1], size=20, style="B", h=10)
            pdf.ln(2)
        elif kind == "site":
            if pdf.get_y() > pdf.t_margin + 5:
                pdf.add_page()
            write(section[1], size=16, style="B", h=9, color=40)
            pdf.ln(2)
        elif kind == "heading":
            # Keep a heading with at least a few lines of what follows it.
            if not _fits_on_page(pdf, 30):
                pdf.add_page()
            write(section[1], size=13, style="B", h=7)
        elif kind == "label":
            pdf.ln(1)
            write(section[1], size=10, style="B", h=6, color=60)
        elif kind == "meta":
            write(section[1], size=9, h=5, color=110)
        elif kind == "text":
            write(section[1], size=11, h=6)
        elif kind == "step":
            write(section[1], size=11, style="B", h=6)
            write(section[2], size=11, h=6)
        elif kind == "code":
            _code_block(pdf, txt(section[1]))
        elif kind == "image":
            if images < MAX_IMAGES_PER_PDF and Path(section[1]).exists():
                images += 1
                if not _fits_on_page(pdf, 100):
                    pdf.add_page()
                if len(section) > 2:
                    write(section[2], size=10, style="B", h=6, color=60)
                try:
                    pdf.image(section[1], w=120)
                except Exception:
                    write("(image could not be added)", size=9, color=110)
                pdf.ln(2)
        elif kind == "rule":
            pdf.ln(2)
            pdf.set_draw_color(200)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(4)
        elif kind == "pagebreak":
            pdf.add_page()
    pdf.output(str(path))
    return path


def _code_block(pdf: _PDF, code: str) -> None:
    """Monospace code on a light background. Long lines wrap inside the page.
    A short block that doesn't fit at the bottom moves to the next page instead of splitting."""
    pdf.set_font(pdf.mono, size=9)
    pdf.set_fill_color(244, 245, 247)
    line_h = 4.6
    # fpdf2 keeps leading spaces, so indentation survives; tabs become 4 spaces.
    text = code.replace("\t", "    ") or " "
    wrapped = pdf.multi_cell(0, line_h, text, dry_run=True, output=MethodReturnValue.LINES)
    height = len(wrapped) * line_h
    page_body = pdf.h - pdf.t_margin - pdf.b_margin
    if height < page_body / 2 and not _fits_on_page(pdf, height):
        pdf.add_page()
        pdf.set_font(pdf.mono, size=9)
    pdf.multi_cell(0, line_h, text, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
