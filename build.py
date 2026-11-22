"""Build the Windows release.

Produces:
  dist/WoWAutoTranslate/                        run WoWAutoTranslate.exe
  dist/WoWAutoTranslate-v<version>-windows.zip  the same folder, zipped for release

Usually run via build.bat, which installs the requirements first.
"""

import importlib.metadata as md
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import wow_translate  # noqa: E402

APP = "WoWAutoTranslate"
# Top-level packages that end up in the exe; their dependencies are found automatically.
BUNDLED = ["mss", "pillow", "numpy", "rapidocr", "onnxruntime"]
LICENSE_FILE = re.compile(r"LICEN[CS]E|NOTICE|COPYING|AUTHORS|MODEL_LICENSES", re.I)


def ensure_models():
    """Loading the engine downloads any model that isn't on this machine yet."""
    print("Checking OCR models...")
    wow_translate.RapidEngine()


def bundled_distributions():
    seen, todo, out = set(), list(BUNDLED), []
    while todo:
        name = todo.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        out.append(dist)
        for req in dist.requires or []:
            if "extra ==" not in req:
                todo.append(re.split(r"[ ;<>=!~\[(]", req, maxsplit=1)[0])
    return sorted(out, key=lambda d: d.metadata["Name"].lower())


def collect_licenses(dest: Path):
    """Copy every bundled component's license/notice files into dest/<component>/."""
    dest.mkdir(parents=True, exist_ok=True)
    rows = []
    for dist in bundled_distributions():
        name, version = dist.metadata["Name"], dist.version
        lic = (dist.metadata.get("License-Expression") or dist.metadata.get("License") or "")
        lic = lic.splitlines()[0][:60] if lic else "see files"
        target = dest / f"{name}-{version}"
        files = [f for f in dist.files or [] if LICENSE_FILE.search(f.name)]
        for f in files:
            src = Path(dist.locate_file(f))
            if src.is_file():
                target.mkdir(exist_ok=True)
                shutil.copy2(src, target / src.name)
        rows.append(f"{name} {version}: {lic}" + ("" if files else " (no license file shipped by the package)"))
    # Python runtime and Tcl/Tk (tkinter)
    base = Path(sys.base_prefix)
    if (base / "LICENSE.txt").exists():
        (dest / "Python").mkdir(exist_ok=True)
        shutil.copy2(base / "LICENSE.txt", dest / "Python" / "LICENSE.txt")
        rows.append(f"Python {sys.version.split()[0]}: PSF License")
    for terms in sorted((base / "tcl").glob("*/license.terms"))[:4]:
        (dest / "Tcl-Tk").mkdir(exist_ok=True)
        shutil.copy2(terms, dest / "Tcl-Tk" / f"{terms.parent.name}-license.terms")
    if (dest / "Tcl-Tk").exists():
        rows.append("Tcl/Tk: BSD-style (Tcl/Tk license)")
    (dest / "INDEX.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"Collected licenses for {len(rows)} components")


def main():
    ensure_models()
    print("Running PyInstaller...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "wow_translate.spec"],
        cwd=ROOT, check=True,
    )
    out = ROOT / "dist" / APP
    for f in ("LICENSE", "README.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / f, out / f)
    collect_licenses(out / "licenses")
    zip_base = ROOT / "dist" / f"{APP}-v{wow_translate.__version__}-windows"
    archive = shutil.make_archive(str(zip_base), "zip", root_dir=out.parent, base_dir=APP)
    size = Path(archive).stat().st_size / 1e6
    print(f"\nBuilt {out / (APP + '.exe')}\nRelease zip: {archive} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
