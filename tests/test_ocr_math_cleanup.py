import pytest
from edu_core.rag.loaders.math_cleanup import normalize_ocr_math


@pytest.mark.parametrize('raw,expected', [
    ('当 k〈 0 时，y 减小。', '当 k< 0 时，y 减小。'),
    ('当 a〉 -2 时', '当 a> -2 时'),
    ('y= 2 X 2+ 1= 5。', 'y= 2 × 2+ 1= 5。'),
    ('z= 3 X 4-2= 10。', 'z= 3 × 4-2= 10。'),
    ('y= 1.5 X 2=3', 'y= 1.5 × 2=3'),
    ('当 x=2 时\n| X | 0 | 1 |\n| y | 1 | 3 |', '当 x=2 时\n| x | 0 | 1 |\n| y | 1 | 3 |'),
    ('一次函数:从表达式到图像', '一次函数：从表达式到图像'),
    ('式为y=kx+b, 其中k、b是常数', '式为y=kx+b， 其中k、b是常数'),
    ('k<0时,y 随 x 增大;k>0时,y 随 x 减小', 'k<0时，y 随 x 增大；k>0时，y 随 x 减小'),
    ('这个说法对吗? 请依据资料说明!', '这个说法对吗？ 请依据资料说明！'),
    ('仅用于 0 CR 验证', '仅用于 O CR 验证'),
    ('编号: 0cr 核对要点', '编号： ocr 核对要点'),
])
def test_constrained_math_repair(raw, expected):
    result, changes = normalize_ocr_math(raw)
    assert result == expected and changes
    assert normalize_ocr_math(result) == (result, [])


@pytest.mark.parametrize('text', [
    '向量〈0, 1〉', '阅读〈数学〉', 'x〈0〉', 'x < 0',
    '矩阵 X', '2 X 2', 'y=2 X 2+1=6', 'y=2 X a+1=5', 'y=2X=4',
    'X=2\n| X | 0 | 1 |', '| X | 0 | 1 |',
    'y=2 X 2/0=4', 'y=__import__("os")=1',
    'y=f(x), y=2x+1', '坐标 (1, 2) 与 (3, 4)',
    '当 k> 0 时', '样本编号 20260926-01', '向量 x012y135 取值',
])
def test_ambiguous_or_nonmath_content_is_unchanged(text):
    assert normalize_ocr_math(text) == (text, [])
