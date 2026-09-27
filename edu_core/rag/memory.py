"""保守的追问主题补全：只用已完成轮次的用户问题，不复用旧回答证据。"""

import re


def completed_questions(messages: list[dict]) -> list[str]:
    questions = []
    pending = None
    for message in messages:
        if message.get("role") == "user":
            if pending is not None:
                # 连续用户消息可能来自失败或并发请求，不猜测回答归属。
                return []
            pending = message.get("content", "")
        elif message.get("role") == "assistant":
            if pending and not message.get("refused"):
                questions.append(pending[:400])
            else:
                questions = []
            pending = None
        else:
            questions, pending = [], None
    return [] if pending is not None else questions[-3:]


def contextual_query(query: str, questions: list[str]) -> str:
    """有限规则识别显式追问；独立新问题不自动混入上文。"""
    if not questions or not re.search(r"(上面|上述|刚才|前面|上一[步轮题]|这[道个种]|它|继续|再举|那.+呢[？?]?$)", query):
        return query
    if re.search(r"(换个话题|换一个话题|不讨论|不聊|新问题)", query):
        return query
    context = "；".join(q[:400] for q in questions[-3:] if isinstance(q, str) and q.strip())
    if not context:
        return query
    prefix = f"此前问题（仅帮助理解指代，不是事实依据）：{context}\n当前追问："
    # 保留本轮问题全部内容，历史只占剩余预算。
    budget = max(0, 2000 - len(query))
    return prefix[:budget] + query if budget >= len(prefix) else query
