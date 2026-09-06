import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:7860/api/v1"

data = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request(BASE + "/auth/login?portal=teacher", data=data,
                             headers={"Content-Type": "application/x-www-form-urlencoded"},
                             method="POST")
tok = json.loads(urllib.request.urlopen(req).read())["access_token"]

body = json.dumps({"title": "诊断卷", "subject": "生物", "grade_band": "高中",
                   "question_ids": [5]}).encode()
req2 = urllib.request.Request(BASE + "/teacher/papers", data=body,
                              headers={"Authorization": "Bearer " + tok,
                                       "Content-Type": "application/json"}, method="POST")
try:
    print("OK:", urllib.request.urlopen(req2).read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print("HTTP", e.code, ":", e.read().decode("utf-8"))
