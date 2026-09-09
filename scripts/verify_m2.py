"""M2 本地真实接口与浏览器验收；临时账号和业务数据在 finally 中定向清理。

先启动应用，设置 NODE_PATH 指向含 playwright 的 node_modules 后运行本脚本。
仅允许回环地址，禁止对共享生产库使用；账号密码仅经 stdin 传给浏览器进程。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from sqlalchemy import bindparam, text  # noqa: E402

from edu_core.security.auth import hash_password  # noqa: E402
from edu_core.storage.stores import StoreBundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:7860')
    parser.add_argument('--node', default='node')
    args = parser.parse_args()
    if urlparse(args.base_url).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('验收只允许本机回环服务')
    stores = StoreBundle()
    prefix = 'm2qa_' + secrets.token_hex(5)
    users: dict[str, dict] = {}
    checks: list[str] = []
    out = ROOT / 'reports' / 'verification' / prefix
    out.mkdir(parents=True)
    report = {'verified_at': datetime.now().isoformat(), 'all_passed': False,
              'checks': checks, 'browser': None, 'fixtures_removed': False}

    def call(role, method, path, expected=200, **kwargs):
        headers = {'Authorization': 'Bearer ' + users[role]['token']} if role else {}
        response = httpx.request(method, args.base_url + '/api/v1' + path,
                                 headers=headers, timeout=30, **kwargs)
        assert response.status_code == expected, (
            f'{method} {path}: expected {expected}, got {response.status_code}; '
            f'{response.text[:180]}')
        return response.json()

    def login(role):
        data = call(None, 'POST', '/auth/login', data={
            'username': users[role]['username'], 'password': users[role]['password']})
        users[role]['token'] = data['access_token']

    def cleanup():
        if not users:
            return
        ids = [u['id'] for u in users.values()]
        with stores.engine.begin() as conn:
            actual = conn.execute(text('SELECT id, username FROM users WHERE id IN :ids')
                                  .bindparams(bindparam('ids', expanding=True)),
                                  {'ids': ids}).mappings().all()
            expected = {u['id']: u['username'] for u in users.values()}
            assert {r['id']: r['username'] for r in actual} == expected, '停止清理：账号归属不符'
            # 只删除本次随机账号创建的对象，不按宽泛标题或全表清理。
            statements = [
                'DELETE FROM answer_records WHERE student_id IN :ids',
                'DELETE FROM submissions WHERE student_id IN :ids',
                'DELETE FROM assignments WHERE created_by IN :ids',
                'DELETE FROM paper_questions WHERE paper_id IN '
                '(SELECT id FROM papers WHERE created_by IN :ids)',
                'DELETE FROM papers WHERE created_by IN :ids',
                'DELETE FROM questions WHERE created_by IN :ids',
                'DELETE FROM classes WHERE created_by IN :ids',
                'DELETE FROM audit_logs WHERE user_id IN :ids',
                'DELETE FROM users WHERE id IN :ids',
            ]
            for statement in statements:
                conn.execute(text(statement).bindparams(bindparam('ids', expanding=True)),
                             {'ids': ids})

    try:
        call(None, 'GET', '/student/assignments', expected=401)
        admin = {'username': prefix + '_admin', 'password': secrets.token_urlsafe(24)}
        admin['id'] = stores.users.create(admin['username'], hash_password(admin['password']),
                                         role='admin', real_name='验收管理员')
        users['admin'] = admin
        login('admin')
        for name, role in [('teacher', 'teacher'), ('other_teacher', 'teacher'),
                           ('student', 'student'), ('peer', 'student'), ('outsider', 'student')]:
            account = {'username': prefix + '_' + name, 'password': secrets.token_urlsafe(24)}
            account['id'] = call('admin', 'POST', '/auth/users', json={**account, 'role': role})['id']
            users[name] = account
            login(name)
        checks.append('管理员建教师/学生账号，真实密码登录，匿名访问401')
        assert call('student', 'GET', '/student/assignments')['items'] == []
        call('student', 'GET', '/teacher/assignments', expected=403)
        call('teacher', 'GET', '/student/assignments', expected=403)
        cls = call('teacher', 'POST', '/teacher/classes', json={
            'name': '初三数学 · 联调班', 'grade_band': '初中', 'grade': '初三'})
        for name in ('student', 'peer'):
            call(name, 'POST', '/student/join-class', json={'invite_code': cls['invite_code']})
        checks.append('未入班空列表、师生角色隔离、邀请码入班')

        questions = []
        for index in range(20):
            if index < 5:
                kind, answer = '选择题', 'A'
                content = f'第 {index + 1} 题：一次函数 y=2x+1，当 x=1 时，y 的值是？'
                opts = [{'key': k, 'text': v} for k, v in zip('ABCD', ['3', '2', '1', '0'])]
                if index == 0:
                    content += '\nA. 3    B. 2    C. 1    D. 0'
                    opts = None
            elif index < 10:
                kind, answer = '多选题', 'AC'
                content = f'第 {index + 1} 题：关于一次函数 y=2x+1，以下结论正确的是？'
                opts = [{'key': k, 'text': v} for k, v in zip(
                    'ABCD', ['斜率为2', '经过原点', '截距为1', 'y随x增大而减小'])]
            elif index < 15:
                kind, answer = '判断题', '正确' if index < 14 else '错误'
                content = f'第 {index + 1} 题：一次函数 y=2x+1 的函数值随 x 的增大而'
                content += '增大。' if index < 14 else '减小。'
                opts = None
            else:
                kind, answer, opts = '解答题', '设x=1，代入y=2x+1，得y=3。', None
                content = f'第 {index + 1} 题：请写出求一次函数 y=2x+1 在 x=1 时函数值的过程。'
            payload = {'text': content + f'\n[验收样本 {prefix}]', 'subject': '数学',
                       'question_type': kind, 'knowledge_point': '一次函数',
                       'grade_band': '初中', 'grade': '初三', 'answer': answer,
                       'analysis': '一次函数 y=kx+b 中，k 决定增减性，b 是纵轴截距；代入自变量求函数值。',
                       'options': opts, 'difficulty': 2, 'status': 'published'}
            qid = call('teacher', 'POST', '/teacher/questions', json=payload)['id']
            questions.append({'id': qid, 'question_type': kind, 'options': opts, 'answer': answer})
        # 复核草稿和结构化选项编辑（在组卷锁定之前）。
        qid = questions[1]['id']
        call('teacher', 'PUT', f'/teacher/questions/{qid}', json={'options': []})
        assert call('teacher', 'GET', f'/teacher/questions/{qid}')['options'] is None
        call('teacher', 'PUT', f'/teacher/questions/{qid}', json={'options': questions[1]['options']})
        draft = call('teacher', 'POST', '/teacher/questions', json={
            'text': f'验收草稿题目不应进入已发布库存 {prefix}', 'subject': '数学',
            'question_type': '解答题', 'status': 'draft'})['id']
        assert call('teacher', 'GET', f'/teacher/questions/{draft}')['status'] == 'draft'
        paper = call('teacher', 'POST', '/teacher/papers', json={
            'title': '一次函数 · 单元巩固（20题）', 'subject': '数学', 'grade_band': '初中',
            'question_ids': [q['id'] for q in questions]})['id']
        call('teacher', 'PUT', f'/teacher/questions/{qid}', expected=400, json={'answer': 'B'})
        call('teacher', 'DELETE', f'/teacher/questions/{qid}', expected=400)
        for path in (f'/teacher/papers/{paper}', f'/teacher/papers/{paper}/export'):
            call('other_teacher', 'GET', path, expected=403)
        call('other_teacher', 'DELETE', f'/teacher/papers/{paper}', expected=403)
        checks.append('20题建卷、草稿状态、选项编辑、原题引用保护、试卷归属')

        config = {'base_url': args.base_url, 'teacher': {k: users['teacher'][k] for k in ('username', 'password')},
                  'student': {k: users['student'][k] for k in ('username', 'password')},
                  'paper_id': paper, 'class_id': cls['id'], 'title': '一次函数 · 本周巩固练习',
                  'questions': questions, 'output_dir': str(out)}
        proc = subprocess.run([args.node, str(ROOT / 'scripts' / 'verify_m2_browser.cjs')],
                              input=json.dumps(config), capture_output=True, encoding='utf-8',
                              timeout=240, cwd=ROOT)
        if proc.returncode:
            raise AssertionError('浏览器验收失败：' + proc.stderr[-1500:])
        browser = json.loads(proc.stdout)
        report['browser'] = browser
        aid = browser['assignment_id']
        sid = browser['submission_id']
        result = call('student', 'GET', f'/student/assignments/{aid}/result')
        submitted_at = datetime.fromisoformat(result['submission']['submitted_at'])
        assert abs((datetime.now() - submitted_at).total_seconds()) < 120
        alias = httpx.get(args.base_url + '/api/student/assignments', headers={
            'Authorization': 'Bearer ' + users['student']['token']}, timeout=30)
        assert alias.status_code == 200 and aid in [a['id'] for a in alias.json()['items']]
        checks.append('浏览器20题作答、93.3自动分、教师88.5批改、学生查看结果')
        checks.append('提交时间与应用本地时间一致、/api与/api/v1双前缀兼容')
        call('outsider', 'GET', f'/student/assignments/{aid}', expected=403)
        call('outsider', 'GET', f'/student/assignments/{aid}/result', expected=403)
        call('peer', 'GET', f'/student/assignments/{aid}/result', expected=409)
        hidden = call('peer', 'GET', f'/student/assignments/{aid}')
        assert all('answer' not in q and 'analysis' not in q for q in hidden['questions'])
        call('other_teacher', 'GET', f'/teacher/assignments/{aid}/submissions', expected=403)
        call('other_teacher', 'POST', f'/teacher/submissions/{sid}/check', expected=403,
             json={'final_score': 0})
        call('teacher', 'DELETE', f'/teacher/papers/{paper}', expected=400)
        call('student', 'POST', f'/student/assignments/{aid}/submit', expected=409,
             json={'answers': []})
        checks.append('作业越权、提交前答案隐藏、未提交结果409、重复提交409、已发布试卷防删')

        def concurrent_submit(_):
            return httpx.post(args.base_url + f'/api/v1/student/assignments/{aid}/submit',
                              headers={'Authorization': 'Bearer ' + users['peer']['token']},
                              json={'answers': []}, timeout=30).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(concurrent_submit, range(2))) == [200, 409]
        roster = call('teacher', 'GET', f'/teacher/assignments/{aid}/submissions')
        assert roster['completed'] == 2 and roster['total'] == 2
        assert all(len(s['answers']) == 20 for s in roster['students'])
        checks.append('真实MySQL并发首次交卷：一次200一次409，每人恰好20条答案')

        deadline = call('teacher', 'POST', '/teacher/assignments', json={
            'paper_id': paper, 'class_id': cls['id'], 'title': '截止控制验收',
            'due_at': (datetime.now() + timedelta(hours=1)).isoformat()})['id']
        with stores.engine.begin() as conn:
            conn.execute(text('UPDATE assignments SET due_at = NOW() - INTERVAL 1 SECOND '
                              'WHERE id = :i AND created_by = :u'),
                         {'i': deadline, 'u': users['teacher']['id']})
        call('peer', 'POST', f'/student/assignments/{deadline}/submit', expected=409,
             json={'answers': []})
        transfer = call('teacher', 'POST', '/teacher/classes', json={
            'name': '转班历史验收', 'grade_band': '初中', 'grade': '初三'})
        call('student', 'POST', '/student/join-class', json={'invite_code': transfer['invite_code']})
        assert aid in [a['id'] for a in call('student', 'GET', '/student/assignments')['items']]
        assert call('student', 'GET', f'/student/assignments/{aid}/result')['submission']['final_score'] == 88.5
        checks.append('截止后409、转班保留本人已提交成绩历史')
        report['all_passed'] = True
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        try:
            cleanup()
            report['fixtures_removed'] = True
        except Exception as exc:
            report['all_passed'] = False
            report['cleanup_error'] = str(exc)
        (out / 'm2_acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (out.parent / 'm2_acceptance_latest.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print('报告：', out / 'm2_acceptance.json')
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
