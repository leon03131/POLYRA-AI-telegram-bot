"""HTML → plain text экстрактор (stdlib html.parser, локальный fallback для open_url)."""

import re
from html.parser import HTMLParser

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "nav", "iframe"})
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)

_BLANKS_RE = re.compile(r"\n{3,}")


class _TextExtractor(HTMLParser):
    """Собирает текстовые узлы, пропуская script/style/nav и превращая block-теги в \\n."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def html_to_text(html: str, *, max_chars: int = 20000) -> str:
    """Извлечь текст из HTML: вырезать script/style/nav, схлопнуть пробелы и пустые строки."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    raw = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = _BLANKS_RE.sub("\n\n", text)
    return text[:max_chars]
