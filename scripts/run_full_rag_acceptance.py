"""Isolated real HTTP/browser acceptance with its own MySQL DB and Milvus collection."""
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
from sqlalchemy import create_engine, text
from edu_core.config.settings import Settings
from edu_core.governance.model_versions import ModelVersionManager
from edu_core.security.auth import hash_password
from edu_core.storage.bootstrap import apply_pending_migrations
from edu_core.storage.stores import StoreBundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=7863)
    parser.add_argument('--node-modules', type=Path, required=True)
    parser.add_argument('--chromium', required=True)
    args = parser.parse_args()
    if not args.execute or args.output.exists():
        parser.error('需要 --execute 和新的输出目录')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    original = StoreBundle()
    before = original.rag.get_active_version()
    model = original.model_versions.get_active()
    suffix = secrets.token_hex(5)
    db = 'edu_rag_acceptance_' + suffix
    collection = 'edu_rag_acceptance_' + suffix
    settings = Settings(mysql_db=db)
    args.output.mkdir(parents=True)
    apply_pending_migrations(settings)
    # StoreBundle's default engine is process-global. Explicitly inject a fresh
    # engine when original and acceptance databases coexist in this runner.
    stores = StoreBundle(engine=create_engine(settings.mysql_url, pool_pre_ping=True))
    with stores.engine.connect() as conn:
        if conn.execute(text('SELECT DATABASE()')).scalar_one() != db:
            raise RuntimeError('隔离数据库连接校验失败')
    manager = ModelVersionManager(stores, settings)
    manager.register_version(model['version'])
    manager.activate_version(model['version'])
    users = {}
    for name, role, band, grade in [('teacher', 'teacher', None, None), ('student', 'student', '初中', '初二'),
                                   ('other', 'student', '高中', '高三')]:
        password = secrets.token_urlsafe(24)
        username = 'acceptance_' + name
        uid = stores.users.create(username, hash_password(password), role=role, grade_band=band, grade=grade)
        users[name] = {'username': username, 'password': password, 'id': uid}
    env = os.environ.copy()
    env.update(EDU_MYSQL_DB=db, EDU_RAG_DOCUMENTS_COLLECTION=collection,
        EDU_MILVUS_COLLECTION=collection + '_dedup', EDU_RAG_QUESTIONS_COLLECTION=collection + '_questions',
        EDU_RAG_FAQ_COLLECTION=collection + '_faq', EDU_RAG_UPLOAD_DIR=str((args.output / 'uploads').resolve()),
        EDU_JWT_SECRET=secrets.token_urlsafe(40), EDU_ADMIN_BOOTSTRAP_PASSWORD='', EDU_AUTH_DISABLED='false',
        EDU_RAG_ENABLED='true', EDU_RAG_GROUNDING_CHECK_ENABLED='true', EDU_RAG_NATIVE_STREAM_ENABLED='true',
        EDU_RAG_OCR_ENABLED='true', EDU_RAG_OCR_BACKEND='tesseract_js', EDU_RAG_OCR_LAYOUT_MODE='lines_tables',
        EDU_RAG_OCR_MAX_SIDE_PIXELS='2339', EDU_RAG_OCR_TESSDATA_DIR=str(ROOT / '.ocr_models/tessdata-4.1.0'),
        EDU_RAG_OCR_JS_MODULE_DIR=str(args.node_modules / 'tesseract.js'),
        EDU_RAG_OCR_NODE_COMMAND=shutil.which('node'), EDU_RAG_OCR_PDFTOPPM_COMMAND=shutil.which('pdftoppm'),
        CUDA_VISIBLE_DEVICES='')
    with (args.output / 'server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'uvicorn', 'app:app',
            '--host', '127.0.0.1', '--port', str(args.port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    report = {'database': db, 'collection': collection, 'server_pid': server.pid,
              'base_url': f'http://127.0.0.1:{args.port}', 'active_original_before': (before or {}).get('id'),
              'status': 'starting', 'credentials': 'memory_only'}
    try:
        with httpx.Client(trust_env=False, timeout=2) as client:
            for _ in range(120):
                if server.poll() is not None:
                    raise RuntimeError('隔离服务启动失败')
                try:
                    if client.get(report['base_url'] + '/api/openapi.json').status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('隔离服务启动超时')
        payload = {'base_url': report['base_url'], 'users': users, 'chromium': args.chromium,
            'output': str(args.output.resolve()),
            'pdf': str(ROOT / 'output/pdf/ocr_teaching_test/一次函数教学资料_扫描测试.pdf'),
            'corrected_text': (ROOT / 'output/ocr_corrected_publication/一次函数教学资料_校对版.md').read_text(encoding='utf-8')}
        browser = subprocess.run([shutil.which('node'), str(ROOT / 'scripts/full_rag_acceptance.cjs'),
                                  str(args.node_modules / 'playwright')],
            input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, encoding='utf-8', timeout=420,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        browser_report = args.output / 'browser_report.json'
        if browser_report.exists():
            report['browser'] = json.loads(browser_report.read_text(encoding='utf-8'))
        if browser.returncode:
            raise RuntimeError('浏览器验收未完成，检查 browser_report.json 的阶段记录')
        report['status'] = 'passed'
    finally:
        after = original.rag.get_active_version()
        report['active_original_after'] = (after or {}).get('id')
        report['original_unchanged'] = report['active_original_after'] == report['active_original_before']
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'database': db, 'original_unchanged': report['original_unchanged']}))


if __name__ == '__main__':
    main()
