from scripts.build_clean_bench import build_block_index, is_near_duplicate, normalize


def test_normalize_and_exact_duplicate():
    index = build_block_index(["Ａ B\nC"])
    assert normalize("Ａ B\nC") == "abc"
    assert is_near_duplicate("a b c", index)


def test_prefix_and_high_similarity_duplicate():
    index = build_block_index(["这是一个用于验证相似度过滤的长文本样例，后半部分允许存在非常小的改动。"])
    assert is_near_duplicate("这是一个用于验证相似度过滤的长文本样例，后半部分允许存在非常小的修改。", index)


def test_unrelated_text_is_not_duplicate():
    index = build_block_index(["一次函数的图像经过原点。"])
    assert not is_near_duplicate("光合作用的场所是叶绿体。", index)
