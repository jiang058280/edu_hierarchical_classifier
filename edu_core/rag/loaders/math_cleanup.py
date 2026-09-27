"""Conservative OCR symbol repairs with provenance; never infer a missing answer."""
import ast
from fractions import Fraction
import operator
import re


def _number_expression(expression: str) -> Fraction:
    if len(expression) > 120:
        raise ValueError('expression too long')
    operations = {ast.Add: operator.add, ast.Sub: operator.sub,
                  ast.Mult: operator.mul, ast.Div: operator.truediv}
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Fraction(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp) and type(node.op) in operations:
            return operations[type(node.op)](visit(node.left), visit(node.right))
        raise ValueError('not numeric arithmetic')
    return visit(ast.parse(expression.strip(), mode='eval').body)


def normalize_ocr_math(raw: str) -> tuple[str, list[dict]]:
    """Only local, unambiguous conventions; return every replacement for review."""
    edits = []
    def replace(pattern, transform, rule, value):
        def apply(match):
            before = match.group(0)
            after = transform(match)
            if before != after:
                edits.append({'rule': rule, 'before': before, 'after': after})
            return after
        return re.sub(pattern, apply, value)
    text = raw
    # Chinese temporal/comparison punctuation prevents changing paired angle brackets.
    text = replace(r'\b[A-Za-z]\s*[〈〉]\s*[+-]?\d+(?:\.\d+)?(?=\s*(?:时|[，,；;。]|$))',
                   lambda m: m[0].replace('〈', '<').replace('〉', '>'), 'comparison_glyph', text)
    # OCR 常把中文句读输出成半角标点；仅当紧邻（跳过空白）CJK 字符时恢复全角，
    # 纯公式/代码中的半角（前后均为字母数字或空格）保持原样。
    full_width = {',': '，', ';': '；', ':': '：', '?': '？', '!': '！'}
    cjk = r'[\u4e00-\u9fff]'

    def restore_punctuation(match):
        source = match.string
        index_start, index_end = match.start(), match.end()

        def neighbor(steps: int, direction: int) -> str:
            index = index_start + direction if direction < 0 else index_end - 1 + direction
            for _ in range(3):
                index += direction
                if index < 0 or index >= len(source) or source[index] in '\r\n':
                    return ''
                if not source[index].isspace():
                    return source[index]
            return ''

        glyph = match[0]
        if re.match(cjk, neighbor(0, -1) or ' ') or re.match(cjk, neighbor(0, 1) or ' '):
            return full_width[glyph]
        return glyph

    text = replace(r'[,;:?!]', restore_punctuation, 'cjk_adjacent_punctuation', text)
    # 字母 O 在 CJK/全角标点之后（允许间隔空格，OCR 引擎常逐字输出）被误读为数字 0，
    # 且跳过空格后紧跟两个字母；数字串中的 0（如 20260926）与比较运算的 0（如 k> 0）不受影响。
    cjk_or_punct = re.compile(r'[\u4e00-\u9fff:：;；,，、]')

    def restore_letter_o(match):
        source = match.string
        start, end = match.start(), match.end()
        index = start - 1
        before = ''
        for _ in range(3):
            if index < 0:
                break
            ch = source[index]
            if ch.isspace():
                index -= 1
                continue
            before = ch
            break
        if not cjk_or_punct.match(before or ' '):
            return match[0]
        after = source[end:end + 4].lstrip(' \u3000')[:2]
        if len(after) == 2 and after.isalpha():
            return 'O' if after.isupper() else 'o'
        return match[0]

    text = replace(r'0', restore_letter_o, 'cjk_context_letter_o', text)
    # Only a closed numeric equality containing an OCR X is eligible. Validate its
    # value first; neither variable expressions nor wrong arithmetic are rewritten.
    def multiply(match):
        before = match[0]
        expression, result = before.rsplit('=', 1)
        try:
            if _number_expression(expression.replace('X', '*').replace('×', '*')) == _number_expression(result.strip()):
                return before.replace('X', '×')
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
            pass
        return before
    text = replace(r'(?<==)\s*[-+]?\d[\d. ()+*/×X-]*X[\d. ()+*/×X-]*=\s*[-+]?\d+(?:\.\d+)?',
                   multiply, 'verified_numeric_multiplication', text)
    # Case is recovered only from an explicit lowercase variable assignment.
    if re.search(r'\bx\s*=', text) and not re.search(r'\bX\s*=', text):
        text = replace(r'(?m)^\|\s*X\s*\|(?=\s*[-+]?\d)',
                       lambda m: m[0].replace('X', 'x'), 'table_variable_case', text)
    return text, edits
