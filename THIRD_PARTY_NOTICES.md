# Third-party notices

WoW Auto Translate is licensed under the GNU General Public License v3.0 (see `LICENSE`).
Source code: https://github.com/Chrysana/wow-auto-translate

The Windows release bundles the third-party components below, unmodified. Each is used
under its own license. The full license and notice files for every bundled component are
in the `licenses/` folder of the release, and `licenses/INDEX.txt` lists them all.

## OCR models

The OCR models are PaddleOCR models, in the ONNX format distributed by RapidOCR:

| File | Upstream model | License |
|---|---|---|
| `PP-OCRv6_det_small.onnx` | PP-OCRv6_small_det | Apache-2.0 |
| `PP-OCRv6_rec_small.onnx` | PP-OCRv6_small_rec | Apache-2.0 |
| `eslav_PP-OCRv5_rec_mobile.onnx` | eslav_PP-OCRv5_mobile_rec | Apache-2.0 |
| `korean_PP-OCRv5_rec_mobile.onnx` | korean_PP-OCRv5_mobile_rec | Apache-2.0 |

> Converted from an official PaddleOCR model to the indicated inference format and packaged
> for use with RapidOCR. The upstream model weights are copyright Baidu and/or the applicable
> PaddleOCR rights holders and are licensed under the Apache License, Version 2.0.

- PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR
- RapidOCR's model license and attribution notice: `licenses/rapidocr-*/MODEL_LICENSES.md`

## Libraries

| Component | License |
|---|---|
| RapidOCR | Apache-2.0 |
| ONNX Runtime | MIT |
| OpenCV (opencv-python-headless) | Apache-2.0, with bundled third-party components (see its notice files) |
| NumPy | BSD-3-Clause, with bundled components (see its license files) |
| Pillow | MIT-CMU |
| mss | MIT |
| Shapely | BSD-3-Clause |
| pyclipper | MIT |
| protobuf | BSD-3-Clause |
| flatbuffers | Apache-2.0 |
| PyYAML, six, colorlog, colorama, urllib3, charset-normalizer | MIT / BSD |
| omegaconf, packaging, requests, idna | BSD / Apache-2.0 |
| certifi, tqdm | MPL-2.0 (tqdm also MIT). Unmodified; source at https://github.com/certifi/python-certifi and https://github.com/tqdm/tqdm |
| Python | PSF License |
| Tcl/Tk | Tcl/Tk license (BSD-style) |

None of these projects endorse WoW Auto Translate. World of Warcraft is a trademark of
Blizzard Entertainment; this project is not affiliated with or endorsed by Blizzard.
