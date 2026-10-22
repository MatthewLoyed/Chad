"""
Deterministic syntax gates for agent-written source files.

Industry-standard order: syntax → (optional lint) before persisting artifacts.
Used by write_file and build_html_file so invalid code is rejected with actionable
errors the ReAct loop can fix — not delivered to the user broken.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional, Tuple

# Extensions that get a syntax gate before write/packaging.
VERIFY_EXTENSIONS = frozenset({
    ".py", ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json",
    ".ts", ".tsx", ".jsx",
})

_VOID_HTML_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


@dataclass(frozen=True)
class CodeVerifyResult:
    ok: bool
    gate: str
    error: Optional[str] = None

    def as_tool_error(self) -> str:
        if self.ok:
            return ""
        return (
            f"CODE_VERIFY_FAILED [{self.gate}]: {self.error}\n"
            "Fix the syntax error and try again. The file was NOT saved."
        )


class _HTMLBalanceParser(HTMLParser):
    """Lightweight tag-balance check for agent-generated HTML (not a full validator)."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t not in _VOID_HTML_TAGS:
            self.stack.append(t)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if not self.stack:
            self.errors.append(f"Unexpected closing </{t}>")
            return
        if self.stack[-1] != t:
            self.errors.append(
                f"Mismatched tag: expected </{self.stack[-1]}>, got </{t}>"
            )
            if t in self.stack:
                while self.stack and self.stack[-1] != t:
                    self.stack.pop()
                if self.stack:
                    self.stack.pop()
            return
        self.stack.pop()

    def close(self) -> None:
        super().close()
        if self.stack:
            self.errors.append(f"Unclosed tag(s): {', '.join(reversed(self.stack))}")


