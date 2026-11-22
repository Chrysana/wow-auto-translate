# SPDX-License-Identifier: GPL-3.0-or-later
"""
WoW Auto Translate: reads the WoW chat off the screen and translates it to English.

1. Run:  python wow_translate.py
2. Click "Select area" and drag a box over the WoW chat window.
3. Anything non-English that appears in that box is translated into this window.

Keep this window *outside* the box you selected (otherwise it reads itself).
WoW should be in "Windowed (Fullscreen)" mode so this window can stay on top.
Right-click the text area for more options.
"""

import ctypes
import difflib
import json
import os
import queue
import re
import statistics
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

import mss
from PIL import Image, ImageOps

# Make screen coords match real pixels on scaled (125%/150%) displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

__version__ = "0.9.0"

if getattr(sys, "frozen", False):
    # Windowed .exe has no console; give libraries that log/print somewhere harmless to write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    # Packaged .exe: the install folder may not be writable, so keep settings in %APPDATA%.
    CONFIG = Path(os.environ.get("APPDATA", Path.home())) / "WoWAutoTranslate" / "config.json"
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
else:
    CONFIG = Path(__file__).with_name("config.json")
TARGET_LANG = "en"
SCAN_SECONDS = 1.0
MIN_CONFIDENCE = 0.93  # RapidOCR line score; real chat lines ~0.96+, garbage ~0.9 or less


# --- OCR --------------------------------------------------------------------

# Extra scripts the main model can't read. Russian is the tricky one: Cyrillic letters look like
# Latin ones ("Все за" -> "Bce 3a"), so the main model is confidently wrong. We re-read each line
# with these models and switch to their reading when it is mostly in their script.
EXTRA_SCRIPTS = {
    "Russian": ("eslav", re.compile(r"[Ѐ-ӿ]"), 0.3),
    "Korean": ("korean", re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]"), 0.15),
}


def script_ratio(text: str, pattern: re.Pattern) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(bool(pattern.match(c)) for c in letters) / len(letters) if letters else 0.0


class RapidEngine:
    """PaddleOCR models via onnxruntime.
    Main model (PP-OCRv6 multilingual): English, Spanish, Portuguese, French, German, Italian,
    Chinese (simplified + traditional), Japanese, and ~40 more Latin-alphabet languages.
    Extra models: Russian/Cyrillic, Korean."""

    name = "RapidOCR"

    def __init__(self, extras=tuple(EXTRA_SCRIPTS)):
        import logging

        import numpy as np
        from rapidocr import RapidOCR
        from rapidocr.ch_ppocr_rec.typings import TextRecInput
        from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        self.np, self.TextRecInput = np, TextRecInput
        # Default detector (PP-OCRv6 small) finds whole lines; the v5 mobile one splits words.
        self.det = RapidOCR()
        self.main_rec = RapidOCR(params={
            "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.lang_type": LangRec.CH,
            "Rec.model_type": ModelType.SMALL,
        })._load_rec_model()
        self.extra_recs = {}
        for name in extras:
            lang, pattern, ratio = EXTRA_SCRIPTS[name]
            rec = RapidOCR(params={
                "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.lang_type": LangRec(lang),
                "Rec.model_type": ModelType.MOBILE,
            })._load_rec_model()
            self.extra_recs[name] = (rec, pattern, ratio)

    def recognize(self, rec, crops):
        out = rec(self.TextRecInput(img=crops))
        return list(zip(out.txts, out.scores))

    def read(self, img: Image.Image) -> list["Line"]:
        arr = self.np.array(img.convert("RGB"))
        res = self.det(arr, use_det=True, use_cls=False, use_rec=False)
        if res.boxes is None or len(res.boxes) == 0:
            return []
        # Merge boxes on the same row (icons can split a line in two).
        boxes = sorted(
            ((b[:, 1].min(), b[:, 1].max(), b[:, 0].min(), b[:, 0].max()) for b in res.boxes),
            key=lambda b: (b[0] + b[1]) / 2,
        )
        rows: list[list[float]] = []
        for y0, y1, x0, x1 in boxes:
            if rows and abs((y0 + y1) / 2 - (rows[-1][0] + rows[-1][1]) / 2) < (y1 - y0) / 2:
                r = rows[-1]
                rows[-1] = [min(r[0], y0), max(r[1], y1), min(r[2], x0), max(r[3], x1)]
            else:
                rows.append([y0, y1, x0, x1])
        H, W = arr.shape[:2]
        crops = [
            arr[max(0, int(y0) - 2):min(H, int(y1) + 3), max(0, int(x0) - 2):min(W, int(x1) + 3)]
            for y0, y1, x0, x1 in rows
        ]
        results = self.recognize(self.main_rec, crops)
        for rec, pattern, min_ratio in self.extra_recs.values():
            for i, (txt, score) in enumerate(self.recognize(rec, crops)):
                if score >= 0.8 and script_ratio(txt, pattern) >= min_ratio:
                    results[i] = (txt, score)
        # A row touching the top/bottom edge of the box is probably cut in half.
        return [
            Line(x=x0, h=y1 - y0, text=txt,
                 ok=score >= MIN_CONFIDENCE and y0 > 1 and y1 < H - 2,
                 # sample colours from the middle band only, so neighbouring lines don't bleed in
                 colors=line_colors(arr[int(y0 + (y1 - y0) * 0.25):int(y1 - (y1 - y0) * 0.25) + 1,
                                        int(x0):int(x1) + 1], self.np))
            for (y0, y1, x0, x1), (txt, score) in zip(rows, results)
        ]


