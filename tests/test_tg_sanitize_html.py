# -*- coding: utf-8 -*-
"""tg_send HTML 이스케이프 (2026-10-18): 허용 태그는 보존, 비교문자/앰퍼샌드는 엔티티로."""
import ast, os, re, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _load():
    src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "_tg_sanitize_html")
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("_TG_ALLOWED_TAG_RE", "_TG_ENTITY_RE") for t in n.targets))]
    ns = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns["_tg_sanitize_html"]

class TestTgSanitize(unittest.TestCase):
    def setUp(self):
        self.f = _load()

    def test_allowed_tags_preserved(self):
        for t in ("⚠️ <b>자동청산 실패</b> X", "<code>abc</code>", '<a href="https://x">링크</a>', "<pre>x</pre>", "<i>y</i>"):
            self.assertEqual(self.f(t), t)

    def test_comparison_chars_escaped(self):
        self.assertEqual(self.f("분포[손해11|<0.1:16|0.5+:2]"), "분포[손해11|&lt;0.1:16|0.5+:2]")
        self.assertEqual(self.f("n<100 and x>3"), "n&lt;100 and x&gt;3")

    def test_ampersand_escaped_but_entities_kept(self):
        self.assertEqual(self.f("A&B"), "A&amp;B")
        self.assertEqual(self.f("5,999원 &lt; 6,000원"), "5,999원 &lt; 6,000원")
        self.assertEqual(self.f("&amp; stays"), "&amp; stays")

    def test_mixed(self):
        self.assertEqual(self.f("<b>kill</b> n<3 & <i>ok</i>"), "<b>kill</b> n&lt;3 &amp; <i>ok</i>")

    def test_passthrough_when_nothing_to_do(self):
        self.assertEqual(self.f("plain 텍스트 123"), "plain 텍스트 123")
        self.assertEqual(self.f(""), "")

    def test_tg_send_uses_sanitized_text(self):
        src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
        i = src.index("def tg_send(")
        body = src[i:i + 4000]
        self.assertIn("_t_html = _tg_sanitize_html(t)", body)
        self.assertIn('"text": _t_html', body)

if __name__ == "__main__":
    unittest.main(verbosity=2)
