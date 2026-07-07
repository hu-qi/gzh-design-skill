#!/usr/bin/env python3
"""微信公众号 HTML 合规校验器。

将公众号编辑器的静态限制与排版 Skill 的强制约束变成可复现的质量门禁。
默认模式保持兼容；CI 与正式交付应使用 ``--strict --fail-on-warning``。

用法:
    validate_gzh_html.py <file.html>
    validate_gzh_html.py --stdin < file.html
    validate_gzh_html.py --strict --fail-on-warning <file.html>

退出码：
    0 = 通过；
    1 = 有 ERROR，或启用 --fail-on-warning 后仍有 WARNING。
"""

import argparse
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse

CJK = re.compile(r"[一-鿿㐀-䶿]")
HALF_PUNCT = re.compile(r"[一-鿿㐀-䶿][,;!?]")
ASCII_QUOTE = re.compile(r"[\"']")
CODE_STYLE = re.compile(
    r"monospace|white-space\s*:\s*pre|courier|consolas|sf mono", re.I
)

FORBIDDEN_TAGS = {
    "style": "<style> 标签会被过滤，样式必须内联",
    "script": "<script> 标签会被过滤",
    "div": "<div> 会被改写，请用 <section>",
    "link": "外部 <link>（CSS/字体）会被过滤",
    "iframe": "<iframe> 不应出现在公众号正文或预览输入中",
    "object": "<object> 不应出现在公众号正文或预览输入中",
    "embed": "<embed> 不应出现在公众号正文或预览输入中",
    "base": "<base> 会改变相对 URL 解析，不应出现",
    "meta": "<meta> 不属于公众号正文片段",
    "form": "<form> 不属于公众号正文片段",
    "input": "<input> 不属于公众号正文片段",
    "button": "<button> 不属于公众号正文片段",
    "textarea": "<textarea> 不属于公众号正文片段",
    "select": "<select> 不属于公众号正文片段",
    "option": "<option> 不属于公众号正文片段",
    "svg": "<svg> 可能被过滤且不属于受支持的正文组件",
    "math": "<math> 不属于受支持的正文组件",
    "video": "<video> 不属于受支持的正文组件",
    "audio": "<audio> 不属于受支持的正文组件",
}

FORBIDDEN_STYLE = [
    (re.compile(r"position\s*:\s*(fixed|absolute|sticky)", re.I),
     "position fixed/absolute/sticky 不被支持"),
    (re.compile(r"float\s*:", re.I), "float 不被支持"),
    (re.compile(r"@media", re.I), "@media 媒体查询不被支持"),
    (re.compile(r"@keyframes", re.I), "@keyframes 动画不被支持"),
    (re.compile(r"@import", re.I), "@import 不被支持"),
    (re.compile(r"display\s*:\s*grid", re.I),
     "display:grid 不被支持，请用 flex"),
    (re.compile(r"var\s*\(\s*--", re.I),
     "CSS 变量 var(--x) 不被支持，请写死值"),
    (re.compile(r"expression\s*\(", re.I),
     "CSS expression() 不安全且不被支持"),
    (re.compile(r"behavior\s*:", re.I),
     "CSS behavior 不安全且不被支持"),
    (re.compile(r"-moz-binding\s*:", re.I),
     "CSS -moz-binding 不安全且不被支持"),
    (re.compile(
        r"url\s*\(\s*['\"]?https?://[^)]*\.(?:woff2?|ttf|otf|eot)",
        re.I,
    ), "外部字体不被支持"),
]

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
SKIP_TAGS = {"head", "title", "style", "script"}
DANGEROUS_SCHEMES = {"javascript", "vbscript", "file"}
URL_ATTRS = {"href", "src", "xlink:href", "formaction"}