# --- name colours -------------------------------------------------------------

CLASS_COLORS = {
    "Death Knight": (0xC4, 0x1E, 0x3A), "Demon Hunter": (0xA3, 0x30, 0xC9),
    "Druid": (0xFF, 0x7C, 0x0A), "Evoker": (0x33, 0x93, 0x7F), "Hunter": (0xAA, 0xD3, 0x72),
    "Mage": (0x3F, 0xC7, 0xEB), "Monk": (0x00, 0xFF, 0x98), "Paladin": (0xF4, 0x8C, 0xBA),
    "Priest": (0xFF, 0xFF, 0xFF), "Rogue": (0xFF, 0xF4, 0x68), "Shaman": (0x00, 0x70, 0xDD),
    "Warlock": (0x87, 0x88, 0xEE), "Warrior": (0xC6, 0x9B, 0x6D),
}
# Default chat channel colours, so a line's own colour snaps cleanly too.
CHANNEL_COLORS = {
    "Raid": (0xFF, 0x7F, 0x00), "Raid Leader": (0xFF, 0x48, 0x09), "Raid Warning": (0xFF, 0x48, 0x00),
    "Party": (0xAA, 0xAA, 0xFF), "Guild": (0x40, 0xFF, 0x40), "Officer": (0x40, 0xC0, 0x40),
    "Say": (0xFF, 0xFF, 0xFF), "Yell": (0xFF, 0x40, 0x40), "Whisper": (0xFF, 0x80, 0xFF),
    "System": (0xFF, 0xFF, 0x00), "Instance": (0xFF, 0x7F, 0x00),
}
PALETTE = list(dict.fromkeys(list(CLASS_COLORS.values()) + list(CHANNEL_COLORS.values())))


