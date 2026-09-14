"""Build ADR-01's immutable clean benchmark without touching training data."""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime
from difflib import SequenceMatcher
import json
from pathlib import Path
import random
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edu_core.data.dataset import (  # noqa: E402
    dedup, load_raw_samples, merge_small_knowledge, merge_small_types, stratified_split,
)

SEED = 42
TARGET_SIZE = 500
MIN_SIZE = 400


def normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).lower().split())


def read_texts(*paths: Path) -> list[str]:
    values: list[str] = []
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            values.extend(row["text"] for row in csv.DictReader(stream) if row.get("text"))
    return values


def build_block_index(texts: list[str]) -> tuple[set[str], set[str], dict[int, list[tuple[str, Counter[str]]]]]:
    normalized = [normalize(text) for text in texts]
    exact = set(normalized)
    prefixes = {item[:64] for item in normalized}
    by_length: dict[int, list[tuple[str, Counter[str]]]] = defaultdict(list)
    for item in normalized:
        by_length[len(item)].append((item, Counter(item)))
    return exact, prefixes, by_length


def is_near_duplicate(text: str, block_index: tuple[set[str], set[str], dict[int, list[tuple[str, Counter[str]]]]]) -> bool:
    normalized = normalize(text)
    exact, prefixes, by_length = block_index
    if normalized in exact or normalized[:64] in prefixes:
        return True
    # ratio >= .92 is mathematically impossible outside this length interval.
    lower, upper = int(len(normalized) * 0.92), int(len(normalized) / 0.92) + 1
    characters = Counter(normalized)
    for length in range(lower, upper + 1):
        for candidate, candidate_characters in by_length.get(length, []):
            # SequenceMatcher.ratio can never exceed this multiset overlap bound.
            overlap_upper_bound = 2 * sum((characters & candidate_characters).values()) / (len(normalized) + length)
            if overlap_upper_bound < 0.92:
                continue
            if SequenceMatcher(None, normalized, candidate, autojunk=False).ratio() >= 0.92:
                return True
    return False


def stratified_sample(samples: list[dict], target: int, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    groups: dict[str, list[dict]] = defaultdict(list)
    for sample in samples:
        groups[sample["subject"]].append(sample)
    selected: list[dict] = []
    total = len(samples)
    for subject in sorted(groups):
        group = list(groups[subject])
        rng.shuffle(group)
        quota = round(target * len(group) / total)
        selected.extend(group[:quota])
    remaining = [item for group in groups.values() for item in group if item not in selected]
    rng.shuffle(remaining)
    selected.extend(remaining[:max(0, target - len(selected))])
    rng.shuffle(selected)
    return selected[:target]


def build(raw_dir: Path, train_csv: Path, val_csv: Path) -> dict:
    samples = merge_small_types(merge_small_knowledge(dedup(load_raw_samples(raw_dir))))
    _, _, original_test = stratified_split(samples, seed=SEED)
    block_index = build_block_index(read_texts(train_csv, val_csv))
    candidates = [sample for sample in original_test if not is_near_duplicate(sample["text"], block_index)]
    fallback_used = False
    if len(candidates) < MIN_SIZE:
        fallback_used = True
        candidates = [sample for sample in samples if not is_near_duplicate(sample["text"], block_index)]
    picked = stratified_sample(candidates, min(TARGET_SIZE, len(candidates)))
    if len(picked) < MIN_SIZE:
        raise RuntimeError(f"去重后 clean benchmark 仅 {len(picked)} 条，低于最小 {MIN_SIZE} 条")
    return {
        "dataset": "bench_clean",
        "source": "K-12EduBench raw unaugmented samples",
        "purpose": "absolute quality baseline; golden_test_set remains the regression gate",
        "seed": SEED,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filter": "NFKC+whitespace normalization; exact/prefix64/SequenceMatcher>=0.92 vs train.csv and val.csv",
        "raw_deduped": len(samples),
        "original_test_candidates": len(original_test),
        "candidates_after_filter": len(candidates),
        "fallback_to_full_raw_pool": fallback_used,
        "cases": [{
            "text": item["text"], "subject": item["subject"],
            "question_type": item["question_type"], "knowledge_point": item["knowledge_point"],
        } for item in picked],
    }


def main() -> int:
    output = ROOT / "eval_sets" / "bench_clean.json"
    if output.exists():
        raise RuntimeError(f"拒绝覆盖既有基准：{output}")
    result = build(
        ROOT / "data" / "raw" / "k12edubench",
        ROOT / "data" / "processed" / "train.csv",
        ROOT / "data" / "processed" / "val.csv",
    )
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
