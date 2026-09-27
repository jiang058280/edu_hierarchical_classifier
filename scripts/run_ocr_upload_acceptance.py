"""显式执行一次真实 OCR 上传验收；创建专用账号和未发布版本，保留结果。"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from sqlalchemy import text

from edu_core.storage.stores import StoreBundle
from edu_core.security.auth import hash_password


def verify_review(client, stores, report, document_id, output, browser_module, password, browser_executable=None):
    """Exercise the real negative publication path; never approve the sample."""
    url = f"/api/v1/teacher/rag/documents/{document_id}"
    document = stores.rag.get_owned_document(document_id, report["user_id"])
    if (not document or document["kb_version_id"] != report["version_id"]
            or document["status"] != "PROCESSED" or document.get("published_at")):
        raise RuntimeError("仅验证当前测试版本中尚未发布的已处理资料")
    response = client.get(url)
    response.raise_for_status()
    review = response.json().get("ocr_review", {})
    if review.get("status") != "PENDING" or not review.get("parent_texts"):
        raise RuntimeError("需新版复核接口且测试资料必须待复核")
    rejected = client.post(url + "/ocr-review", json={
        "token": review["token"], "approved": True,
        "note": "负向验收：未核对原件，不应通过", "checked_source_title_table_math": False})
    if rejected.status_code != 400:
        raise RuntimeError("缺少核对确认的请求未被拦截")
    publication = client.post(url + "/publication", json={"published": True})
    if publication.status_code != 400:
        raise RuntimeError("待复核资料发布未被拦截，请立即检查测试资料状态")
    report.update(document_id=document_id, review_status=review["status"],
                  missing_checklist_http_status=rejected.status_code,
                  publication_http_status=publication.status_code,
                  publication_response=publication.json())
    if browser_module:
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "scripts" / "verify_ocr_review_browser.cjs"), browser_module],
            input=json.dumps({"base_url": report["base_url"], "username": report["username"],
                              "password": password, "document_id": document_id,
                              "version_id": report["version_id"],
                              "browser_executable": browser_executable,
                              "output_dir": str(output.resolve())}),
            text=True, encoding="utf-8", capture_output=True, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("浏览器验收失败（不回显可能包含凭据的进程输出）")
        report["browser"] = json.loads(result.stdout)
    final = client.get(url)
    final.raise_for_status()
    data = final.json()
    version = stores.rag.get_owned_version(report["version_id"], report["user_id"])
    if (data["document"]["status"] != "PROCESSED" or data["document"].get("published_at")
            or data["ocr_review"]["status"] != "PENDING" or version["status"] != "STAGED"):
        raise RuntimeError("测试后状态不符合未审核、未发布、未激活要求")
    report.update(status="review_gate_verified", document_status="PROCESSED", version_status="STAGED")
    print(json.dumps({"status": report["status"], "document_id": document_id}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--js-module", required=True)
    parser.add_argument("--existing-server", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "ocr_upload_acceptance_20260926")
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--rebuild-document", type=int)
    parser.add_argument("--review-document", type=int, help="仅验证复核门禁，不上传、不重建、不审批")
    parser.add_argument("--browser-module", help="本地 playwright 模块目录；仅用于复核验收")
    parser.add_argument("--browser-executable", help="已有本地 Chromium 可执行文件，不自动下载")
    parser.add_argument("--tessdata-dir", type=Path, default=ROOT / ".ocr_models" / "tessdata_fast-4.1.0")
    parser.add_argument("--layout-mode", choices=["page", "lines_tables"], default="page")
    parser.add_argument("--max-side", type=int, default=3000)
    args = parser.parse_args()
    if args.review_document and (not args.resume_report or args.rebuild_document):
        parser.error("复核验收必须复用已有测试报告，且不能同时重建")
    if not args.execute:
        parser.error("需要 --execute；将创建账号并写入真实测试资料")
    if not args.existing_server:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", args.port))
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(EDU_RAG_OCR_ENABLED="true", EDU_RAG_OCR_BACKEND="tesseract_js",
               EDU_RAG_OCR_JS_MODULE_DIR=str(Path(args.js_module).resolve()),
               EDU_RAG_OCR_TESSDATA_DIR=str(args.tessdata_dir.resolve()),
               EDU_RAG_OCR_LAYOUT_MODE=args.layout_mode,
               EDU_RAG_OCR_MAX_SIDE_PIXELS=str(args.max_side),
               EDU_RAG_OCR_PDFTOPPM_COMMAND=shutil.which("pdftoppm"),
               EDU_RAG_OCR_NODE_COMMAND=shutil.which("node"))
    log = (output / "server.log").open("w", encoding="utf-8")
    server = None if args.existing_server else subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", "app:app",
                               "--host", "127.0.0.1", "--port", str(args.port)], cwd=ROOT, env=env,
                              stdout=log, stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    report = {"server_pid": server.pid if server else None, "base_url": f"http://127.0.0.1:{args.port}", "status": "starting"}
    try:
        with httpx.Client(base_url=report["base_url"], timeout=180, trust_env=False) as client:
            for _ in range(90):
                if server is not None and server.poll() is not None:
                    raise RuntimeError("测试服务启动失败，请查看本地日志")
                try:
                    if client.get("/api/openapi.json", timeout=2).status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("测试服务启动超时")
            stores = StoreBundle()
            username, password = "ocr_test_teacher_" + secrets.token_hex(3), secrets.token_urlsafe(24)
            # 一次性验收凭据仅留在内存。后续交互登录请由管理员重置密码。
            previous = json.loads(args.resume_report.read_text(encoding="utf-8")) if args.resume_report else None
            if previous:
                username, user_id = previous["username"], previous["user_id"]
                user = stores.users.get_by_username(username)
                if not username.startswith("ocr_test_teacher_") or user["id"] != user_id or user["role"] != "teacher":
                    raise RuntimeError("仅允许恢复本次专用测试账号")
                with stores.users.engine.begin() as connection:
                    connection.execute(text("UPDATE users SET password_hash=:password WHERE id=:id"),
                                       {"password": hash_password(password), "id": user_id})
            else:
                user_id = stores.users.create(username, hash_password(password), role="teacher", real_name="OCR专项测试教师")
            report.update(username=username, user_id=user_id, password_handling="in_memory_only_reset_required_for_later_login")
            response = client.post("/api/v1/auth/login?portal=teacher", data={"username": username, "password": password})
            response.raise_for_status()
            client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
            if previous:
                version_id = previous["version_id"]
                owned = stores.rag.get_owned_version(version_id, user_id)
                if not owned or owned["status"] != "STAGED":
                    raise RuntimeError("仅恢复未发布测试版本")
            else:
                response = client.post("/api/v1/teacher/rag/versions", json={
                    "version": "ocr-upload-test-" + secrets.token_hex(4),
                    "description": "用户授权的一页扫描教学资料 OCR 验收；仅测试，不激活、不发布。"})
                response.raise_for_status()
                version_id = response.json()["id"]
            report["version_id"] = version_id
            if args.review_document:
                verify_review(client, stores, report, args.review_document, output,
                              args.browser_module, password, args.browser_executable)
                return
            pdf = next((ROOT / "output" / "pdf" / "ocr_teaching_test").glob("*.pdf"))
            if args.rebuild_document:
                document = stores.rag.get_owned_document(args.rebuild_document, user_id)
                if not document or document["kb_version_id"] != version_id or document.get("published_at"):
                    raise RuntimeError("仅重建本人未发布的测试资料")
                response = client.post(f"/api/v1/teacher/rag/documents/{args.rebuild_document}/rebuild")
            else:
                response = client.post("/api/v1/teacher/rag/documents/upload",
                    data={"kb_version_id": str(version_id), "subject": "数学", "grade_band": "初中", "grade": "初二"},
                    files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")})
            report["upload_http_status"] = response.status_code
            if response.status_code != 200:
                raise RuntimeError("上传失败，请检查测试知识库任务记录")
            result = response.json()
            report["upload"] = result
            preview = client.get(f"/api/v1/teacher/rag/documents/{result['document_id']}")
            preview.raise_for_status()
            report["preview"] = preview.json()
            chunks = stores.rag.list_document_chunks(result["document_id"])
            (output / "recognized.txt").write_text("\n\n".join(
                chunk["content"] for chunk in chunks if chunk["chunk_kind"] == "parent"), encoding="utf-8")
            with stores.rag.engine.connect() as connection:
                job = connection.execute(text("SELECT status,metrics_json FROM rag_ingestion_jobs WHERE id=:id"),
                                         {"id": result["job_id"]}).mappings().first()
            report["job"] = dict(job) if job else None
            report["version_status"] = stores.rag.get_owned_version(version_id, user_id)["status"]
            report["status"] = "uploaded_pending_quality_review"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log.close()
    print(json.dumps({key: report.get(key) for key in ("status", "username", "user_id", "version_id", "upload")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
