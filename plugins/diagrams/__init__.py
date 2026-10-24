"""Diagrams: exact pictures of arrays, two pointers, trees, graphs, stacks, queues and linked lists,
drawn with Pillow. Why not an AI image model: they draw text, numbers and counts badly, and an
exam diagram must be exact.

Illustrations (style "illustration": a picture for slides or a poster, not an exam diagram) use an
open image model, FLUX.1 schnell (Apache 2.0), on Nebius Token Factory when NEBIUS_API_KEY is set,
as in the hackathon version. They are labelled AI-generated, capped per day, and never used for diagrams.
Pictures are saved to outputs/diagrams/ and shown under the answer.
"""
import base64
import io
import math
import re
from datetime import date, datetime

from PIL import Image, ImageDraw, ImageFont

from airtac import config
from airtac.plugin_api import Tool, ToolResult, after, fetch, plugin, schema, unreachable

p = plugin(__name__)

BG, FG, ACCENT, MUTED = (255, 255, 255), (30, 33, 38), (40, 110, 220), (120, 126, 135)


def _font(size: int):
    path = config.FONTS_DIR / "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def _numbers(prompt: str, default: list[int]) -> list[int]:
    nums = [int(n) for n in re.findall(r"-?\d+", prompt)][:12]
    return nums if len(nums) >= 2 else default


def _save(img: Image.Image, prefix: str) -> str:
    name = f"{prefix}-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
    img.save(p.output_dir() / name, "PNG", optimize=True)
    return name


def _canvas(w: int, h: int, title: str):
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    d.text((20, 14), title, fill=FG, font=_font(22))
    d.text((20, h - 26), "Drawn by AIRTAC (exact diagram, not AI-generated)", fill=MUTED, font=_font(13))
    return img, d


def _box(d, x, y, w, h, text, fill=(240, 244, 250), outline=FG):
    d.rectangle([x, y, x + w, y + h], fill=fill, outline=outline, width=2)
    d.text((x + w / 2, y + h / 2), str(text), fill=FG, font=_font(20), anchor="mm")


def _arrow(d, x1, y1, x2, y2, color=ACCENT):
    d.line([x1, y1, x2, y2], fill=color, width=3)
    angle = math.atan2(y2 - y1, x2 - x1)
    for spread in (0.45, -0.45):  # two short lines make the arrow head
        d.line([x2, y2, x2 - 12 * math.cos(angle + spread), y2 - 12 * math.sin(angle + spread)], fill=color, width=3)


def draw_array(prompt: str) -> Image.Image:
    values = _numbers(prompt, [1, 3, 4, 6, 8, 11, 15])
    two_pointers = "pointer" in prompt.lower()
    cell = 70
    w = max(520, 60 + cell * len(values))
    img, d = _canvas(w, 300, "Two pointers" if two_pointers else "Array")
    x0, y0 = 30, 110
    for i, v in enumerate(values):
        _box(d, x0 + i * cell, y0, cell, cell, v)
        d.text((x0 + i * cell + cell / 2, y0 + cell + 14), str(i), fill=MUTED, font=_font(14), anchor="mm")
    if two_pointers:
        for i, label in ((0, "L"), (len(values) - 1, "R")):
            cx = x0 + i * cell + cell / 2
            d.text((cx, 62), label, fill=ACCENT, font=_font(22), anchor="mm")
            _arrow(d, cx, 76, cx, y0 - 6)
    return img


def draw_tree(prompt: str) -> Image.Image:
    values = _numbers(prompt, [8, 4, 12, 2, 6, 10, 14])[:15]  # level order, like a heap array
    depth = max(1, math.ceil(math.log2(len(values) + 1)))
    w, h = 640, 140 + depth * 90
    img, d = _canvas(w, h, "Binary tree (level order)")
    pos = {}
    for i, v in enumerate(values):
        level = int(math.log2(i + 1))
        index_in_level = i - (2 ** level - 1)
        slots = 2 ** level
        pos[i] = (w * (index_in_level + 0.5) / slots, 80 + level * 90)
    for i in pos:
        for child in (2 * i + 1, 2 * i + 2):
            if child in pos:
                d.line([pos[i], pos[child]], fill=FG, width=2)
    for i, (x, y) in pos.items():
        d.ellipse([x - 24, y - 24, x + 24, y + 24], fill=(240, 244, 250), outline=FG, width=2)
        d.text((x, y), str(values[i]), fill=FG, font=_font(18), anchor="mm")
    return img


def draw_graph(prompt: str) -> Image.Image:
    nodes = ["A", "B", "C", "D", "E", "F"]
    edges = [(0, 1), (0, 2), (1, 3), (2, 3), (3, 4), (4, 5), (2, 5)]
    img, d = _canvas(560, 440, "Graph")
    cx, cy, r = 280, 225, 140
    pos = [(cx + r * math.cos(2 * math.pi * i / len(nodes) - math.pi / 2),
            cy + r * math.sin(2 * math.pi * i / len(nodes) - math.pi / 2)) for i in range(len(nodes))]
    for a, b in edges:
        d.line([pos[a], pos[b]], fill=FG, width=2)
    for (x, y), name in zip(pos, nodes):
        d.ellipse([x - 24, y - 24, x + 24, y + 24], fill=(240, 244, 250), outline=FG, width=2)
        d.text((x, y), name, fill=FG, font=_font(18), anchor="mm")
    return img


