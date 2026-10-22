"""L1 敏感词表：Aho-Corasick 多模匹配 + 三级知识库（法规 / 行业 / 企业）。

为什么需要它
------------
正则擅长认「值的形状」（身份证 18 位、银行卡 Luhn），但认不出「这段正文在谈论
什么敏感主题」：一句「请补充病历号与诊断结果」里并没有敏感值，却明确是敏感业务。
敏感词表补的正是这一层：命中只产生**低权重的类别证据**，交给评分公式与值命中一起决策，
因此不会因为出现「身份证」三个字就误报成高危。

实现取舍
--------
- 装了 ``pyahocorasick`` 就用 C 扩展（百万级词表也够快）；没装则退回同语义的纯 Python
  自动机，保证「只装最小依赖也能跑」——两条路径的匹配结果由测试保证一致；
- 英文词按整词边界匹配（``token`` 不应命中 ``tokenize``），中文按子串匹配；
- 知识库是 YAML（``knowledge/*.yaml``），改词表不用改代码，改完重启即生效。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .types import EntityType, Hit, parse_entity_type

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

# 词表命中的默认量级：低权重 + 中等置信度，单命中最多把分数推到 0.2 上下（SAFE/L1 边界）
DEFAULT_WEIGHT = 0.30
DEFAULT_CONFIDENCE = 0.60

# 英文词整词边界：前后不能是字母/数字/下划线
_WORDISH = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


@dataclass(frozen=True, slots=True)
class KeywordTerm:
    """一条敏感词条目。"""

    term: str
    entity: EntityType
    tier: str
    weight: float = DEFAULT_WEIGHT
    confidence: float = DEFAULT_CONFIDENCE
    note: str = ""

    @property
    def is_ascii_word(self) -> bool:
        return self.term.isascii() and self.term.replace("-", "").isalnum()


class _PureAhoCorasick:
    """纯 Python 的 Aho-Corasick：goto 表 + fail 指针 + 输出集。

    只为「没装 pyahocorasick」时保底，语义与 C 扩展一致：返回所有出现位置（含重叠）。
    """

    def __init__(self) -> None:
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._out: list[list[str]] = [[]]

    def add(self, word: str) -> None:
        node = 0
        for ch in word:
            nxt = self._goto[node].get(ch)
            if nxt is None:
                nxt = len(self._goto)
                self._goto[node][ch] = nxt
                self._goto.append({})
                self._fail.append(0)
                self._out.append([])
            node = nxt
        self._out[node].append(word)

    def build(self) -> None:
        queue: list[int] = []
        for child in self._goto[0].values():
            self._fail[child] = 0
            queue.append(child)
        head = 0
        while head < len(queue):
            node = queue[head]
            head += 1
            for ch, child in self._goto[node].items():
                fail = self._fail[node]
                while fail and ch not in self._goto[fail]:
                    fail = self._fail[fail]
                self._fail[child] = self._goto[fail].get(ch, 0)
                if self._fail[child] == child:
                    self._fail[child] = 0
                self._out[child].extend(self._out[self._fail[child]])
                queue.append(child)

    def find_all(self, text: str) -> list[tuple[int, int, str]]:
        """返回 ``[(start, end, word)]``，end 为开区间。"""
        found: list[tuple[int, int, str]] = []
        node = 0
        for index, ch in enumerate(text):
            while node and ch not in self._goto[node]:
                node = self._fail[node]
            node = self._goto[node].get(ch, 0)
            for word in self._out[node]:
                end = index + 1
                found.append((end - len(word), end, word))
        return found


def _build_automaton(terms: list[KeywordTerm]):
    """优先用 pyahocorasick；没有就退回纯 Python 实现。返回带 ``find_all`` 的对象。"""
    try:  # pragma: no cover - 取决于是否安装 C 扩展
        import ahocorasick  # type: ignore

        automaton = ahocorasick.Automaton()
        for term in terms:
            automaton.add_word(term.term, term.term)
        automaton.make_automaton()

        class _Wrapper:
            def find_all(self, text: str):
                # pyahocorasick.iter 返回 (end_index, value)，end_index 是「包含」的末字符下标，
                # 这里统一换算成 [start, end) 左闭右开，与纯 Python 实现保持同一语义。
                return [
                    (end - len(word) + 1, end + 1, word)
                    for end, word in automaton.iter(text)
                ]

        return _Wrapper()
    except Exception:  # noqa: BLE001 - 任何导入/构建失败都退回纯 Python
        automaton = _PureAhoCorasick()
        for term in terms:
            automaton.add(term.term)
        automaton.build()
        return automaton


class KeywordIndex:
    """已加载的知识库 + 构建好的自动机。"""

    def __init__(self, terms: list[KeywordTerm]) -> None:
        self.terms = tuple(terms)
        self._by_term = {term.term: term for term in terms}
        self._automaton = _build_automaton(terms)

    def find(self, text: str) -> list[tuple[int, int, KeywordTerm]]:
        """返回 ``[(start, end, term)]``；英文词做整词边界过滤。"""
        if not text:
            return []
        results: list[tuple[int, int, KeywordTerm]] = []
        for start, end, word in self._automaton.find_all(text):
            term = self._by_term[word]
            if term.is_ascii_word:
                before = text[start - 1] if start > 0 else ""
                after = text[end] if end < len(text) else ""
                if before in _WORDISH or after in _WORDISH:
                    continue
            results.append((start, end, term))
        results.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        return results

    def count_by_tier(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for term in self.terms:
            counts[term.tier] = counts.get(term.tier, 0) + 1
        return counts


def _term_from_dict(raw: dict[str, object], tier: str) -> KeywordTerm:
    term = str(raw.get("term", "")).strip()
    if not term:
        raise ValueError("知识库条目缺少 term")
    category = raw.get("category")
    if category is None:
        raise ValueError(f"知识库条目 {term!r} 缺少 category")
    entity = parse_entity_type(str(category))
    if entity is None:
        raise ValueError(f"知识库条目 {term!r} 的 category 非法：{category!r}")
    return KeywordTerm(
        term=term,
        entity=entity,
        tier=str(raw.get("tier", tier)),
        weight=float(raw.get("weight", DEFAULT_WEIGHT)),
        confidence=float(raw.get("confidence", DEFAULT_CONFIDENCE)),
        note=str(raw.get("note", "")),
    )


def load_knowledge(directory: str | Path | None = None) -> list[KeywordTerm]:
    """读取目录下所有 ``*.yaml``，合并成一份词表（同词保留先出现的）。"""
    root = Path(directory) if directory is not None else KNOWLEDGE_DIR
    if not root.is_dir():
        return []
    terms: list[KeywordTerm] = []
    seen: set[str] = set()
    for path in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"知识库文件格式错误（应为映射）：{path.name}")
        tier = str(data.get("tier", path.stem))
        for raw in data.get("terms") or []:
            if not isinstance(raw, dict):
                raise ValueError(f"知识库文件 {path.name} 的条目应为映射")
            term = _term_from_dict(raw, tier)
            if term.term in seen:
                continue
            seen.add(term.term)
            terms.append(term)
    return terms


@lru_cache(maxsize=4)
def _cached_index(directory: str) -> KeywordIndex:
    return KeywordIndex(load_knowledge(directory))


def get_keyword_index(directory: str | Path | None = None) -> KeywordIndex:
    """进程内单例（按目录缓存）；测试可用 ``reset_keyword_index()`` 清缓存。"""
    root = Path(directory) if directory is not None else KNOWLEDGE_DIR
    return _cached_index(str(root))


def reset_keyword_index() -> None:
    _cached_index.cache_clear()


def keywords_enabled_by_default() -> bool:
    return os.environ.get("FLOWMASK_DISABLE_KEYWORDS", "").strip() not in {"1", "true", "yes"}


def keyword_hits(text: str, index: KeywordIndex | None = None) -> list[Hit]:
    """把命中词转成 ``Hit``：区间就是词本身，rule 记成 ``kw-<tier>``。"""
    if not text:
        return []
    idx = index or get_keyword_index()
    hits: list[Hit] = []
    for start, end, term in idx.find(text):
        hits.append(
            Hit(
                type=term.entity,
                start=start,
                end=end,
                value=text[start:end],
                weight=term.weight,
                confidence=term.confidence,
                rule=f"kw-{term.tier}",
            )
        )
    return hits


def knowledge_summary(index: KeywordIndex | None = None) -> dict[str, object]:
    """给 ``/api/v1/keywords`` 用的词表概览（不泄露企业词表全文时可只取计数）。"""
    idx = index or get_keyword_index()
    by_tier = idx.count_by_tier()
    return {
        "total": len(idx.terms),
        "tiers": by_tier,
        "terms": [
            {
                "term": term.term,
                "category": term.entity.value,
                "tier": term.tier,
                "weight": term.weight,
                "confidence": term.confidence,
            }
            for term in idx.terms
        ],
    }
