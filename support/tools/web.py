"""上网两件：搜资料、直接取网页。

都不需要 API key，靠公开搜索结果页解析。三条规矩：

1. **多源降级**：cn.bing.com → www.so.com → www.baidu.com，哪个能用用哪个
2. **相关性过滤**：搜索引擎对中文 query 会退化（拿首字当关键词），
   过滤后为空才算失败——宁可降级，不把垃圾当结果
3. **错误说人话**：403 就说"站点拒绝抓取"，别甩栈
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from support.tools.base import ToolResult, ToolSpec

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

STOPWORDS = {
    "的", "了", "吗", "呢", "吧", "啊", "是", "在", "有", "和", "与", "我", "你",
    "他", "她", "它", "这", "那", "什么", "怎么", "多少", "为什么", "如何",
}

HTTP_HINTS = {
    401: "需要登录才能看",
    403: "站点拒绝抓取（反爬）",
    404: "页面不存在",
    429: "请求太频繁被限流",
    500: "对方服务器出错了",
}


def _clean(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def _tokens(query: str) -> list[str]:
    """把 query 切成能比对的片段。

    中文不能整串比：「明天北京天气」在标题「北京天气预报」里是找不到的，
    切开成「明天/北京/天气/北京天/京天气」才比得上。
    """
    raw = re.findall(r"[0-9A-Za-z_]+|[\u4e00-\u9fff]+", str(query or ""))
    words: list[str] = []
    for token in raw:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            for size in (2, 3):
                for i in range(len(token) - size + 1):
                    gram = token[i : i + size]
                    if gram not in STOPWORDS and gram not in words:
                        words.append(gram)
        elif len(token) > 1 and token.casefold() not in STOPWORDS:
            words.append(token.casefold())
    if not words and raw:
        words = [raw[0].casefold()]  # 单字 query 也要能搜
    return words[:12]


def _relevant(query: str, title: str, snippet: str) -> bool:
    """搜索结果是不是真的和 query 有关。

    bing 对中文会退化成单字结果——标题可能变成「周（汉字）」这种。
    按有效词命中判断，命中太少就当没用。
    """
    words = _tokens(query)
    if not words:
        return False
    haystack = f"{title} {snippet}".casefold()
    hits = sum(1 for w in words if w in haystack)
    need = 1 if len(words) <= 2 else max(2, len(words) // 3)
    return hits >= need


def _bing(text: str, n: int) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for block in re.finditer(
        r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>(.*?)</li>', text, re.S | re.I
    ):
        body = block.group(1)
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S | re.I)
        if not m:
            continue
        link = html.unescape(m.group(1)).strip()
        title = _clean(m.group(2))
        if not title or not link.startswith(("http://", "https://")):
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", body, re.S | re.I)
        out.append((title, link, _clean(p.group(1)) if p else ""))
        if len(out) >= n:
            break
    return out


def _h3(text: str, n: int) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for m in re.finditer(
        r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S | re.I
    ):
        link = html.unescape(m.group(1)).strip()
        title = _clean(m.group(2))
        if not title or not link.startswith(("http://", "https://")):
            continue
        snippet = ""
        tail = text[m.end() : m.end() + 1200]
        p = re.search(
            r'<(?:div|span)[^>]*class="[^"]*(?:c-abstract|content-right|res-desc)[^"]*"'
            r"[^>]*>(.*?)</(?:div|span)>",
            tail,
            re.S | re.I,
        )
        if p:
            snippet = _clean(p.group(1))
        out.append((title, link, snippet))
        if len(out) >= n:
            break
    return out


ENGINES = (
    ("cn.bing.com", "https://cn.bing.com/search?q=", _bing),
    ("www.so.com", "https://www.so.com/s?q=", _h3),
    ("www.baidu.com", "https://www.baidu.com/s?wd=", _h3),
)


def _search(ctx, query: str, count: int = 6) -> ToolResult:
    import time

    q = str(query or "").strip()
    if not q:
        return ToolResult(False, "关键词是空的")
    n = max(1, min(int(count or 6), 10))

    deadline = time.monotonic() + 20
    tried: list[str] = []
    got_response = False

    for host, base, parse in ENGINES:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        req = urllib.request.Request(base + urllib.parse.quote(q), headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=min(8.0, left)) as resp:
                raw = resp.read(800_000)
        except Exception as exc:  # noqa: BLE001 - 单源失败就降级
            tried.append(f"{host}（{type(exc).__name__}）")
            continue

        got_response = True
        items = parse(raw.decode("utf-8", errors="replace"), n)
        good = [it for it in items if _relevant(q, it[0], it[2])]
        if good:
            lines = [f"搜「{q}」，{len(good)} 条（来自 {host}）："]
            for i, (title, link, snippet) in enumerate(good, 1):
                lines.append(f"{i}. {title}")
                lines.append(f"   {link}")
                if snippet:
                    lines.append(f"   {snippet[:160]}")
            return ToolResult(True, "\n".join(lines), {"source": host, "count": len(good)})

    if not got_response:
        return ToolResult(
            False, f"搜索失败（{('、'.join(tried)) or '没有可用源'}）。换个关键词再试。"
        )
    return ToolResult(
        False, f"搜「{q}」没找到靠谱的结果（搜索引擎返回的都是不相关的）。换个说法试试。"
    )


def _fetch(ctx, url: str, max_chars: int = 4000) -> ToolResult:
    """取一个网页/接口的正文。中文地址会先百分号编码。"""
    raw_url = str(url or "").strip()
    if not raw_url.startswith(("http://", "https://")):
        return ToolResult(False, "只支持 http/https 地址")

    parts = urllib.parse.urlsplit(raw_url)
    safe_url = urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            urllib.parse.quote(parts.path, safe="/%:@&=+$,;~"),
            urllib.parse.quote(parts.query, safe="=&%:@+,;/?:~"),
            parts.fragment,
        )
    )

    req = urllib.request.Request(safe_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read(500_000)
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        return ToolResult(False, f"对方返回 {exc.code}：{HTTP_HINTS.get(exc.code, '请求被拒绝')}")
    except urllib.error.URLError as exc:
        return ToolResult(False, f"连不上：{exc.reason}")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(False, f"抓取失败：{type(exc).__name__}")

    text = body.decode(charset, errors="replace")
    looks_html = "<html" in text[:2000].lower() or "<body" in text[:2000].lower()
    if looks_html:
        text = _clean(text)

    limit = max(200, min(int(max_chars or 4000), 20000))
    truncated = len(text) > limit
    from core import clock

    body = text[:limit] + ("\n…（后面还有，太长截掉了）" if truncated else "")
    # 带上抓取时间——网页内容会变，模型得知道这是什么时候看到的
    return ToolResult(
        True,
        f"（{safe_url.split('/')[2]} · 抓取于 {clock.stamp()} · {len(text)} 字）\n{body}",
        {"chars": len(text), "truncated": truncated},
    )


TOOL_SEARCH = ToolSpec(
    name="web_search",
    description="搜网页。查资料、找新闻、不知道去哪查的时候用它；找到链接再决定要不要 web_fetch 看全文。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，别写整句话"},
            "count": {"type": "integer", "description": "几条，默认 6，最多 10"},
        },
        "required": ["query"],
    },
    toolbox="封装",
    func=_search,
)

TOOL_FETCH = ToolSpec(
    name="web_fetch",
    description="直接打开一个网址，拿正文。天气/榜单这类有固定接口的，直接取接口比搜网页准。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整 http/https 地址"},
            "max_chars": {"type": "integer", "description": "最多取多少字，默认 4000"},
        },
        "required": ["url"],
    },
    toolbox="封装",
    func=_fetch,
)
