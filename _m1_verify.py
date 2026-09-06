"""M1 全链路验证：papers API 返回 JSON / 建完整题 / AI 预标注 / 组卷生成+保存+Word 导出。"""
import io
import json
import sys
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:7860/api/v1"


def call(method, path, token=None, form=None, body=None, raw=False):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        payload = resp.read()
        return resp.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload[:300].decode("utf-8", "ignore")


data = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request(BASE + "/auth/login?portal=teacher", data=data,
                             headers={"Content-Type": "application/x-www-form-urlencoded"},
                             method="POST")
tok = json.loads(urllib.request.urlopen(req).read())["access_token"]
print("登录 OK")

code, papers = call("GET", "/teacher/papers?limit=1", token=tok)
print("1) papers API 返回 JSON:", code, "| total:", papers.get("total"))

# 完整题目创建
q = {"text": "关于 chlorophyll 的叙述，下列正确的是：叶绿素 a 主要吸收红光和蓝紫光。",
     "subject": "生物", "question_type": "选择题", "grade_band": "高中",
     "answer": "A", "analysis": "叶绿素 a 的吸收峰在红光与蓝紫光区域。",
     "difficulty": 3, "status": "published",
     "options": [{"key": "A", "text": "主要吸收红光和蓝紫光"},
                 {"key": "B", "text": "主要吸收绿光"},
                 {"key": "C", "text": "只吸收蓝紫光"},
                 {"key": "D", "text": "不吸收光"}]}
code, created = call("POST", "/teacher/questions", token=tok, body=q)
print("2) 完整题目入库:", code, "| id:", created.get("id"))
qid = created["id"]

code, detail = call("GET", f"/teacher/questions/{qid}", token=tok)
print("3) 题目详情回读:", code, "| 难度:", detail.get("difficulty"),
      "| 选项数:", len(detail.get("options") or []), "| 状态:", detail.get("status"))

# AI 预标注（单条）
code, pre = call("POST", "/teacher/questions/classify", token=tok,
                 body={"text": "计算 2x²-3x+1 在 x=2 处的函数值。"})
print("4) AI 预标注:", code, "|", pre.get("subject"), pre.get("question_type"),
      pre.get("grade_band"), "| conf:", pre.get("confidences", {}).get("subject"))

# 组卷生成
code, gen = call("POST", "/teacher/papers/generate", token=tok,
                 body={"subject": "生物", "grade_band": "高中",
                       "type_counts": {"选择题": 2}})
print("5) 组卷生成:", code, "| 题数:", gen.get("total"))

# 保存试卷
ids = list(dict.fromkeys([item["id"] for item in gen["questions"]] + [qid]))
code, saved = call("POST", "/teacher/papers", token=tok,
                   body={"title": "生物高中验证卷", "subject": "生物",
                         "grade_band": "高中", "question_ids": ids})
print("6) 保存试卷:", code, "| 响应:", saved if code != 200 else saved.get("id"))
if code != 200:
    sys.exit(1)
pid = saved["id"]

# 导出 Word
code, raw_doc = call("GET", f"/teacher/papers/{pid}/export", token=tok, raw=True)
print("7) Word 导出:", code, "| 大小:", len(raw_doc), "bytes | DOCX 魔数:",
      raw_doc[:2] == b"PK")
