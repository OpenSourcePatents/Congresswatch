"""Verify shared wording; no inference about legal equivalence or authorship."""
from dataclasses import dataclass
from .official_prose import words

MIN_COSINE = .30
MIN_PASSAGE_WORDS = 32
SHINGLE = 12
MIN_COVERAGE = .20


@dataclass(frozen=True, slots=True)
class Tokens:
    text: str
    terms: tuple
    spans: tuple

    @classmethod
    def from_text(cls, text):
        normalized = list(words(text))
        return cls(text, tuple(w[0] for w in normalized), tuple((w[1], w[2]) for w in normalized))

    def excerpt(self, start, length):
        return self.text[self.spans[start][0]:self.spans[start + length - 1][1]]


class Passages:
    """Linear-time exact contiguous reuse via a suffix automaton."""

    def __init__(self, target):
        self.target = target
        lengths, links, ends, edges = [0], [-1], [-1], [{}]
        last = 0
        for offset, token in enumerate(target.terms):
            curr = len(lengths)
            lengths.append(lengths[last] + 1)
            links.append(0)
            ends.append(offset)
            edges.append({})
            p = last
            while p >= 0 and token not in edges[p]:
                edges[p][token] = curr
                p = links[p]
            if p >= 0:
                q = edges[p][token]
                if lengths[p] + 1 == lengths[q]:
                    links[curr] = q
                else:
                    clone = len(lengths)
                    lengths.append(lengths[p] + 1)
                    links.append(links[q])
                    ends.append(ends[q])
                    edges.append(edges[q].copy())
                    while p >= 0 and edges[p].get(token) == q:
                        edges[p][token] = clone
                        p = links[p]
                    links[q] = links[curr] = clone
            last = curr
        self.lengths, self.links, self.ends, self.edges = lengths, links, ends, edges

    def longest(self, matched):
        state = length = best = best_left = best_right = 0
        for offset, token in enumerate(matched.terms):
            while state and token not in self.edges[state]:
                state = self.links[state]
                length = min(length, self.lengths[state])
            dest = self.edges[state].get(token)
            if dest is None:
                state = length = 0
                continue
            state = dest
            length += 1
            if length > best:
                best, best_left, best_right = length, self.ends[state] - length + 1, offset - length + 1
        return best, best_left, best_right

    def evidence(self, matched, *, min_words=MIN_PASSAGE_WORDS, min_coverage=MIN_COVERAGE):
        length, left, right = self.longest(matched)
        if length < min_words:
            return None
        a, b = (set(zip(*(x.terms[i:] for i in range(SHINGLE)))) for x in (self.target, matched))
        joint = len(a & b)
        ca, cb = joint / len(a), joint / len(b)
        if max(ca, cb) < min_coverage:
            return None
        shown = min(length, 120)
        return {
            'longest_passage_words': length, 'target_word_offset': left, 'matched_word_offset': right,
            'displayed_passage_words': shown, 'target_passage': self.target.excerpt(left, shown),
            'matched_passage': matched.excerpt(right, shown),
            'target_shingle_coverage': ca, 'matched_shingle_coverage': cb,
        }
