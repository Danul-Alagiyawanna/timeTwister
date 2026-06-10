"""Minimal driver shim so Selenium extractors can run on Scrapling HTML."""


class HtmlPage:
    def __init__(self, html: str, url: str, title: str = ""):
        self.page_source = html
        self.current_url = url
        self.title = title


def html_driver(html: str, url: str, title: str = "") -> HtmlPage:
    return HtmlPage(html, url, title=title)
