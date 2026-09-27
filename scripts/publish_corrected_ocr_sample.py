"""Publish the explicitly authorized, visually proofread test transcript via HTTP.

Does not approve the inaccurate PDF, activate a KB version, or change OCR output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from pymilvus import MilvusClient
from sqlalchemy import text

from edu_core.config.settings import get_settings
from edu_core.security.auth import hash_password
from edu_core.storage.stores import StoreBundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume', action='store_true', help='核对已有失败记录后，仅继续验证和发布，不再次上传')
    args = parser.parse_args()
    if not args.execute:
        parser.error('需要 --execute：将上传校对版、记录校对审计并发布至未激活测试版本')
    stores, settings = StoreBundle(), get_settings()
    output = ROOT / 'output' / 'ocr_corrected_publication'
    report_path = output / 'report.json'
    resume = json.loads(report_path.read_text(encoding='utf-8')) if args.resume and report_path.exists() else None
    if report_path.exists() and not resume:
        raise RuntimeError('已有执行记录，请先核对结果，不自动重复发布')
    if args.resume and (not resume or resume['status'] != 'failed_requires_inspection' or not resume.get('upload')):
        raise RuntimeError('没有可恢复的已上传失败记录')
    previous = json.loads((ROOT / 'output/ocr_repaired_upload_20260926/report.json').read_text(encoding='utf-8'))
    owner, version_id, original_id = previous['user_id'], previous['version_id'], previous['upload']['document_id']
    original = stores.rag.get_owned_document(original_id, owner)
    version = stores.rag.get_owned_version(version_id, owner)
    active = stores.rag.get_active_version()
    active_id = active['id'] if active else None
    if (not original or original['status'] != 'PROCESSED' or original['kb_version_id'] != version_id
            or not version or version['status'] != 'STAGED' or active_id == version_id):
        raise RuntimeError('仅操作未发布原资料及未激活测试版本')
    source_path = settings.abs_path(original['storage_key']).resolve()
    if settings.abs_path(settings.rag_upload_dir).resolve() not in source_path.parents:
        raise RuntimeError('原件存储位置不合法')
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if source_hash != original['content_hash']:
        raise RuntimeError('原扫描件已变化，需重新校对')
    corrected = output / '一次函数教学资料_校对版.md'
    content = corrected.read_bytes()
    expected = ['k < 0', '2 × 2 + 1 = 5', '| x | 0 | 1 | 2 |', '| y | 1 | 3 | 5 |',
                'y = 3x - 2', 'y = -2x + 4', 'OCR-TEACHING-20260926-01']
    if not all(item in content.decode('utf-8') for item in expected):
        raise RuntimeError('校对文件缺少已核对的关键内容')
    user = stores.users.get_by_username(previous['username'])
    if not user or user['id'] != owner or user['role'] != 'teacher' or not user['username'].startswith('ocr_test_teacher_'):
        raise RuntimeError('不是原专用测试教师')
    report = {'status': 'starting', 'original_document_id': original_id, 'version_id': version_id,
              'active_version_before': active_id, 'source_sha256': source_hash,
              'corrected_sha256': hashlib.sha256(content).hexdigest(),
              'review_method': 'assistant_visual_comparison_with_original_scan_user_authorized',
              'automatic_ocr_quality_passed': False}
    if resume:
        if resume['corrected_sha256'] != report['corrected_sha256'] or resume['source_sha256'] != source_hash:
            raise RuntimeError('文件变化，不允许复用上次入库记录')
        report['previous_attempt'] = resume
    try:
        password = secrets.token_urlsafe(24)
        with stores.users.engine.begin() as conn:
            conn.execute(text('UPDATE users SET password_hash=:password WHERE id=:id'),
                         {'password': hash_password(password), 'id': owner})
        with httpx.Client(base_url='http://127.0.0.1:7862', timeout=180, trust_env=False) as client:
            response = client.post('/api/v1/auth/login?portal=teacher', data={'username': user['username'], 'password': password})
            response.raise_for_status()
            client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
            if resume:
                result = resume['upload']
            else:
                response = client.post('/api/v1/teacher/rag/documents/upload',
                    data={'kb_version_id': str(version_id), 'subject': '数学', 'grade_band': '初中', 'grade': '初二'},
                    files={'file': (corrected.name, content, 'text/markdown')})
                response.raise_for_status()
                result = response.json()
            report['upload'] = result
            doc_id = result['document_id']
            doc = stores.rag.get_owned_document(doc_id, owner)
            if (not doc or doc['kb_version_id'] != version_id or doc['status'] != 'PROCESSED'
                    or doc['content_hash'] != report['corrected_sha256']):
                raise RuntimeError('校对版入库状态或版本不符')
            chunks = stores.rag.list_document_chunks(doc_id)
            parents = '\n'.join(c['content'] for c in chunks if c['chunk_kind'] == 'parent')
            if not all(item in parents for item in expected):
                raise RuntimeError('入库正文与校对内容不符，拒绝发布')
            children = [c for c in chunks if c['chunk_kind'] == 'child']
            index = MilvusClient(uri=settings.milvus_uri)
            try:
                rows = index.get(collection_name=settings.rag_documents_collection,
                                 ids=[c['id'] for c in children], output_fields=['id', 'document_id', 'content_hash'],
                                 consistency_level='Strong')
                if {(r['id'], r['document_id'], r['content_hash']) for r in rows} != {
                    (c['id'], doc_id, c['content_hash']) for c in children} or not children:
                    raise RuntimeError('校对版向量记录不完整，拒绝发布')
            finally:
                index.close()
            stores.audit.insert(action='proofread_ocr_transcript', user_id=owner, username=user['username'],
                resource=f'rag/documents/{doc_id}', detail={
                    'source_document_id': original_id, 'source_sha256': source_hash,
                    'corrected_sha256': report['corrected_sha256'], 'review_method': report['review_method'],
                    'checked': expected, 'note': '用户授权助手对照原扫描页校对文本后发布；不批准原 OCR 记录'},
                client_ip='127.0.0.1')
            response = client.post(f'/api/v1/teacher/rag/documents/{doc_id}/publication', json={'published': True})
            report['publication_http_status'] = response.status_code
            response.raise_for_status()
            after = client.get(f'/api/v1/teacher/rag/documents/{doc_id}')
            after.raise_for_status()
            report['preview'] = after.json()
            final_chunks = stores.rag.list_document_chunks(doc_id)
            if after.json()['document']['status'] != 'PUBLISHED' or any(c['status'] != 'PUBLISHED' for c in final_chunks):
                raise RuntimeError('资料或分块发布状态不符')
            report['verified_vector_ids'] = [c['id'] for c in children]
            report['original_status'] = stores.rag.get_document(original_id)['status']
            report['version_status'] = stores.rag.get_owned_version(version_id, owner)['status']
            after_active = stores.rag.get_active_version()
            report['active_version_after'] = after_active['id'] if after_active else None
            if report['active_version_after'] != active_id or report['version_status'] != 'STAGED':
                raise RuntimeError('激活版本发生变化，需要核对')
            report['status'] = 'corrected_transcript_published_verified'
    except Exception as exc:
        report.update(status='failed_requires_inspection', error_type=type(exc).__name__)
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['status', 'upload', 'publication_http_status', 'version_status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
