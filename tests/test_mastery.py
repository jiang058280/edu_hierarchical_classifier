from edu_core.application.mastery import profile_for_records

def test_mastery_rewards_recent_and_redo_success_but_penalizes_streaks():
    good=profile_for_records([{'is_correct':False,'source':'assignment'},{'is_correct':True,'source':'wrong_redo'}]*4)
    bad=profile_for_records([{'is_correct':False,'source':'assignment'}]*5)
    assert good['mastery_score'] > bad['mastery_score']
    assert bad['consecutive_wrong'] == 5
    assert good['redo_success_rate'] == 1.0


def test_mastery_snapshot_time_is_json_safe():
    from datetime import datetime
    import json
    profile = profile_for_records([{'is_correct': None, 'source': 'assignment', 'created_at': datetime(2026, 1, 2, 3, 4)}])
    assert profile['last_practiced_at'] == '2026-01-02 03:04:00'
    json.dumps(profile)
