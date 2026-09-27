"""小规模授权语料的 BM25 与 RRF；排名分数不作为答案置信度。"""

from collections import Counter
import math
import re
import unicodedata


def tokenize(text: str) -> list[str]:
    tokens = []
    for part in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", unicodedata.normalize("NFKC", text).lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) > 1:
                tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
            else:
                tokens.append(part)
        else:
            tokens.append(part)
    return tokens


def bm25(query: str, documents: list[dict], *, limit: int, k1: float = 1.2, b: float = 0.75) -> list[dict]:
    if limit < 1 or k1 <= 0 or not 0 <= b <= 1:
        raise ValueError("BM25 参数不合法")
    terms = sorted(set(tokenize(query)))
    if not terms or not documents:
        return []
    counts = [Counter(tokenize(doc["content"])) for doc in documents]
    lengths = [sum(count.values()) for count in counts]
    average = sum(lengths) / len(lengths)
    if not average:
        return []
    frequencies = Counter(term for count in counts for term in count if term in terms)
    results = []
    for doc, count, length in zip(documents, counts, lengths):
        score = 0.0
        for term in terms:
            tf = count[term]
            if not tf:
                continue
            idf = math.log1p((len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
        if score > 0:
            results.append({"id": int(doc["id"]), "score": score})
    return sorted(results, key=lambda item: (-item["score"], item["id"]))[:limit]


def reciprocal_rank_fusion(rankings: list[list[int]], *, k: int = 60) -> dict[int, float]:
    if k < 1:
        raise ValueError("RRF k 必须为正数")
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(dict.fromkeys(ranking), 1):
            scores[identifier] = scores.get(identifier, 0.0) + 1 / (k + rank)
    return scores
