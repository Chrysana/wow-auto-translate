# PyInstaller spec for WoW Auto Translate. Build with build.bat (or: python build.py).
from pathlib import Path

import rapidocr
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Only the OCR models the app actually loads (see RapidEngine in wow_translate.py).
MODELS = [
    "PP-OCRv6_det_small.onnx",         # line detection
    "PP-OCRv6_rec_small.onnx",         # multilingual recognition
    "eslav_PP-OCRv5_rec_mobile.onnx",  # Russian / Cyrillic
    "korean_PP-OCRv5_rec_mobile.onnx", # Korean
]
model_dir = Path(rapidocr.__file__).parent / "models"

datas = [(src, dst) for src, dst in collect_data_files("rapidocr") if not src.endswith(".onnx")]
datas += [(str(model_dir / m), "rapidocr/models") for m in MODELS]

# RapidOCR loads its inference engine dynamically; we only use ONNX Runtime.
unused_engines = ("pytorch", "paddle", "openvino", "tensorrt", "mnn")
hiddenimports = collect_submodules(
    "rapidocr", filter=lambda name: not any(e in name for e in unused_engines)
)

a = Analysis(
    ["wow_translate.py"],
    datas=datas,
    hiddenimports=hiddenimports,
    # winrt = Windows OCR fallback, not needed when RapidOCR is bundled
    excludes=["winrt", "torch", "paddle", "openvino", "matplotlib", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="WoWAutoTranslate",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="WoWAutoTranslate", upx=False)
