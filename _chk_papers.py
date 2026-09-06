import io
import json
import sys
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
data = urllib.parse.urlencode({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:7860/api/v1/auth/login?portal=teacher", data=data,
    headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
tok = json.loads(urllib.request.urlopen(req).read())["access_token"]
req2 = urllib.request.Request(
    "http://127.0.0.1:7860/api/v1/teacher/papers?limit=1",
    headers={"Authorization": "Bearer " + tok})
body = urllib.request.urlopen(req2).read().decode("utf-8")
print("papers response:", body)
