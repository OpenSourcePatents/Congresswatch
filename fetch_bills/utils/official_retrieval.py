"""Bounded federal bill similarity search over verified legislative text."""
from functools import lru_cache
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from .official_prose import ANALYSIS_VERSION, words
from .official_passages import MIN_COSINE, MIN_PASSAGE_WORDS, Tokens, Passages

TOP_CANDIDATES = 40
MAX_FINDINGS = 3
MODEL_UNAVAILABLE = 'primary_model_corpus_unavailable'


def vector_words(text):
    return [word for word, _, _ in words(text)]


class OfficialRetrieval:
    def __init__(self, corpus, metadata):
        self.corpus, self.metadata = corpus, metadata
        self.keys = sorted(corpus)
        self.indices = {key: i for i, key in enumerate(self.keys)}
        self.vectorizer = TfidfVectorizer(tokenizer=vector_words, token_pattern=None,
            lowercase=False, ngram_range=(1, 2), min_df=2, max_df=.85,
            sublinear_tf=True, dtype=np.float32)
        self.matrix = self.vectorizer.fit_transform(corpus[k]['text'] for k in self.keys)
        self.transpose = self.matrix.T.tocsr()
        self.tokens = lru_cache(maxsize=128)(
            lambda i: Tokens.from_text(self.corpus[self.keys[i]]['text']))

    def candidates(self, requested, batch_size=32):
        """Batch sparse cosine query; ordering deterministic even at tied scores."""
        for offset in range(0, len(requested), batch_size):
            batch = requested[offset:offset + batch_size]
            scored = (self.matrix[batch] @ self.transpose).tocsr()
            for index, target in enumerate(batch):
                row = scored.getrow(index)
                candidates = [(self.keys[i], float(val)) for i,val in zip(row.indices,row.data)
                              if i != target and val >= MIN_COSINE]
                candidates.sort(key=lambda p: (-p[1], p[0]))
                yield target, candidates[:TOP_CANDIDATES]

    def evidence(self, target, ranked):
        if not ranked:
            return []
        tkey = self.keys[target]
        source = self.corpus[tkey]['source']
        target_tokens = self.tokens(target)
        # Build the automaton only on a target with a numerical candidate.
        # Build on the shorter side per pair if the target is an omnibus.
        target_auto = None
        out = []
        for mkey, cosine in ranked:
            matched_tokens = self.tokens(self.indices[mkey])
            if (min(len(matched_tokens.terms),len(target_tokens.terms)) < MIN_PASSAGE_WORDS):
                continue
            if len(target_tokens.terms) > 15_000 and len(matched_tokens.terms) < len(target_tokens.terms):
                reverse = Passages(matched_tokens).evidence(target_tokens)
                found = None if reverse is None else {
                    (('matched_' + k[7:]) if k.startswith('target_') else
                     ('target_' + k[8:]) if k.startswith('matched_') else k):v
                    for k,v in reverse.items()}
            else:
                if target_auto is None: target_auto = Passages(target_tokens)
                found = target_auto.evidence(matched_tokens)
            if not found:
                continue
            other = self.corpus[mkey]['source']
            for prefix, identity in (('target',source),('matched',other)):
                for field in ('source_url','source_sha256','text_sha256','version_code','version_date'):
                    found[f'{prefix}_{field}'] = identity[field]
            tmeta, mmeta = self.metadata.get(tkey,{}), self.metadata.get(mkey,{})
            out.append({'bill_id':mkey,'bill_title':mmeta.get('title') or mkey,
                'member_id':(mmeta.get('sponsor_ids') or [None])[0],
                'same_sponsor':bool(set(tmeta.get('sponsor_ids',[])) & set(mmeta.get('sponsor_ids',[]))),
                'similarity_score':min(1., cosine),'evidence':found,'analysis_version':ANALYSIS_VERSION})
            if len(out) >= MAX_FINDINGS:
                break
        return out