class ComplianceChecker(HTMLParser):
    """检查结构、平台红线、预览页输入安全和 leaf 包裹。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []  # [(tag, is_leaf, is_code)]
        self.leaf_depth = 0
        self.code_depth = 0
        self.span_leaf_count = 0
        self.unwrapped = []  # (文本片段, 父标签)
        self.half_punct = []
        self.errors = []
        self.top_level_tags = []
        self.root_text = []
        self._seen_errors = set()

    def add_error(self, message):
        if message not in self._seen_errors:
            self._seen_errors.add(message)
            self.errors.append(message)

    def handle_decl(self, decl):
        self.add_error(f"不允许文档声明 <!{decl}>；产物只能是 <section> 正文片段")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = [(name.lower(), value or "") for name, value in attrs]

        if not self.stack:
            self.top_level_tags.append(tag)

        if tag in FORBIDDEN_TAGS:
            self.add_error(FORBIDDEN_TAGS[tag])

        attr_map = dict(attrs)
        for name, value in attrs:
            if name in {"class", "id"}:
                self.add_error(f"{name} 属性会被剥离，请使用内联 style")
            elif name.startswith("on"):
                self.add_error(f"事件属性 {name} 不允许出现在正文或预览输入中")
            elif name in URL_ATTRS:
                self._check_url(tag, name, value)

        style = attr_map.get("style", "")
        for rx, message in FORBIDDEN_STYLE:
            if rx.search(style):
                self.add_error(message)

        is_leaf = tag == "span" and "leaf" in attr_map
        is_code = tag in {"code", "pre"} or bool(CODE_STYLE.search(style))
        if tag not in VOID_TAGS:
            if is_leaf:
                self.span_leaf_count += 1
                self.leaf_depth += 1
            if is_code:
                self.code_depth += 1
            self.stack.append((tag, is_leaf, is_code))

    def _check_url(self, tag, name, value):
        value = value.strip()
        if not value:
            return
        compact = re.sub(r"[\x00-\x20]+", "", value).lower()
        try:
            parsed = urlparse(compact)
            scheme = parsed.scheme.lower()
        except ValueError:
            self.add_error(f"{name} 包含无效的 URL 格式")
            return

        if scheme in DANGEROUS_SCHEMES:
            self.add_error(f"{name} 使用了不安全的 {scheme}: URL")
        elif scheme == "data":
            if tag != "img" or not re.match(
                r"^data:image/(?:png|gif|jpe?g|webp);base64,", compact, re.I
            ):
                self.add_error(
                    f"{name} 仅允许用于 <img> 的 base64 图片 data:image/... URL"
                )

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for _, was_leaf, was_code in self.stack[i:]:
                    if was_leaf:
                        self.leaf_depth -= 1
                    if was_code:
                        self.code_depth -= 1
                del self.stack[i:]
                return
        self.add_error(f"发现无法匹配的闭合标签 </{tag}>")

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if not self.stack:
            self.root_text.append(text)
        if not CJK.search(text):
            return
        if any(tag in SKIP_TAGS for tag, _, _ in self.stack):
            return

        if self.leaf_depth == 0 and self.code_depth == 0:
            parent = self.stack[-1][0] if self.stack else "(root)"
            self.unwrapped.append((self._snippet(text), parent))
        if self.code_depth == 0 and (
            HALF_PUNCT.search(text) or ASCII_QUOTE.search(text)
        ):
            self.half_punct.append(self._snippet(text))

    @staticmethod
    def _snippet(text):
        return text[:24] + ("…" if len(text) > 24 else "")

    def finish(self, strict=False):
        if self.stack:
            open_tags = " > ".join(tag for tag, _, _ in self.stack)
            self.add_error(f"HTML 标签未闭合：{open_tags}")

        if strict:
            if self.top_level_tags != ["section"]:
                actual = ", ".join(f"<{tag}>" for tag in self.top_level_tags) or "无"
                self.add_error(
                    "严格模式要求产物只有一个顶层 <section>；"
                    f"当前顶层节点：{actual}"
                )
            if self.root_text:
                sample = "；".join(self._snippet(item) for item in self.root_text[:3])
                self.add_error(
                    f"严格模式不允许根节点裸文本；例：{sample}"
                )


def validate(html, name="<input>", strict=False):
    """返回 (errors, warnings, leaf_count)。strict 使 leaf/根节点契约可阻断。"""
    checker = ComplianceChecker()
    warnings = []
    try:
        checker.feed(html)
        checker.close()
    except Exception as exc:
        checker.add_error(f"HTML 解析失败：{exc}")
    checker.finish(strict=strict)

    has_cjk = bool(CJK.search(html))
    if has_cjk and checker.span_leaf_count == 0:
        checker.add_error(
            '全文没有任何 <span leaf=""> 包裹——粘贴到公众号后样式会大面积丢失'
        )
    elif checker.unwrapped:
        sample = "；".join(
            f"「{snippet}」(在 <{parent}> 内)"
            for snippet, parent in checker.unwrapped[:5]
        )
        message = (
            f"{len(checker.unwrapped)} 处中文文本未被 <span leaf> 包裹，"
            f"样式可能丢失。例：{sample}"
        )
        if strict:
            checker.add_error(message)
        else:
            warnings.append(message)

    if checker.half_punct:
        sample = "；".join(f"「{snippet}」" for snippet in checker.half_punct[:5])
        warnings.append(
            f"{len(checker.half_punct)} 处正文疑似半角标点/英文引号，应改中文全角"
            f"（代码块内不计）。例：{sample}"
        )

    return checker.errors, warnings, checker.span_leaf_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="HTML 文件路径")
    ap.add_argument("--stdin", action="store_true", help="从标准输入读取")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="将 leaf 漏包裹和非单一 <section> 根节点视为 ERROR",
    )
    ap.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="有 WARNING 时也返回非零退出码，适用于 CI",
    )
    args = ap.parse_args()

    if args.stdin or not args.file:
        html = sys.stdin.read()
        name = "<stdin>"
    else:
        with open(args.file, encoding="utf-8", errors="replace") as handle:
            html = handle.read()
        name = args.file

    errors, warnings, leaf_n = validate(html, name, strict=args.strict)

    print(f"📋 公众号 HTML 合规校验: {name}")
    print(f"   span leaf 包裹: {leaf_n} 处")
    if errors:
        print(f"\n❌ ERROR ×{len(errors)}（必须修复，否则粘贴后失效）:")
        for error in errors:
            print(f"   • {error}")
    if warnings:
        print(f"\n⚠️  WARNING ×{len(warnings)}（建议检查）:")
        for warning in warnings:
            print(f"   • {warning}")
    if not errors and not warnings:
        print("\n✅ 完全合规，可直接粘贴到公众号编辑器")
    elif not errors:
        print("\n✅ 无致命问题，可粘贴（warning 请人工确认）")

    sys.exit(1 if errors or (args.fail_on_warning and warnings) else 0)


if __name__ == "__main__":
    main()