def draw_linear(prompt: str) -> Image.Image:
    low = prompt.lower()
    values = _numbers(prompt, [5, 9, 2, 7])
    if "stack" in low:
        img, d = _canvas(360, 140 + 60 * len(values), "Stack (top first)")
        for i, v in enumerate(reversed(values)):
            _box(d, 110, 70 + i * 60, 140, 56, v)
        d.text((265, 98), "← top", fill=ACCENT, font=_font(18), anchor="lm")
        return img
    if "queue" in low:
        img, d = _canvas(max(480, 120 + 80 * len(values)), 260, "Queue")
        for i, v in enumerate(values):
            _box(d, 60 + i * 80, 110, 76, 60, v)
        d.text((98, 90), "front", fill=ACCENT, font=_font(16), anchor="mm")
        d.text((60 + (len(values) - 1) * 80 + 38, 90), "rear", fill=ACCENT, font=_font(16), anchor="mm")
        return img
    img, d = _canvas(max(520, 80 + 110 * len(values)), 240, "Linked list")
    for i, v in enumerate(values):
        x = 30 + i * 110
        _box(d, x, 100, 70, 56, v)
        _arrow(d, x + 70, 128, x + 106, 128)
    d.text((30 + len(values) * 110, 128), "None", fill=MUTED, font=_font(16), anchor="lm")
    return img


def draw_diagram(prompt: str) -> tuple[Image.Image, str]:
    low = prompt.lower()
    if "tree" in low or "bst" in low or "heap" in low:
        return draw_tree(prompt), "tree"
    if "graph" in low or "bfs" in low or "dfs" in low:
        return draw_graph(prompt), "graph"
    if any(k in low for k in ("stack", "queue", "linked list")):
        return draw_linear(prompt), "list"
    return draw_array(prompt), "array"


def _result(name: str, spoken: str, note: str) -> ToolResult:
    # The "Image:" line tells the page to show a preview.
    return ToolResult(spoken, f"{note}\nImage: /files/diagrams/{name}\nSaved to outputs/diagrams/{name}")


def _diagram(prompt: str) -> ToolResult:
    img, kind = draw_diagram(prompt)
    name = _save(img, f"diagram-{kind}")
    article = "an" if kind[0] in "aeiou" else "a"
    return _result(name, f"I drew {article} {kind} diagram. It's on screen.", "Diagram drawn by AIRTAC with Pillow (exact, not AI).")


def make_image(prompt: str, style: str = "diagram") -> ToolResult:
    """Offline, or no Nebius key: exact diagrams only."""
    if style == "illustration":
        return ToolResult("I can draw exact diagrams here: arrays, trees, graphs, stacks, queues and lists. "
                          "AI illustrations need a Nebius key in your settings and the internet.",
                          "AI illustrations use FLUX.1 schnell on Nebius Token Factory. Add NEBIUS_API_KEY to .env to switch them on.")
    return _diagram(prompt)


MAX_SIDE, MAX_PNG_BYTES = 1024, 2_500_000


def ai_images_today() -> int:
    folder = p.output_dir()
    return sum(1 for f in folder.glob("ai-*.png") if date.fromtimestamp(f.stat().st_mtime) == date.today())


def make_image_live(prompt: str, style: str = "diagram") -> ToolResult:
    if style != "illustration":
        return _diagram(prompt)  # exact diagrams never go to the AI model
    limit = int(p.setting("images_per_day", 10))
    if ai_images_today() >= limit:
        return ToolResult(f"I've already made {limit} AI images today. That's your daily limit.")
    model = p.setting("image_model", "black-forest-labs/flux-schnell")
    data = fetch(config.NEBIUS_BASE_URL + "/images/generations", method="POST", write=True, timeout=60,
                 headers={"Authorization": f"Bearer {p.secret('NEBIUS_API_KEY')}"},
                 json_body={"model": model, "prompt": prompt[:500], "response_format": "b64_json"})
    try:
        raw = base64.b64decode(data["data"][0]["b64_json"])
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:  # no answer, or not an image
        return unreachable("the image model")
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    name = _save(img, "ai")
    if (p.output_dir() / name).stat().st_size > MAX_PNG_BYTES:
        (p.output_dir() / name).unlink()
        return ToolResult("The image was too large to keep, so I deleted it.", ok=False)
    return _result(name, "Here's your picture. It's AI-generated, so check any words or numbers in it.",
                   f"AI-generated image ({model}). Prompt: {prompt[:200]}")


p.add(Tool("make_image", "Draw an exact data-structure diagram (array, two pointers, tree, graph, stack, queue, linked list), "
           "or with style illustration an AI picture for slides or posters.",
           schema({"prompt": {"type": "string", "description": "What to draw, e.g. 'two pointers on 1 3 5 7'"},
                   "style": {"type": "string", "enum": ["diagram", "illustration"]}}, required=("prompt",)),
           "local", make_image, make_image_live, needs=("NEBIUS_API_KEY",)))


@p.dashboard
def page(pg: dict) -> None:
    images = sorted(p.output_dir().glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
    pg["stats"] = [{"label": "Diagrams drawn", "value": len(images)}]
    pg["lists"] = [{"title": "Latest", "empty": "No diagrams yet. Ask for one, like: draw a binary tree 8 4 12.",
                    "items": [{"title": f.name, "image": f"/files/diagrams/{f.name}"} for f in images[:8]]}]


def _image_args(text, _messages):
    low = text.lower()
    exact = any(k in low for k in ("diagram", "array", "tree", "graph", "pointer", "stack", "queue", "linked list"))
    prompt = after(text, r"\b(draw|make|create|generate)\b(\s+(me|an?|the))*(\s+(diagram|image|picture|illustration)\s+of)?")
    return {"prompt": prompt or text, "style": "diagram" if exact else "illustration"}


p.mock_rule(10, ("draw", "diagram", "illustration", "picture"), "make_image", _image_args)