def to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def line_colors(crop, np) -> tuple[str, str | None] | None:
    """-> (line colour, name colour or None) as "#rrggbb".
    WoW draws text in exact colours, so the solid middle of each letter matches a known class
    or channel colour almost exactly (edges are blended and ignored). The line's colour is the
    one the timestamp/first characters use; the name is the first *other* known colour."""
    if crop.size == 0:
        return None
    px = crop.reshape(-1, 3).astype(int)
    xs = np.tile(np.arange(crop.shape[1]), crop.shape[0])
    pal = np.array(PALETTE)
    dist = ((px[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
    nearest, clean = dist.argmin(axis=1), dist.min(axis=1) <= 12 ** 2
    nearest, xs = nearest[clean], xs[clean]
    if len(nearest) < 10:
        return None
    order = xs.argsort()
    nearest, xs = nearest[order], xs[order]
    line_i = Counter(nearest[:30].tolist()).most_common(1)[0][0]
    # where each other colour really starts (5th pixel, so stray matches don't count)
    starts = {}
    for i in set(nearest.tolist()) - {line_i}:
        hits = xs[nearest == i]
        if len(hits) >= 8:
            starts[i] = hits[4]
    name_i = min(starts, key=starts.get) if starts else None
    return to_hex(PALETTE[line_i]), (to_hex(PALETTE[name_i]) if name_i is not None else None)


class WindowsEngine:
    """Built-in Windows OCR. Fast, no extra install, weaker on busy backgrounds."""

    name = "Windows OCR"

    def __init__(self):
        import asyncio

        from winrt.windows.media.ocr import OcrEngine

        self.engine = OcrEngine.try_create_from_user_profile_languages()
        if self.engine is None:
            raise RuntimeError("Windows OCR not available (no OCR language installed).")
        self.loop = asyncio.new_event_loop()

    def read(self, img: Image.Image) -> list["Line"]:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        # Light text on dark bg -> invert to dark-on-light, upscale for small fonts.
        g = ImageOps.autocontrast(ImageOps.invert(ImageOps.grayscale(img)), cutoff=1)
        g = g.resize((g.width * 2, g.height * 2), Image.LANCZOS).convert("RGBA")
        r, gg, b, a = g.split()
        dw = DataWriter()
        dw.write_bytes(Image.merge("RGBA", (b, gg, r, a)).tobytes())
        bmp = SoftwareBitmap.create_copy_from_buffer(
            dw.detach_buffer(), BitmapPixelFormat.BGRA8, g.width, g.height
        )
        result = self.loop.run_until_complete(self.engine.recognize_async(bmp))
        lines = []
        for line in result.lines:
            rect = line.words[0].bounding_rect
            lines.append(Line(x=rect.x / 2, h=rect.height / 2, text=line.text, ok=True))
        return lines


def make_engine():
    try:
        return RapidEngine()
    except ImportError:
        return WindowsEngine()


# --- chat line handling -----------------------------------------------------

# Chat lines look like:  "09:05 [R] [Swaggylock-Area52]: y hacer los cleans..."
#                         "09:05 [Midio-Ragnaros] says: entiendo"
#                         "Chemistry-Ragnaros has initiated a ready check."
TIMESTAMP_RE = re.compile(r"^\d{1,2}\s?[:.;]\s?\d{2}(\s?[AaPp][Mm])?\s*(?=\S)")
# [аеуо] = Cyrillic look-alikes the Russian model sometimes uses for English words.
# CJK chat often has no space after the colon, and may use a full-width colon.
SPEAKER_RE = re.compile(
    r"^(.{1,60}?)(?:\s+(?:s[aа][yу]s|[yу][eе]lls|whispers))?\s?[:：]\s*(.+)$"
)


@dataclass
class Line:
    x: float      # left edge in pixels
    h: float      # text height in pixels
    text: str
    ok: bool      # OCR confidence good enough
    colors: tuple | None = None  # (line colour, name colour or None) as "#rrggbb"


class Msg(str):
    """A chat message's text, carrying the colours sampled from its first line."""

    colors: tuple | None = None


def group_messages(lines: list[Line]) -> list[Msg]:
    """WoW starts every message at the left edge and indents wrapped lines,
    so a line at the left margin starts a new message; an indented one continues it."""
    lines = [l for l in lines if l.text.strip()]
    if not lines:
        return []
    margin = min(l.x for l in lines)
    line_h = statistics.median(l.h for l in lines)
    groups: list[list[Line]] = []
    for line in lines:
        if line.x - margin < line_h * 0.5:
            groups.append([line])
        elif groups:
            groups[-1].append(line)
        # else: indented line at the very top = tail of a message scrolled off; skip
    # drop a message if any of its lines was low-confidence (half-cut, garbled)
    msgs = []
    for g in groups:
        if all(l.ok for l in g):
            m = Msg(" ".join(l.text.strip() for l in g))
            m.colors = g[0].colors
            msgs.append(m)
    return msgs


def clean_speaker(who: str) -> str:
    # OCR often reads "]" as l / I / J / 1. A "[token" with no "]" lost its closing bracket.
    parts = []
    for tok in re.findall(r"\[[^\[\]]*\]?|[^\[]+", who):
        tok = tok.strip()
        if tok.startswith("[") and not tok.endswith("]"):
            tok = re.sub(r"\s+\S{1,2}$", "", tok)  # icon junk: "[Name-Realml &" -> "[Name-Realml"
            tok = tok[:-1] if tok[-1:] in "lIJ1|" else tok
            tok += "]"
        if len(tok) > 2:  # skip icon junk like "&", "g", "@"
            parts.append(tok)
    return " ".join(parts)


def split_message(msg: str) -> tuple[str, str, str]:
    """-> (timestamp, speaker, body)"""
    ts = ""
    m = TIMESTAMP_RE.match(msg)
    if m:
        ts, msg = m.group(0).strip(), msg[m.end():]
    m = SPEAKER_RE.match(msg)
    if m:
        return ts, clean_speaker(m.group(1)), m.group(2).strip()
    return ts, "", msg.strip()


def norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


def similar(a: str, b: str, threshold: float) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= threshold


class Seen:
    """Remembers recent messages so scrolling chat isn't re-translated (fuzzy, OCR jitters)."""

    def __init__(self, size=300):
        self.items: list[str] = []
        self.size = size

    def contains(self, key: str) -> bool:
        for old in reversed(self.items):
            if (key in old or old in key) and min(len(key), len(old)) > 0.8 * max(len(key), len(old)):
                return True
            if similar(key, old, 0.85):
                return True
        return False

    def check_and_add(self, key: str) -> bool:
        """True if already seen. `key` should be normalized (see message_key)."""
        if len(key) < 3:
            return True
        if self.contains(key):
            return True
        self.items.append(key)
        del self.items[: -self.size]
        return False


def message_key(msg: str) -> str:
    # Key on the message text, so a garbled name/timestamp doesn't make it look new.
    # Short texts ("ok", "ty") are common, so for those include the speaker too.
    body = norm(split_message(msg)[2])
    return body if len(body) >= 12 else norm(msg)


# --- translation ------------------------------------------------------------

# Languages WoW ships clients in. Anything else Google "detects" (Quechua, Ilocano...) is
# almost always garbled OCR or slang, so it's ignored.
WOW_LANGS = {"es", "pt", "de", "fr", "it", "ru", "ko", "zh-CN", "zh-TW", "ja"}
# Google's language-detection confidence. Pure garbage scores ~0.45, but so can real chat that
# mixes Spanish with English raid terms ("Aaah el frost nova, no" = 0.18), so it's off by default.
MIN_LANG_CONFIDENCE = 0.0

_cache: dict[str, tuple[str, str, float]] = {}


def translate(text: str, target: str = TARGET_LANG, source: str = "auto") -> tuple[str, str, float]:
    """Return (translated_text, detected_source_lang, detection_confidence).
    Uses Google's free endpoint."""
    if (text, source) in _cache:
        return _cache[(text, source)]
    url = (
        "https://translate.googleapis.com/translate_a/single?client=gtx&dt=t&dt=ld"
        f"&sl={source}&tl={target}&q={urllib.parse.quote(text)}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    conf = data[6] if len(data) > 6 and isinstance(data[6], (int, float)) else 1.0
    out = ("".join(p[0] for p in data[0] if p and p[0]), data[2], float(conf))
    _cache[(text, source)] = out
    return out


# --- worker -----------------------------------------------------------------

class Scanner(threading.Thread):
    def __init__(self, out: queue.Queue):
        super().__init__(daemon=True)
        self.out = out
        self.region: dict | None = None
        self.paused = False
        self.debug = False
        self.seen = Seen()
        self.prev: list[str] = []  # messages from the previous scan
        self.recent_langs: deque[str] = deque(maxlen=20)

    def stable(self, msgs: list[str]) -> list[str]:
        """Only pass messages that also appeared in the previous scan. Filters out one-frame
        garbage from overlays (Scroll to Bottom, tooltips), scrolling, and fading lines."""
        keys = [norm(m) for m in msgs]
        out = [m for m, k in zip(msgs, keys) if any(similar(k, p, 0.9) for p in self.prev)]
        self.prev = keys
        return out

    def new_messages(self, msgs: list[str]) -> list[str]:
        """Chat grows downward, so only messages below the lowest one we've already handled
        are new. Anything above it (including half-cut lines at the top) is old.
        If nothing is recognised (startup, or a burst bigger than the box), skip the topmost
        message since it may be cut in half."""
        anchor = -1
        for i, m in enumerate(msgs):
            key = message_key(m)
            if len(key) >= 3 and self.seen.contains(key):
                anchor = i
        return msgs[anchor + 1:] if anchor >= 0 else msgs[1:]

    def process(self, lines: list["Line"]):
        for msg in self.new_messages(self.stable(group_messages(lines))):
            self.handle(msg)

    def run(self):
        self.out.put(("status", "Loading OCR engine..."))
        try:
            engine = make_engine()
        except Exception as e:
            self.out.put(("status", f"OCR failed to load: {e}"))
            return
        self.out.put(("status", f"OCR engine: {engine.name}"))
        with mss.MSS() as sct:
            while True:
                start = time.time()
                if self.region and not self.paused:
                    try:
                        shot = sct.grab(self.region)
                        img = Image.frombytes("RGB", shot.size, shot.rgb)
                        lines = engine.read(img)
                        if self.debug:
                            self.out.put(("status", "OCR:\n" + "\n".join(
                                f"  x={l.x:4.0f} {'ok ' if l.ok else 'LOW'} {l.text}"
                                for l in lines)))
                        self.process(lines)
                    except Exception as e:
                        self.out.put(("status", f"Error: {e}"))
                time.sleep(max(0.05, SCAN_SECONDS - (time.time() - start)))

    def handle(self, msg: str):
        ts, who, body = split_message(msg)
        if self.seen.check_and_add(message_key(msg)):
            return
        if ts:
            who = f"{ts} {who}".strip()
        if sum(c.isalpha() for c in body) < 3:
            return
        try:
            translated, src, conf = translate(body)
            if src == TARGET_LANG:
                return
            unchanged = norm(translated) == norm(body)
            if (src not in WOW_LANGS or unchanged) and self.recent_langs:
                # Typos/slang confuse detection ("empiesa" -> Bulgarian). Retry assuming the
                # language most of recent chat has been in.
                guess = Counter(self.recent_langs).most_common(1)[0][0]
                retry, _, _ = translate(body, source=guess)
                if norm(retry) != norm(body):
                    translated, src, conf, unchanged = retry, f"{guess}?", 1.0, False
        except Exception as e:
            self.out.put(("status", f"Translate error: {e}"))
            return
        if unchanged:
            return
        if src.rstrip("?") not in WOW_LANGS or conf < MIN_LANG_CONFIDENCE:
            if self.debug:
                self.out.put(("status", f"Skipped ({src} {conf:.2f}): {body}"))
            return
        if not src.endswith("?"):
            self.recent_langs.append(src)
        # Split "09:45 [R] [Name-Realm]" into prefix + name so the name gets its class colour.
        m = re.match(r"^(.*?)\s*(\[[^\[\]]+\])$", who)
        prefix, name = (m.group(1), m.group(2)) if m else (who, "")
        line_c, name_c = getattr(msg, "colors", None) or (None, None)
        self.out.put(("msg", prefix, name, translated, body, src, line_c, name_c or line_c))


# --- region picker ----------------------------------------------------------

def pick_region(root: tk.Tk, on_done):
    with mss.MSS() as sct:
        v = sct.monitors[0]  # whole virtual desktop
    top = tk.Toplevel(root)
    top.overrideredirect(True)
    top.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
    top.attributes("-topmost", True)
    top.attributes("-alpha", 0.3)
    c = tk.Canvas(top, bg="black", cursor="crosshair", highlightthickness=0)
    c.pack(fill="both", expand=True)
    c.create_text(v["width"] // 2, 40, text="Drag a box over the chat  (Esc to cancel)",
                  fill="white", font=("Segoe UI", 18, "bold"))
    state = {}

    def down(e):
        state["x0"], state["y0"] = e.x, e.y
        state["rect"] = c.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff3", width=3)

    def drag(e):
        c.coords(state["rect"], state["x0"], state["y0"], e.x, e.y)

    def up(e):
        x0, x1 = sorted((state["x0"], e.x))
        y0, y1 = sorted((state["y0"], e.y))
        top.destroy()
        if x1 - x0 > 20 and y1 - y0 > 10:
            on_done({"left": x0 + v["left"], "top": y0 + v["top"],
                     "width": x1 - x0, "height": y1 - y0})

    c.bind("<ButtonPress-1>", down)
    c.bind("<B1-Motion>", drag)
    c.bind("<ButtonRelease-1>", up)
    top.bind("<Escape>", lambda e: top.destroy())
    top.focus_force()


# --- main window ------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.scanner = Scanner(self.q)
        cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}

        root.title(f"WoW Auto Translate v{__version__}")
        root.geometry(cfg.get("window", "520x260+40+40"))
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.88)
        root.configure(bg="#111")

        bar = tk.Frame(root, bg="#222")
        bar.pack(fill="x")
        btn = dict(bg="#333", fg="#eee", relief="flat", padx=8, activebackground="#555")
        tk.Button(bar, text="Select area", command=self.select, **btn).pack(side="left", padx=2, pady=2)
        self.pause_btn = tk.Button(bar, text="Pause", command=self.toggle_pause, **btn)
        self.pause_btn.pack(side="left", padx=2, pady=2)
        tk.Button(bar, text="Clear", command=self.clear, **btn).pack(side="left", padx=2, pady=2)
        self.show_original = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="Show original", variable=self.show_original, bg="#222",
                       fg="#eee", selectcolor="#333", activebackground="#222").pack(side="left", padx=6)

        self.text = tk.Text(root, bg="#111", fg="#eee", font=("Segoe UI", 11), wrap="word",
                            borderwidth=0, highlightthickness=0, padx=8, pady=6)
        self.text.pack(fill="both", expand=True)
        self.text.tag_config("who", foreground="#ff7d0a", font=("Segoe UI", 11, "bold"))
        self.text.tag_config("orig", foreground="#888", font=("Segoe UI", 9, "italic"))
        self.text.tag_config("status", foreground="#6a9", font=("Segoe UI", 9))
        self.text.configure(state="disabled")

        self.debug = tk.BooleanVar(value=False)
        menu = tk.Menu(root, tearoff=0)
        menu.add_checkbutton(label="Debug: show raw OCR", variable=self.debug,
                             command=lambda: setattr(self.scanner, "debug", self.debug.get()))
        menu.add_command(label="Quit", command=self.quit)
        self.text.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        root.protocol("WM_DELETE_WINDOW", self.quit)

        if cfg.get("region"):
            self.scanner.region = cfg["region"]
            self.status("Using saved chat area. Click 'Select area' to change it.")
        else:
            self.status("Click 'Select area' and drag a box over the WoW chat.")
        self.scanner.start()
        self.poll()

    def select(self):
        def done(region):
            self.scanner.region = region
            self.scanner.seen = Seen()
            self.save()
            self.status(f"Watching {region['width']}x{region['height']} at "
                        f"({region['left']},{region['top']})")
        pick_region(self.root, done)

    def toggle_pause(self):
        self.scanner.paused = not self.scanner.paused
        self.pause_btn.config(text="Resume" if self.scanner.paused else "Pause")

    def save(self):
        CONFIG.write_text(json.dumps({"region": self.scanner.region,
                                      "window": self.root.geometry()}, indent=2))

    def quit(self):
        self.save()
        self.root.destroy()

    def append(self, *chunks):
        self.text.configure(state="normal")
        for s, tag in chunks:
            self.text.insert("end", s, tag)
        if int(self.text.index("end-1c").split(".")[0]) > 300:
            self.text.delete("1.0", "100.0")
        self.text.see("end")
        self.text.configure(state="disabled")

    def status(self, s):
        self.append((s + "\n", "status"))

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def color_tag(self, color: str | None) -> str:
        """A bold text tag in the given colour (game colours), defaulting to orange."""
        color = color or "#ff7d0a"
        tag = "c" + color
        if tag not in self.text.tag_names():
            self.text.tag_config(tag, foreground=color, font=("Segoe UI", 11, "bold"))
        return tag

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "status":
                    self.status(item[1])
                else:
                    _, prefix, name, translated, original, src, line_c, name_c = item
                    chunks = []
                    if prefix:
                        chunks.append((prefix + " ", self.color_tag(line_c)))
                    if name:
                        chunks.append((name, self.color_tag(name_c)))
                    if prefix or name:
                        chunks.append((": ", self.color_tag(line_c)))
                    chunks.append((translated + "\n", None))
                    if self.show_original.get():
                        chunks.append((f"   [{src}] {original}\n", "orig"))
                    self.append(*chunks)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def self_test(image_path: str, out_path: str):
    """`--self-test chat.png out.txt`: OCR + translate an image without the UI (checks a build)."""
    engine, q = make_engine(), queue.Queue()
    scanner = Scanner(q)
    img = Image.open(image_path).convert("RGB")
    lines = engine.read(img)
    report = [f"engine: {engine.name}", *(f"ocr: {l.text}" for l in lines)]
    for msg in group_messages(lines):
        scanner.handle(msg)
    while not q.empty():
        item = q.get()
        report.append(f"[{item[5]}] {item[1]} {item[2]}: {item[3]}" if item[0] == "msg" else item[1])
    Path(out_path).write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--self-test":
        self_test(sys.argv[2], sys.argv[3])
    else:
        root = tk.Tk()
        App(root)
        root.mainloop()
