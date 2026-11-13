"""Generate page_inject.js from page.js.

Chrome MV3 serializes `chrome.scripting.executeScript({func})` via toString().
Injecting a long function imported from an ES module can silently produce
`result: null` with no exceptionDetails. Instead we inject a classic script
that installs a structured wrapper on the isolated world's global object,
then call that wrapper with a tiny function. Any page-action exception is
returned as data, so the panel always gets a usable result.

Run this after every edit to page.js.
"""
from pathlib import Path

MARKER = "export async function pageAction(action, args) {"

HEADER = (
    "// GENERATED from page.js by build_page_inject.py - do not edit directly.\n"
    "// Classic script injected into the isolated world. All page actions are\n"
    "// exposed through one structured wrapper so failures never become null.\n"
)

FOOTER = """

globalThis.__dqPageAction = async function (action, args) {
  try {
    return {ok: true, data: await globalThis.__dqImpl(action, args)};
  } catch (error) {
    return {
      ok: false,
      error: String((error && error.message) || error),
      stack: String((error && error.stack) || '').slice(0, 400)
    };
  }
};
"""


def build(src: Path, dst: Path) -> None:
    text = src.read_text(encoding="utf-8")
    if MARKER not in text:
        raise SystemExit(f"marker not found in {src}")
    body = text.replace(
        MARKER, "globalThis.__dqImpl = async function (action, args) {", 1
    )
    idx = body.rfind("}")
    if idx < 0:
        raise SystemExit(f"no closing brace in {src}")
    body = body[:idx] + "};" + body[idx + 1 :]
    dst.write_text(HEADER + body + FOOTER, encoding="utf-8")
    print(f"{dst.name} <- {src.name} ({len(body)} chars)")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    build(root / "page.js", root / "page_inject.js")
