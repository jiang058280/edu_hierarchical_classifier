"""导出 Word 端点独立验证。"""
import io
import json
import sys
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:7860/api/v1"

data = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request(BASE + "/auth/login?portal=teacher", data=data,
                             headers={"Content-Type": "application/x-www-form-urlencoded"},
                             method="POST")
tok = json.loads(urllib.request.urlopen(req).read())["access_token"]

for pid in (1, 2, 3):
    try:
        req2 = urllib.request.Request(BASE + f"/teacher/papers/{pid}/export",
                                      headers={"Authorization": "Bearer " + tok})
        raw = urllib.request.urlopen(req2).read()
        print(f"试卷 #{pid}: {len(raw)} bytes | DOCX魔数: {raw[:2] == b'PK'}")
    except urllib.error.HTTPError as e:
        print(f"试卷 #{pid}: HTTP {e.code} {e.read().decode('utf-8', 'ignore')[:120]}")

# 试卷详情回读
req3 = urllib.request.Request(BASE + "/teacher/papers/3",
                              headers={"Authorization": "Bearer " + tok})
detail = json.loads(urllib.request.urlopen(req3).read())
print(f"试卷 #3 详情: {detail['title']} | {len(detail['questions'])} 题 | "
      f"首题选项数: {len(detail['questions'][0].get('options') or [])} | "
      f"首题答案: {detail['questions'][0].get('answer')}")
