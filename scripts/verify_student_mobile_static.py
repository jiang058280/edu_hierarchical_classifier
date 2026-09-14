"""学生端移动适配静态验收；不启动浏览器、不访问外网。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES = ("student.html", "student_assignment.html", "student_qa.html", "student_wrong_book.html", "student_report.html")


def main() -> int:
    static = ROOT / "static"
    checks = []
    for name in PAGES:
        content = (static / name).read_text(encoding="utf-8")
        checks.append({"page": name, "viewport": 'name="viewport"' in content,
                       "uses_design_system": "/static/edu.css" in content,
                       "responsive_rule": "@media" in content or "teacher_common.js" in content or "student_common.js" in content})
    passed = all(all(value for key, value in item.items() if key != "page") for item in checks)
    report = {"pages": checks, "passed": passed,
              "note": "静态验收不替代真实 375px 浏览器或手机走查。"}
    target = ROOT / "reports/verification/student_mobile_static_latest.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