def _ext_from_name(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _verify_python(content: str) -> CodeVerifyResult:
    try:
        ast.parse(content)
        return CodeVerifyResult(ok=True, gate="python_syntax")
    except SyntaxError as e:
        loc = f"line {e.lineno}" if e.lineno else "unknown line"
        msg = e.msg or "invalid syntax"
        return CodeVerifyResult(ok=False, gate="python_syntax", error=f"{loc}: {msg}")


def _verify_json(content: str) -> CodeVerifyResult:
    try:
        json.loads(content)
        return CodeVerifyResult(ok=True, gate="json_syntax")
    except json.JSONDecodeError as e:
        return CodeVerifyResult(
            ok=False, gate="json_syntax",
            error=f"line {e.lineno}: {e.msg}",
        )


def _verify_html(content: str) -> CodeVerifyResult:
    stripped = content.strip()
    if len(stripped) < 20:
        return CodeVerifyResult(
            ok=False, gate="html_syntax",
            error="HTML content is too short to be a valid page",
        )
    parser = _HTMLBalanceParser()
    try:
        parser.feed(stripped)
        parser.close()
    except Exception as e:
        return CodeVerifyResult(ok=False, gate="html_syntax", error=str(e))
    if parser.errors:
        return CodeVerifyResult(
            ok=False, gate="html_syntax",
            error="; ".join(parser.errors[:3]),
        )
    return CodeVerifyResult(ok=True, gate="html_syntax")


def _brace_balance(content: str, open_ch: str, close_ch: str) -> Optional[str]:
    depth = 0
    in_single = in_double = in_backtick = False
    escape = False
    i = 0
    while i < len(content):
        ch = content[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\" and (in_single or in_double or in_backtick):
            escape = True
            i += 1
            continue
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue
        if in_backtick:
            if ch == "`":
                in_backtick = False
            i += 1
            continue
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "`":
            in_backtick = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth < 0:
                return f"Unbalanced {open_ch}{close_ch}: extra closing brace"
        i += 1
    if depth != 0:
        return f"Unbalanced {open_ch}{close_ch}: {depth} unclosed"
    return None


def _verify_css(content: str) -> CodeVerifyResult:
    err = _brace_balance(content, "{", "}")
    if err:
        return CodeVerifyResult(ok=False, gate="css_syntax", error=err)
    return CodeVerifyResult(ok=True, gate="css_syntax")


def _node_check_available() -> bool:
    return shutil.which("node") is not None


def _verify_javascript(content: str, ext: str) -> CodeVerifyResult:
    if _node_check_available():
        suffix = ext if ext in (".js", ".mjs", ".cjs") else ".js"
        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=suffix, delete=False, encoding="utf-8",
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            proc = subprocess.run(
                ["node", "--check", tmp_path],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "syntax error").strip()
                return CodeVerifyResult(ok=False, gate="javascript_syntax", error=err[:500])
            return CodeVerifyResult(ok=True, gate="javascript_syntax")
        except subprocess.TimeoutExpired:
            return CodeVerifyResult(
                ok=False, gate="javascript_syntax",
                error="node --check timed out",
            )
        except Exception as e:
            return CodeVerifyResult(ok=False, gate="javascript_syntax", error=str(e))
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # Fallback when Node is not installed: brace/paren balance only.
    for open_ch, close_ch, label in (("{", "}", "braces"), ("(", ")", "parens")):
        err = _brace_balance(content, open_ch, close_ch)
        if err:
            return CodeVerifyResult(
                ok=False, gate="javascript_heuristic",
                error=f"{label}: {err} (install Node.js for full JS syntax check)",
            )
    return CodeVerifyResult(ok=True, gate="javascript_heuristic")


def _verify_typescript(content: str, filename: str) -> CodeVerifyResult:
    """TS/TSX: use tsc --noEmit when a tsconfig exists nearby; else JS heuristic."""
    if shutil.which("npx"):
        cwd = os.getcwd()
        tsconfig = os.path.join(cwd, "tsconfig.json")
        if os.path.isfile(tsconfig):
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=os.path.splitext(filename)[1] or ".ts",
                    delete=False,
                    encoding="utf-8",
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                proc = subprocess.run(
                    ["npx", "tsc", "--noEmit", tmp_path],
                    capture_output=True, text=True, timeout=30, cwd=cwd,
                )
                if proc.returncode != 0:
                    err = (proc.stderr or proc.stdout or "typecheck failed").strip()
                    return CodeVerifyResult(ok=False, gate="typescript_check", error=err[:800])
                return CodeVerifyResult(ok=True, gate="typescript_check")
            except subprocess.TimeoutExpired:
                return CodeVerifyResult(
                    ok=False, gate="typescript_check", error="tsc timed out",
                )
            except Exception as e:
                return CodeVerifyResult(ok=False, gate="typescript_check", error=str(e))
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
    ext = _ext_from_name(filename)
    if ext in (".tsx", ".jsx"):
        # Strip JSX-ish tags for a rough JS parse attempt via node on transformed stub — skip;
        # fall through to brace balance.
        err = _brace_balance(content, "{", "}")
        if err:
            return CodeVerifyResult(ok=False, gate="tsx_heuristic", error=err)
        return CodeVerifyResult(ok=True, gate="tsx_heuristic")
    return _verify_javascript(content, ext)


def verify_source(filename: str, content: str) -> CodeVerifyResult:
    """
    Run the appropriate syntax gate for `filename`'s extension.
    Returns ok=True when no gate applies or verification passes.
    """
    ext = _ext_from_name(filename)
    if ext not in VERIFY_EXTENSIONS:
        return CodeVerifyResult(ok=True, gate="skip")

    if ext == ".py":
        return _verify_python(content)
    if ext == ".json":
        return _verify_json(content)
    if ext in (".html", ".htm"):
        return _verify_html(content)
    if ext == ".css":
        return _verify_css(content)
    if ext in (".js", ".mjs", ".cjs"):
        return _verify_javascript(content, ext)
    if ext in (".ts", ".tsx", ".jsx"):
        return _verify_typescript(content, filename)

    return CodeVerifyResult(ok=True, gate="skip")


def verify_source_or_html(filename: str, content: str) -> Tuple[bool, str]:
    """
    Convenience wrapper for tools: (passed, error_message_for_tool).
    Treats content as HTML when filename ends with .html/.htm or lacks extension.
    """
    name = filename or "artifact.html"
    if not _ext_from_name(name):
        name = f"{name}.html"
    # Strip markdown fences commonly leaked by models
    cleaned = _strip_markdown_fences(content)
    result = verify_source(name, cleaned)
    if result.ok:
        return True, ""
    return False, result.as_tool_error()


def _strip_markdown_fences(text: str) -> str:
    text = re.sub(r"^```html\s*", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"^```\w*\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    return text.strip()
