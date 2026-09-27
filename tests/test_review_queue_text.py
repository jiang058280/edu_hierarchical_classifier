from edu_core.storage.stores import is_unreadable_text


def test_rejects_irrecoverable_question_mark_history():
    assert is_unreadable_text("????????????,????? ( ) A. ????? B. ?????")
    assert is_unreadable_text("���???????")


def test_keeps_normal_chinese_and_english_questions():
    assert not is_unreadable_text("下列关于三角函数的说法正确的是？")
    assert not is_unreadable_text("Which answer is correct? A. One B. Two")
    assert not is_unreadable_text("答案？")
