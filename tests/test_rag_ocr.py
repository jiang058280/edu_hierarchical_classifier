from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from edu_core.config.settings import Settings
from edu_core.rag.loaders import DocumentLoadError, load_document_bytes
from edu_core.rag.loaders import ocr


def settings(**kwargs):
    return Settings(_env_file=None, rag_ocr_enabled=True, **kwargs)


def mock_pdf(monkeypatch, texts):
    import pypdf
    pages = [SimpleNamespace(extract_text=lambda text=text: text) for text in texts]
    monkeypatch.setattr(pypdf, "PdfReader", lambda _: SimpleNamespace(pages=pages))


def test_mixed_pdf_ocr_only_sparse_pages_preserves_order(monkeypatch):
    text = "这是足够长的文字层内容，不需要进行图片识别，也不应重复处理。"
    mock_pdf(monkeypatch, [text, "2", text])
    def recognize(content, pages, config):
        assert pages == [2]
        return {2: "扫描页的识别内容"}
    monkeypatch.setattr("edu_core.rag.loaders.document_loader.recognize_pages", recognize)
    result = load_document_bytes("资料.pdf", b"fake", settings=settings())
    assert result.text == f"{text}\n\n扫描页的识别内容\n\n{text}"
    assert result.ocr_pages == (2,) and result.page_count == 3


def test_disabled_ocr_does_not_invoke_local_tools(monkeypatch):
    mock_pdf(monkeypatch, [""])
    def forbidden(*args):
        pytest.fail("关闭时不调用 OCR")
    monkeypatch.setattr("edu_core.rag.loaders.document_loader.recognize_pages", forbidden)
    with pytest.raises(DocumentLoadError, match="OCR"):
        load_document_bytes("资料.pdf", b"fake")


def test_ocr_failure_not_silently_ignored_in_mixed_pdf(monkeypatch):
    mock_pdf(monkeypatch, ["文字层内容" * 10, ""])
    def fail(*args):
        raise ocr.OCRError("缺少 OCR 语言包")
    monkeypatch.setattr("edu_core.rag.loaders.document_loader.recognize_pages", fail)
    with pytest.raises(DocumentLoadError, match="语言包"):
        load_document_bytes("资料.pdf", b"fake", settings=settings())


def test_page_limit_and_missing_dependencies(monkeypatch):
    with pytest.raises(ocr.OCRError, match="页数"):
        ocr.recognize_pages(b"fake", [1, 2], settings(rag_ocr_max_pages=1))
    monkeypatch.setattr(ocr.shutil, "which", lambda _: None)
    with pytest.raises(ocr.OCRError, match="缺少"):
        ocr.recognize_pages(b"fake", [1], settings())
    assert ocr.recognize_pages(b"fake", [], settings()) == {}


def test_js_requires_local_assets_before_any_process(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", lambda name: name)
    with pytest.raises(ocr.OCRError, match="显式配置"):
        ocr.recognize_pages(b"fake", [1], settings(rag_ocr_backend="tesseract_js"))


def test_layout_requires_supported_backend():
    with pytest.raises(ocr.OCRError, match="仅支持"):
        ocr.recognize_pages(b"fake", [1], settings(rag_ocr_layout_mode="lines_tables"))


def test_js_passes_local_assets_and_output_path(tmp_path, monkeypatch):
    module, data = tmp_path / "module", tmp_path / "data"
    module.mkdir()
    data.mkdir()
    (module / "package.json").write_text("{}", encoding="utf-8")
    for lang in ("chi_sim", "eng"):
        (data / f"{lang}.traineddata").write_bytes(b"test")
    factory = ocr.tempfile.TemporaryDirectory
    monkeypatch.setattr(ocr.tempfile, "TemporaryDirectory", lambda **kwargs: factory(dir=tmp_path, **kwargs))
    monkeypatch.setattr(ocr.shutil, "which", lambda name: name)
    def run(command, timeout):
        if command[0] == "pdftoppm":
            Path(command[-1]).with_suffix(".png").write_bytes(b"test")
        else:
            assert command[0] == "node"
            assert command[1].endswith("tesseract_bridge.cjs")
            assert command[2:4] == [str(module), str(data)]
            assert command[-1] == "chi_sim+eng"
            Path(command[-2]).write_text("中文识别", encoding="utf-8")
    monkeypatch.setattr(ocr, "_run", run)
    assert ocr.recognize_pages(b"fake", [1], settings(
        rag_ocr_backend="tesseract_js", rag_ocr_js_module_dir=str(module),
        rag_ocr_tessdata_dir=str(data))) == {1: "中文识别"}


@pytest.mark.parametrize("failure", [None, "timeout", "exit", "empty"])
def test_local_commands_use_argv_limits_and_cleanup(tmp_path, monkeypatch, failure):
    temp_factory = ocr.tempfile.TemporaryDirectory
    monkeypatch.setattr(ocr.tempfile, "TemporaryDirectory",
                        lambda **kwargs: temp_factory(dir=tmp_path, **kwargs))
    monkeypatch.setattr(ocr.shutil, "which", lambda command: command)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 60 and kwargs["stderr"] == subprocess.DEVNULL
        assert "shell" not in kwargs
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        if failure == "exit":
            return SimpleNamespace(returncode=1)
        if command[0] == "pdftoppm":
            assert command[1:5] == ["-f", "2", "-l", "2"]
            assert command[7:9] == ["-scale-to", "3000"]
            Path(command[-1]).with_suffix(".png").write_bytes(b"image")
        else:
            assert command[3:] == ["-l", "chi_sim+eng", "--psm", "3"]
            Path(command[2]).with_suffix(".txt").write_text("" if failure == "empty" else "识别文字", encoding="utf-8")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(ocr.subprocess, "run", run)
    if failure:
        with pytest.raises(ocr.OCRError):
            ocr.recognize_pages(b"fake", [2], settings())
    else:
        assert ocr.recognize_pages(b"fake", [2], settings()) == {2: "识别文字"}
        assert len(calls) == 2
    assert list(tmp_path.iterdir()) == []
