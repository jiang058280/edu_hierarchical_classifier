"""v0.3 训练进度监控。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
lines = open("logs/train_v03.log", encoding="utf-8", errors="ignore").read().splitlines()
for ln in lines:
    if ("epoch" in ln and "完成" in ln) or "早停" in ln or "保存最佳" in ln or "训练完成" in ln or "Error" in ln or "Traceback" in ln:
        print(ln)
print("--- 最新 3 行 ---")
print("\n".join(lines[-3:]))
