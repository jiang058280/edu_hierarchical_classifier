"""只读源 PDF，渲染指定页并调用本地 OCR；不入库、不调用外部模型。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edu_core.config.settings import Settings
from edu_core.rag.loaders.ocr import recognize_pages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--tessdata-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--pages", nargs="+", type=int, default=[1])
    args = parser.parse_args()
    from pypdf import PdfReader
    content = args.pdf.read_bytes()
    reader = PdfReader(args.pdf)
    if any(page < 1 or page > len(reader.pages) for page in args.pages):
        parser.error("页码超出原 PDF 范围")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    settings = Settings(_env_file=None, rag_ocr_enabled=True, rag_ocr_backend="tesseract_js",
                        rag_ocr_js_module_dir=str(args.module_dir.resolve()),
                        rag_ocr_tessdata_dir=str(args.tessdata_dir.resolve()))
    started = perf_counter()
    recognized = recognize_pages(content, args.pages, settings)
    elapsed = round(perf_counter() - started, 2)
    for page, text in recognized.items():
        (args.output_dir / f"page-{page}.txt").write_text(text, encoding="utf-8")
        subprocess.run([shutil.which("pdftoppm"), "-f", str(page), "-l", str(page),
                        "-singlefile", "-png", "-scale-to", "1600", str(args.pdf.resolve()),
                        str((args.output_dir / f"page-{page}").resolve())],
                       check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    report = {"status": "executed_pending_visual_review", "source_sha256": hashlib.sha256(content).hexdigest(),
              "source": str(args.pdf), "pages": args.pages, "elapsed_seconds": elapsed,
              "backend": "tesseract_js", "ocr_chars": {page: len(text) for page, text in recognized.items()},
              "source_text_chars": {page: len(reader.pages[page - 1].extract_text() or "") for page in args.pages},
              "language_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in args.tessdata_dir.glob("*.traineddata")},
              "limitation": "原页渲染后 OCR 的技术冒烟，不是自然扫描件质量金标或入库验收"}
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "pages": args.pages, "elapsed_seconds": elapsed}))


if __name__ == "__main__":
    main()
