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
        # T124：未构建态直接返回空结果（未构建语义 = 无词库 = 不命中），不抛异常。
        # 实测 pyahocorasick（本仓库环境）：fresh Automaton 与仅 add_word 未
        # make_automaton 的 trie，iter() 均抛 AttributeError("Not an Aho-Corasick
        # automaton yet")；空词库 make_automaton() 虽调用成功但 kind 仍为 0(EMPTY)，
        # iter() 依旧抛异常。故以 kind != AHOCORASICK(2) 判定未构建态最可靠。
        automaton = self._automaton
        if automaton.kind != ahocorasick.AHOCORASICK:
            return []
        hits = []
        # T124：先取局部引用再判断/遍历，load_words 并发替换 _automaton 时
        # 保证 kind 检查与 iter 使用同一（均已构建完整的）automaton 实例。
        for end_pos, (idx, word) in automaton.iter(text):
            hits.append({
                "word": word,
                "start": end_pos - len(word) + 1,
                "end": end_pos + 1,
            })
        return hits

    def contains(self, text: str) -> bool:
        return bool(self.match(text))


sensitive_matcher = SensitiveWordMatcher()
