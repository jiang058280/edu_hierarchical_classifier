"""生成一页自编教学测试资料及无文字层扫描式 PDF，不写入业务库。"""
from pathlib import Path
import shutil
import subprocess

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.pagesizes import A4

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "pdf" / "ocr_teaching_test"
TMP = ROOT / "tmp" / "pdfs" / "ocr_teaching_test"


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    TMP.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    source = TMP / "text_source.pdf"
    c = canvas.Canvas(str(source), pagesize=A4)
    c.setTitle("一次函数教学资料 - OCR测试")
    def line(text, y, size=13):
        c.setFont("STSong-Light", size)
        c.drawString(48, y, text)
    line("一次函数：从表达式到图像", 785, 22)
    line("初中数学 | 自编教学测试资料 | 仅用于 OCR 验证，不用于正式授课", 753, 10)
    c.line(48, 738, 547, 738)
    line("一、学习目标", 710, 16)
    line("理解一次函数的表达式，能用列表法确定图像上的点。", 683)
    line("二、核心知识", 645, 16)
    line("一次函数的表达式为 y = kx + b，其中 k、b 是常数，且 k 不等于 0。", 618, 12)
    line("当 k > 0 时，y 随 x 的增大而增大；当 k < 0 时，y 随 x 的增大而减小。", 592, 12)
    line("三、例题与数值表", 550, 16)
    line("已知 y = 2x + 1，请计算下表并观察变化规律。", 523)
    xs = [48, 173, 298, 423, 547]
    ys = [500, 465, 430]
    for x in xs:
        c.line(x, ys[-1], x, ys[0])
    for y in ys:
        c.line(xs[0], y, xs[-1], y)
    for row, values in enumerate([["x", "0", "1", "2"], ["y", "1", "3", "5"]]):
        for col, value in enumerate(values):
            c.setFont("STSong-Light", 14)
            c.drawCentredString((xs[col] + xs[col + 1]) / 2, 478 - row * 35, value)
    line("解析：当 x = 2 时，y = 2 × 2 + 1 = 5。", 402)
    line("四、巩固练习", 357, 16)
    line("1. 已知 y = 3x - 2，求 x = 2 时的函数值。", 329)
    line("2. 函数 y = -2x + 4 中，y 随 x 的增大怎样变化？", 301)
    line("参考答案：第 1 题为 4；第 2 题为减小。", 273)
    line("五、OCR 核对要点", 226, 16)
    line("核对中文、负号、乘号、不等号和表格行列，识别错误须人工纠正。", 197, 12)
    line("本文为自编测试样本，不代表真实扫描件识别准确率。", 171, 12)
    line("测试样本编号：OCR-TEACHING-20260926-01", 65, 10)
    c.showPage()
    c.save()
    subprocess.run([shutil.which("pdftoppm"), "-singlefile", "-r", "200", "-png",
                    str(source), str(TMP / "raster")], check=True)
    target = OUT / "一次函数教学资料_扫描测试.pdf"
    c = canvas.Canvas(str(target), pagesize=A4)
    c.drawImage(str(TMP / "raster.png"), 0, 0, width=A4[0], height=A4[1])
    c.showPage()
    c.save()
    subprocess.run([shutil.which("pdftoppm"), "-singlefile", "-scale-to", "1600", "-png",
                    str(target), str(OUT / "preview")], check=True)
    print(target)


if __name__ == "__main__":
    main()
