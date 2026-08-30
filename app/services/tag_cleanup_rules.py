"""Rules for one-shot tag cleanup suggestions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_ALLOWED_ACTIONS = {"merge", "rename", "deprecate", "categorize", "keep"}

_SIMPLIFIED_MAP = {
    "內射": "内射",
    "絲襪": "丝袜",
    "顏射": "颜射",
    "調教": "调教",
    "MILF": "熟女",
    "Teacher": "老师",
    "Blowjob": "口交",
}


CATEGORY_L1 = ["演员/角色特征", "性行为/玩法", "剧情/主题", "JAV特有子类"]


def _normalize_tag(raw: str) -> str:
    value = str(raw or "").strip().lstrip("#").casefold()
    return value


def normalize_input_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in tags:
        tag = _normalize_tag(raw)
        if not tag:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def prefer_simplified_tag(raw: str) -> str:
    src = str(raw or "").strip()
    if not src:
        return ""
    if src in _SIMPLIFIED_MAP:
        return _SIMPLIFIED_MAP[src]
    low = src.casefold()
    for k, v in _SIMPLIFIED_MAP.items():
        if k.casefold() == low:
            return v
    return src


def resolve_target_tag(source_tag: str, rules: dict[str, str]) -> str | None:
    key = _normalize_tag(source_tag)
    if not key:
        return None
    target = rules.get(key)
    if not target:
        return None
    return prefer_simplified_tag(target)


def infer_category_l1(tag: str) -> str:
    value = str(tag or "").casefold()
    if any(k in value for k in ["milf", "人妻", "熟女", "老师", "teacher", "巨乳", "贫乳", "asian", "latina", "ebony"]):
        return "演员/角色特征"
    if any(k in value for k in ["口交", "blowjob", "deepthroat", "anal", "肛交", "内射", "creampie", "sm", "调教", "bdsm", "潮吹", "自慰", "footjob", "pov"]):
        return "性行为/玩法"
    if any(k in value for k in ["女仆", "maid", "制服", "nurse", "ol", "孕妇", "丝袜", "催眠", "绿帽", "cuckold", "incest"]):
        return "剧情/主题"
    if any(k in value for k in ["jav", "有码", "无码", "ntr", "痴女", "中出", "骑乘位"]):
        return "JAV特有子类"
    return "剧情/主题"


def infer_category_l2(tag: str, category_l1: str) -> str:
    value = str(tag or "").casefold()
    if category_l1 == "演员/角色特征":
        if any(k in value for k in ["milf", "人妻", "熟女"]):
            return "熟女相关"
        if any(k in value for k in ["巨乳", "贫乳", "big tits", "small tits"]):
            return "身材特征"
        return "角色特征"
    if category_l1 == "性行为/玩法":
        if any(k in value for k in ["口交", "blowjob", "deepthroat", "handjob"]):
            return "口交/手交"
        if any(k in value for k in ["anal", "肛交", "内射", "creampie", "gangbang", "threesome"]):
            return "插入类"
        return "特殊玩法"
    if category_l1 == "JAV特有子类":
        if any(k in value for k in ["有码", "无码"]):
            return "有码/无码"
        return "JAV题材"
    return "角色扮演与主题"


def load_rename_rules_from_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    rules: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        row = line.strip()
        if not row or row.startswith("//"):
            continue
        if "=" not in row:
            continue
        left, right = row.split("=", 1)
        src = _normalize_tag(left)
        tgt = prefer_simplified_tag(str(right).strip().lstrip("#"))
        if not src or not tgt:
            continue
        rules[src] = tgt
    return rules


def clean_suggestions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in items:
        source_tag = _normalize_tag(str(item.get("source_tag") or ""))
        if not source_tag:
            continue
        action = str(item.get("suggested_action") or "keep").strip().lower()
        if action not in _ALLOWED_ACTIONS:
            action = "keep"
        target_tag = _normalize_tag(str(item.get("suggested_target_tag") or ""))
        confidence_raw = item.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except Exception:
            confidence = 0.0
        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0

        if action in {"rename", "merge"}:
            if not target_tag or target_tag == source_tag:
                action = "keep"
                target_tag = ""

        cleaned.append(
            {
                **item,
                "source_tag": source_tag,
                "suggested_action": action,
                "suggested_target_tag": target_tag,
                "confidence": confidence,
            }
        )
    return cleaned
