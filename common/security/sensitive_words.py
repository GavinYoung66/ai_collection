"""AC 自动机敏感词匹配。"""
import ahocorasick


class SensitiveWordMatcher:
    def __init__(self):
        self._automaton = ahocorasick.Automaton()

    def load_words(self, words: list[str]) -> None:
        self._automaton = ahocorasick.Automaton()
        for idx, word in enumerate(words):
            self._automaton.add_word(word, (idx, word))
        self._automaton.make_automaton()

    def match(self, text: str) -> list[dict]:
        hits = []
        for end_pos, (idx, word) in self._automaton.iter(text):
            hits.append({
                "word": word,
                "start": end_pos - len(word) + 1,
                "end": end_pos + 1,
            })
        return hits

    def contains(self, text: str) -> bool:
        return bool(self.match(text))


sensitive_matcher = SensitiveWordMatcher()
