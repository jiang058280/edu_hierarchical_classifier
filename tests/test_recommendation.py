from edu_core.application.recommendation import score_candidate

def test_exact_weak_unseen_question_scores_higher():
    profile={'mastery_score':.2,'knowledge_point':'数学::函数','recommended_difficulty':2}
    exact,_=score_candidate(profile,{'id':2,'knowledge_point':'数学::函数','difficulty':2},set())
    seen,_=score_candidate(profile,{'id':2,'knowledge_point':'数学::函数','difficulty':2},{2})
    other,_=score_candidate(profile,{'id':3,'knowledge_point':'数学::几何','difficulty':5},set())
    assert exact > seen and exact > other
