"""Frozen 64-case engineering regression suite, not a company/human-labelled benchmark."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edu_core.config.settings import get_settings
from edu_core.rag.grounding import review_answer
from edu_core.rag.providers import OpenAICompatibleChatProvider

# Every label is determined by the supplied excerpt, not external world knowledge.
# Each row: evidence, supported paraphrase, contradiction, unsupported addition.
FIXTURES = [
 ('当 k < 0 时，y 随 x 的增大而减小。', 'k 小于 0 时，x 增大，y 减小。', 'k < 0 时 y 随 x 增大而增大。', 'k 叫斜率。'),
 ('y = 2x + 1。当 x = 2 时，y = 5。', '把 x=2 代入得到 y=5。', '当 x=2 时 y=6。', '该函数是偶函数。'),
 ('实验中测得距离 60 米，时间 12 秒；平均速度为 5 米每秒。', '该实验的平均速度是每秒 5 米。', '平均速度为每秒 12 米。', '实验使用了激光测速仪。'),
 ('本次记录水温由 20℃ 升到 30℃，升高 10℃。', '记录中的水温升高了 10℃。', '水温降低了 10℃。', '水的沸点始终是 100℃。'),
 ('细胞膜能控制物质进出细胞。', '细胞膜具有控制物质进出细胞的作用。', '细胞膜不能控制物质进出细胞。', '细胞膜主要由磷脂和蛋白质组成。'),
 ('光合作用能把光能转化为化学能。', '光能通过光合作用转化成化学能。', '光合作用把化学能转化为光能。', '光合作用分为光反应和暗反应。'),
 ('本题规定溶液质量为 100 克，溶质质量为 10 克，质量分数为 10%。', '溶质在该溶液中的质量分数是百分之十。', '质量分数为百分之一。', '该溶液一定呈酸性。'),
 ('实验说明：禁止直接用手接触试剂。', '实验中不可以直接用手接触试剂。', '实验中允许直接用手接触试剂。', '试剂具有剧毒。'),
 ('讲义将 go 的过去式列为 went。', '根据讲义，go 的过去式是 went。', 'go 的过去式是 goed。', 'go 的过去分词是 gone。'),
 ('本题句子为 She is reading，表示正在进行的动作。', 'She is reading 表示动作正在进行。', '该句表示动作已经结束。', 'reading 在这里是名词。'),
 ('本次作业要求周五提交，迟交需要说明原因。', '这次作业的提交时间是周五，迟交须说明。', '无需说明原因也可以迟交。', '迟交会扣除十分。'),
 ('本班测试共 20 道题，每题 5 分，满分 100 分。', '20 道题每题 5 分，该测试满分 100 分。', '该测试满分 120 分。', '及格线是 60 分。'),
 ('资料记载 A 城位于 B 城以北。', 'B 城在 A 城以南。', 'A 城在 B 城以南。', '两城相距 100 千米。'),
 ('统计表中第一组 8 人，第二组 12 人，合计 20 人。', '两组人数之和为 20。', '第一组比第二组多 4 人。', '两组学生年龄都为 14 岁。'),
 ('只有提交申请并得到批准，才能进入实验室。', '进入实验室需要申请获批。', '提交申请后，无需批准即可进入。', '申请应提前三天提交。'),
 ('阅读记录显示：小林读了 30 页，小周读了 20 页。资料未记录用时。', '小林比小周多读了 10 页。', '小周比小林多读了 10 页。', '小林的阅读速度更快。'),
]


def cases():
    result = []
    for index, (context, paraphrase, contradiction, addition) in enumerate(FIXTURES):
        for kind, answer, expected in [('quote', context, True), ('paraphrase', paraphrase, True),
                                       ('contradiction', contradiction, False), ('addition', addition, False)]:
            result.append({'id': f'{index+1:02d}-{kind}', 'split': 'dev' if index % 2 == 0 else 'holdout',
                           'question': '请仅根据资料解释其内容。', 'context': '[1] ' + context,
                           'answer': answer + ' [1]', 'expected_supported': expected})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--split', choices=['dev', 'holdout', 'all'], default='all')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute or args.report.exists():
        parser.error('需要 --execute 和不存在的新报告路径')
    all_cases = cases()
    selected = [c for c in all_cases if args.split == 'all' or c['split'] == args.split]
    settings = get_settings()
    report = {'dataset_kind': 'assistant_authored_engineering_regression',
              'dataset_sha256': hashlib.sha256(json.dumps(all_cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              'criteria': {'unsupported_rejection_rate': 1.0, 'supported_acceptance_rate': .90},
              'cases': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    def run(case):
        start = time.perf_counter()
        verdict = review_answer(OpenAICompatibleChatProvider(settings), query=case['question'],
                                context=case['context'], answer=case['answer'], citation_numbers={1})
        return case | {'verdict': asdict(verdict), 'correct': verdict.supported == case['expected_supported'],
                       'seconds': round(time.perf_counter()-start, 3)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(run, case) for case in selected]):
            report['cases'].append(future.result())
            report['cases'].sort(key=lambda c: c['id'])
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    report['splits'] = {}
    for split in sorted({c['split'] for c in selected}):
        rows = [c for c in report['cases'] if c['split'] == split]
        good, bad = [c for c in rows if c['expected_supported']], [c for c in rows if not c['expected_supported']]
        valid = all(c['verdict']['reason'] not in ('invalid_verdict', 'review_unavailable') for c in rows)
        acceptance = sum(c['correct'] for c in good) / len(good)
        rejection = sum(c['correct'] for c in bad) / len(bad)
        report['splits'][split] = {'supported_acceptance': acceptance, 'unsupported_rejection': rejection,
                                   'valid_responses': valid, 'passed': valid and acceptance >= .9 and rejection == 1}
    report['passed'] = all(s['passed'] for s in report['splits'].values())
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['splits']))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
