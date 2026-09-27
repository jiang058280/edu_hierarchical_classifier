# -*- coding: utf-8 -*-
"""一次性回填：为全部已发布题目生成 BERT 向量写入 Milvus 查重集合（幂等，可重复执行）。"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from sqlalchemy import text

from edu_core.application.factory import get_classification_service
from edu_core.storage.stores import StoreBundle

service = get_classification_service()
dedup = service.dedup
if not dedup.available():
    print("ABORT: Milvus 不可用")
    raise SystemExit(2)

stores = StoreBundle(settings=service.settings)
with stores.questions.engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT id, content FROM questions WHERE status = 'published' ORDER BY id""")).mappings().all()
print(f"已发布题 {len(rows)} 道，开始回填向量…")
started = time.perf_counter()
done = 0
for row in rows:
    if dedup.upsert_question(int(row["id"]), row["content"]):
        done += 1
    if done % 200 == 0 and done:
        print(f"  已写入 {done} …({time.perf_counter() - started:.0f}s)")
print(f"回填完成：{done}/{len(rows)}，耗时 {time.perf_counter() - started:.0f} 秒")
