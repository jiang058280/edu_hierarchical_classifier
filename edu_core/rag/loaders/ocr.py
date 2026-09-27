"""本地 Poppler + Tesseract OCR，无网络调用。"""

from pathlib import Path
import shutil
import subprocess
import tempfile

from edu_core.config.settings import Settings


class OCRError(RuntimeError):
    """受控错误，不回显子进程日志或资料正文。"""


def _run(command: list[str], timeout: float) -> None:
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise OCRError("本地 OCR 处理超时") from None
    except OSError:
        raise OCRError("本地 OCR 工具无法启动") from None
    if result.returncode != 0:
        raise OCRError("本地 OCR 工具失败，请检查 PDF、工具版本及语言包")


def recognize_pages(content: bytes, page_numbers: list[int], settings: Settings) -> dict[int, str]:
    if not page_numbers:
        return {}
    if settings.rag_ocr_layout_mode != "page" and settings.rag_ocr_backend != "tesseract_js":
        raise OCRError("逐行表格模式仅支持本地 Tesseract.js 后端")
    if len(page_numbers) > settings.rag_ocr_max_pages:
        raise OCRError("需要 OCR 的页数超过限制，请拆分资料")
    if any(type(page) is not int or page < 1 for page in page_numbers):
        raise OCRError("OCR 页码无效")
    renderer = shutil.which(settings.rag_ocr_pdftoppm_command)
    engine = shutil.which(settings.rag_ocr_node_command if settings.rag_ocr_backend == "tesseract_js"
                          else settings.rag_ocr_tesseract_command)
    if not renderer or not engine:
        raise OCRError("本地 OCR 缺少 pdftoppm 或 Tesseract，请安装并配置可执行文件")
    if settings.rag_ocr_backend == "tesseract_js":
        if not settings.rag_ocr_js_module_dir or not settings.rag_ocr_tessdata_dir:
            raise OCRError("Tesseract.js 需要显式配置本地模块目录及语言包目录")
        module = Path(settings.rag_ocr_js_module_dir).resolve()
        data = Path(settings.rag_ocr_tessdata_dir).resolve()
        if not (module / "package.json").is_file() or any(
                not (data / f"{lang}.traineddata").is_file() for lang in settings.rag_ocr_languages.split("+")):
            raise OCRError("Tesseract.js 模块或本地语言包缺失，不自动联网下载")
    result = {}
    with tempfile.TemporaryDirectory(prefix="edu-rag-ocr-") as directory:
        root = Path(directory)
        source = root / "input.pdf"
        source.write_bytes(content)
        for page in page_numbers:
            prefix = root / f"page-{page}"
            _run([renderer, "-f", str(page), "-l", str(page), "-singlefile", "-png",
                  "-scale-to", str(settings.rag_ocr_max_side_pixels), str(source), str(prefix)],
                 settings.rag_ocr_timeout_seconds)
            image = prefix.with_suffix(".png")
            if not image.is_file() or image.stat().st_size > 40 * 1024 * 1024:
                raise OCRError("OCR 页面图像缺失或过大")
            if settings.rag_ocr_backend == "tesseract_js":
                bridge = Path(__file__).with_name("tesseract_bridge.cjs")
                command = [engine, str(bridge), str(module), str(data), str(image),
                           str(prefix.with_suffix(".txt")), settings.rag_ocr_languages]
                if settings.rag_ocr_layout_mode != "page":
                    command.append(settings.rag_ocr_layout_mode)
            else:
                command = [engine, str(image), str(prefix), "-l", settings.rag_ocr_languages, "--psm", "3"]
            _run(command, settings.rag_ocr_timeout_seconds)
            output = prefix.with_suffix(".txt")
            if not output.is_file() or output.stat().st_size > 1_000_000:
                raise OCRError("OCR 文本缺失或过大")
            try:
                text = output.read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError):
                raise OCRError("OCR 文本无法读取") from None
            if not text:
                raise OCRError(f"第 {page} 页 OCR 未识别到文字，请人工核对空白页或扫描质量")
            result[page] = text
    return result
