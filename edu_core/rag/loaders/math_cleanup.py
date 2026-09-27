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
