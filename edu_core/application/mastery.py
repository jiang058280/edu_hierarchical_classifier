"""R3.2 可解释掌握度规则；不依赖大模型。"""
from __future__ import annotations

def clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)

def profile_for_records(records: list[dict]) -> dict:
    scored = [r for r in records if r.get('is_correct') is not None]
    attempts = len(scored)
    correct = sum(bool(r['is_correct']) for r in scored)
    rate = correct / attempts if attempts else 0.0
    recent = scored[-5:]
    recent_rate = sum(bool(r['is_correct']) for r in recent) / len(recent) if recent else 0.0
    consecutive = 0
    for row in reversed(scored):
        if row['is_correct']:
            break
        consecutive += 1
    redo = [r for r in scored if r.get('source') == 'wrong_redo']
    redo_rate = sum(bool(r['is_correct']) for r in redo) / len(redo) if redo else None
    score = clamp(.60 * rate + .25 * recent_rate + .15 * (redo_rate if redo_rate is not None else rate) - .08 * min(consecutive, 3))
    confidence = clamp(.7 * min(attempts / 8, 1) + .3 * min(len(recent) / 5, 1))
    last_practiced = records[-1].get("created_at") if records else None
    if hasattr(last_practiced, "isoformat"):
        last_practiced = last_practiced.isoformat(sep=" ")
    return {'attempt_count':attempts,'correct_rate':round(rate,4),'recent_correct_rate':round(recent_rate,4),'mastery_score':score,'profile_confidence':confidence,'consecutive_wrong':consecutive,'redo_success_rate':None if redo_rate is None else round(redo_rate,4),'recommended_difficulty':max(1,min(5,1+int(score*4))),'last_practiced_at':last_practiced}
