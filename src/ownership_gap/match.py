"""A name index: exact core-name lookup plus token-sort fuzzy scoring over token-blocked candidates.

The scorer is token-sort similarity on the whole core name (0 to 100), the way name screening scores a match:
word order does not matter, extra or missing words lower the score. ``rapidfuzz`` is used when installed;
otherwise a pure-Python fallback gives the same kind of score (not bit-identical).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Generic, Iterable, List, Optional, Set, Tuple, TypeVar

try:  # pragma: no cover - exercised only when rapidfuzz is installed
    from rapidfuzz import fuzz as _rf_fuzz  # type: ignore[import-not-found, unused-ignore]

    def _token_sort(a: str, b: str) -> float:
        return float(_rf_fuzz.token_sort_ratio(a, b))

    SCORER = "rapidfuzz.token_sort_ratio"
except ImportError:  # pragma: no cover - exercised only without rapidfuzz

    def _token_sort(a: str, b: str) -> float:
        a2, b2 = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
        return 100.0 * SequenceMatcher(None, a2, b2).ratio()

    SCORER = "difflib token-sort ratio"

T = TypeVar("T")

# A core name shorter than this (in characters) only ever matches exactly: "zte" must not fuzz into "ztt".
MIN_FUZZY_LEN = 5


@dataclass(frozen=True)
class Hit(Generic[T]):
    payload: T
    matched: str  # the core name on the list side
    query: str  # the core name on the vendor side
    score: float  # 100.0 for an exact core-name match

    @property
    def exact(self) -> bool:
        return self.score >= 100.0


class NameIndex(Generic[T]):
    def __init__(self, max_df: int = 400) -> None:
        self._names: List[str] = []
        self._payloads: List[T] = []
        self._exact: Dict[str, List[int]] = defaultdict(list)
        self._tok: Dict[str, Set[int]] = defaultdict(set)
        self._max_df = max_df
        self._frozen: Optional[Dict[str, Set[int]]] = None

    def add(self, core_name: str, payload: T) -> None:
        if not core_name:
            return
        i = len(self._names)
        self._names.append(core_name)
        self._payloads.append(payload)
        self._exact[core_name].append(i)
        for t in set(core_name.split()):
            self._tok[t].add(i)
        self._frozen = None

    def __len__(self) -> int:
        return len(self._names)

    def _blocks(self) -> Dict[str, Set[int]]:
        # Very common words ("china", "technology", "trading") would pull in thousands of candidates without
        # deciding anything; a name made only of common words still matches exactly.
        if self._frozen is None:
            self._frozen = {t: ids for t, ids in self._tok.items() if len(ids) <= self._max_df}
        return self._frozen

    def candidates(self, query: str) -> Set[int]:
        blocks = self._blocks()
        out: Set[int] = set()
        for t in set(query.split()):
            ids = blocks.get(t)
            if ids:
                out |= ids
        return out

    def best(
        self, queries: Iterable[str], threshold: float, same_first: bool = False
    ) -> Tuple[List[Hit[T]], Optional[Hit[T]]]:
        """Hits at or above ``threshold`` (best first, one per payload) and the closest name overall.

        The closest name is reported even when it is below the threshold, so a clear result shows what it
        was compared with. ``same_first`` keeps a fuzzy hit only when both names start with the same word
        ("THE BODE TECHNOLOGY GROUP" is not "BOE Technology Group").
        """
        hits: Dict[int, Hit[T]] = {}
        closest: Optional[Hit[T]] = None
        for q in queries:
            if not q:
                continue
            for i in self._exact.get(q, ()):
                h = Hit(self._payloads[i], self._names[i], q, 100.0)
                if i not in hits:
                    hits[i] = h
                closest = h
            if len(q) < MIN_FUZZY_LEN:
                continue
            for i in self.candidates(q):
                name = self._names[i]
                if len(name) < MIN_FUZZY_LEN:
                    continue
                s = _token_sort(q, name)
                h = Hit(self._payloads[i], name, q, round(s, 1))
                ok = not same_first or q.split()[0] == name.split()[0]
                if ok and s >= threshold and (i not in hits or hits[i].score < s):
                    hits[i] = h
                if closest is None or s > closest.score:
                    closest = h
        ranked = sorted(hits.values(), key=lambda h: -h.score)
        return ranked, closest


def build(entries: Iterable[Tuple[str, T]], max_df: int = 400) -> NameIndex[T]:
    idx: NameIndex[T] = NameIndex(max_df=max_df)
    for name, payload in entries:
        idx.add(name, payload)
    return idx


def score(a: str, b: str) -> float:
    """The scorer both screens use, exposed for tests and callers."""
    return _token_sort(a, b)


__all__ = ["MIN_FUZZY_LEN", "SCORER", "Hit", "NameIndex", "build", "score"]
