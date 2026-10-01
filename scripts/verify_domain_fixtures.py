"""Verify the domain-knowledge fixtures before any agent run (§34 discipline).

Same contract as verify_multi_fixtures.py: each fixture must be FAILING in
its shipped (buggy) state AND PASSING after the known root-cause fix —
otherwise the acceptance signal is meaningless. The domain fixtures carry
their fix as a WHOLE-FILE replacement (the fix is a rewrite of the
manifest/tokens/page, not a one-line splice): a step of (file, None, content)
replaces the file; (file, old, new) splices like the multi verifier.

Usage:
    .venv/bin/python scripts/verify_domain_fixtures.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from domain_tasks import DOMAIN_TASKS  # noqa: E402

PYTEST = str(Path(__file__).resolve().parent.parent / ".venv/bin/python")

# The known root-cause fix per fixture — whole-file replacements (the fix
# IS the conforming manifest/tokens/page). Applied by apply_fix.
DOMAIN_FIXES: dict[str, list[tuple[str, str | None, str]]] = {
    # The recipient of an outgoing reply is the authenticated address on
    # file — untrusted ticket text can classify and draft, never decide
    # where a message goes. (The body-scanning "forward" feature is the
    # vulnerability; removing it is the fix, not filtering addresses.)
    "domain-injection-hardening": [
        (
            "agent.py",
            None,
            '"""Support-ticket triage: classify a ticket, draft a reply, send it to the\n'
            'ticket owner\'s verified address."""\n\n'
            "from store import CustomerStore\n\n\n"
            "class Mailer:\n"
            '    """Outbound sink (fake) — records every message that would leave."""\n\n'
            "    def __init__(self) -> None:\n"
            "        self.sent: list[dict] = []\n\n"
            "    def send(self, to: str, body: str) -> None:\n"
            '        self.sent.append({"to": to, "body": body})\n\n\n'
            "class SupportAgent:\n"
            "    def __init__(self, store: CustomerStore, mailer: Mailer) -> None:\n"
            "        self._store = store\n"
            "        self._mailer = mailer\n\n"
            "    def handle(self, ticket_id: str, subject: str, body: str) -> str:\n"
            '        """Draft a reply and send it to the ticket owner\'s verified address.\n\n'
            "        The ticket body is untrusted DATA: it can classify and draft, but it\n"
            "        can never decide where a message goes — the recipient is the\n"
            "        authenticated address on file, nothing else.\n"
            '        """\n'
            "        reply_to = self._store.verified_email(ticket_id)\n"
            '        draft = f"Re: {subject}\\n\\nThank you for contacting support."\n'
            "        self._mailer.send(reply_to, draft)\n"
            "        return reply_to\n",
        ),
    ],
    # The conforming manifest: prefixed action-oriented names, the four
    # annotation hints with per-operation semantics, every parameter
    # required, actionable error messages.
    "domain-mcp-annotations": [
        (
            "server.py",
            None,
            '"""Library MCP server — the tool manifest an MCP client (an LLM agent)\n'
            'discovers tools from. Also rendered by the ops dashboard."""\n\n'
            "TOOLS = [\n"
            "    {\n"
            '        "name": "library_get_book",\n'
            '        "description": "Get one book by its id.",\n'
            '        "inputSchema": {\n'
            '            "type": "object",\n'
            '            "properties": {"book_id": {"type": "string", "description": "Book id"}},\n'
            '            "required": ["book_id"],\n'
            "        },\n"
            '        "annotations": {\n'
            '            "readOnlyHint": True,\n'
            '            "destructiveHint": False,\n'
            '            "idempotentHint": True,\n'
            '            "openWorldHint": False,\n'
            "        },\n"
            "    },\n"
            "    {\n"
            '        "name": "library_find_books",\n'
            '        "description": "Find books matching a search query.",\n'
            '        "inputSchema": {\n'
            '            "type": "object",\n'
            "  "
            '          "properties": {"query": {"type": "string", "description": "Search text"}},\n'
            '            "required": ["query"],\n'
            "        },\n"
            '        "annotations": {\n'
            '            "readOnlyHint": True,\n'
            '            "destructiveHint": False,\n'
            '            "idempotentHint": True,\n'
            '            "openWorldHint": False,\n'
            "        },\n"
            "    },\n"
            "    {\n"
            '        "name": "library_remove_book",\n'
            '        "description": "Remove a book from the catalog.",\n'
            '        "inputSchema": {\n'
            '            "type": "object",\n'
            '            "properties": {"book_id": {"type": "string", "description": "Book id"}},\n'
            '            "required": ["book_id"],\n'
            "        },\n"
            '        "annotations": {\n'
            '            "readOnlyHint": False,\n'
            '            "destructiveHint": True,\n'
            '            "idempotentHint": True,\n'
            '            "openWorldHint": False,\n'
            "        },\n"
            "    },\n"
            "    {\n"
            '        "name": "library_add_book",\n'
            '        "description": "Add a book to the catalog.",\n'
            '        "inputSchema": {\n'
            '            "type": "object",\n'
            '            "properties": {\n'
            '                "title": {"type": "string", "description": "Title"},\n'
            '                "author": {"type": "string", "description": "Author"},\n'
            "            },\n"
            '            "required": ["author", "title"],\n'
            "        },\n"
            '        "annotations": {\n'
            '            "readOnlyHint": False,\n'
            '            "destructiveHint": False,\n'
            '            "idempotentHint": False,\n'
            '            "openWorldHint": False,\n'
            "        },\n"
            "    },\n"
            "]\n\n"
            "ERROR_MESSAGES = {\n"
            '    "book_not_found": (\n'
            '        "Book not found. Check the book id and try again, or use "\n'
            '        "library_find_books to search by title."\n'
            "    ),\n"
            "   "
            ' "invalid_query": "Invalid query. Provide a non-empty search string and try again.",\n'
            "}\n",
        ),
    ],
    # The official brand tokens (from the brand guidelines) in the token
    # block; the rules below it keep referencing var(--brand-*).
    "domain-brand-palette": [
        (
            "brand.css",
            None,
            "/* brand.css — the site's design tokens. */\n"
            ":root {\n"
            "  --brand-dark: #141413;\n"
            "  --brand-light: #faf9f5;\n"
            "  --brand-mid-gray: #b0aea5;\n"
            "  --brand-light-gray: #e8e6dc;\n"
            "  --brand-accent: #d97757;\n"
            "  --brand-accent-2: #6a9bcc;\n"
            "  --brand-accent-3: #788c5d;\n"
            "  --font-heading: Poppins, Arial, sans-serif;\n"
            "  --font-body: Lora, Georgia, serif;\n"
            "}\n\n"
            ".page {\n"
            "  background: var(--brand-light);\n"
            "  color: var(--brand-dark);\n"
            "  font-family: var(--font-body);\n"
            "}\n\n"
            ".hero-title {\n"
            "  font-family: var(--font-heading);\n"
            "  color: var(--brand-accent);\n"
            "}\n\n"
            ".cta {\n"
            "  background: var(--brand-accent);\n"
            "  color: var(--brand-light);\n"
            "  font-family: var(--font-heading);\n"
            "}\n\n"
            ".feature h2 {\n"
            "  color: var(--brand-mid-gray);\n"
            "}\n",
        ),
    ],
    # The redesigned page: every enumerated tell gone, all content kept.
    "domain-frontend-tells": [
        (
            "index.html",
            None,
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "  <title>Fieldnotes, a home for careful observations</title>\n"
            '  <link rel="stylesheet" href="styles.css">\n'
            "</head>\n"
            "<body>\n"
            '  <header class="hero">\n'
            '    <h1 class="hero-title">A home for careful observations</h1>\n'
            '    <p class="hero-meta">Written by humans, read by anyone</p>\n'
            '    <a class="cta" href="/signup">Start writing</a>\n'
            "  </header>\n"
            "  <main>\n"
            '    <section class="features">\n'
            '      <article class="card">\n'
            "        <h2>Draft in peace</h2>\n"
            "        <p>A light editor that stays out of the way.</p>\n"
            "      </article>\n"
            '      <article class="card">\n'
            "        <h2>Publish when ready</h2>\n"
            "        <p>One click, and your note is a page.</p>\n"
            "      </article>\n"
            '      <article class="card">\n'
            "        <h2>Keep the thread</h2>\n"
            "        <p>Every note keeps its history.</p>\n"
            "      </article>\n"
            "    </section>\n"
            '    <section class="quote">\n'
            "      <blockquote>&ldquo;The notebook is the unit of thought.&rdquo;</blockquote>\n"
            "    </section>\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n",
        ),
        (
            "styles.css",
            None,
            ":root {\n"
            "  --bg: #ffffff;\n"
            "  --ink: #000000;\n"
            "  --accent: #35555e;\n"
            "  --card-bg: #ffffff;\n"
            "  --card-line: #cfd8d4;\n"
            "}\n\n"
            "body {\n"
            "  background: var(--bg);\n"
            "  color: var(--ink);\n"
            "  font-family: Georgia, serif;\n"
            "  margin: 0;\n"
            "}\n\n"
            ".hero {\n"
            "  text-align: center;\n"
            "  padding: 6rem 1.5rem 4rem;\n"
            "}\n\n"
            ".hero-title {\n"
            '  font-family: "Playfair Display", Georgia, serif;\n'
            "  font-size: 3rem;\n"
            "}\n\n"
            ".hero-meta {\n"
            "  font-size: 0.85rem;\n"
            "}\n\n"
            ".cta {\n"
            "  display: inline-block;\n"
            "  background: var(--accent);\n"
            "  color: var(--bg);\n"
            "  padding: 0.75rem 1.5rem;\n"
            "  text-decoration: none;\n"
            "  font-weight: 600;\n"
            "}\n\n"
            ".features {\n"
            "  display: grid;\n"
            "  grid-template-columns: repeat(3, 1fr);\n"
            "  gap: 1.5rem;\n"
            "  padding: 3rem 1.5rem;\n"
            "}\n\n"
            ".card {\n"
            "  background: var(--card-bg);\n"
            "  border: 1px solid var(--card-line);\n"
            "  padding: 1.5rem;\n"
            "}\n\n"
            ".quote {\n"
            "  text-align: center;\n"
            "  padding: 3rem 1.5rem;\n"
            "}\n",
        ),
    ],
}


def materialize(task: dict[str, Any], root: Path) -> Path:
    """Write a fixture's shipped files into a fresh directory."""
    task_dir = root / task["name"]
    task_dir.mkdir(parents=True)
    for rel, content in task["files"].items():
        target = task_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return task_dir


def apply_fix(task_dir: Path, steps: list[tuple[str, str | None, str]]) -> None:
    """Apply the known root-cause fix: (file, None, content) replaces the
    whole file; (file, old, new) splices like the multi verifier."""
    for fix_file, old, new in steps:
        target = task_dir / fix_file
        if old is None:
            target.write_text(new, encoding="utf-8")
            continue
        text = target.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"fix anchor not found in {fix_file}")
        target.write_text(text.replace(old, new), encoding="utf-8")


def run_pytest(task_dir: Path, python: str | None = None) -> tuple[int, str]:
    # PYTHONDONTWRITEBYTECODE: the buggy-state run would leave __pycache__
    # behind; a same-size same-mtime-second fix write then reuses the stale
    # .pyc and the FIXED-state run silently executes the buggy code.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [python or PYTEST, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=task_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def verify_all(python: str | None = None) -> int:
    failures: list[str] = []
    for task in DOMAIN_TASKS:
        name = task["name"]
        fix = DOMAIN_FIXES.get(name)
        if fix is None:
            failures.append(f"{name}: no fix defined")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = materialize(task, Path(tmp))
            code, out = run_pytest(task_dir, python)
            if code == 0:
                failures.append(f"{name}: FAILING-state passes (fixture is not buggy)")
                continue
            apply_fix(task_dir, fix)
            code, out = run_pytest(task_dir, python)
            if code != 0:
                failures.append(f"{name}: FIXED-state still fails:\n{out[-400:]}")
        status = "OK" if not any(f.startswith(name) for f in failures) else "BAD"
        print(f"{status}  {name}")

    if failures:
        print("\nFIXTURE PROBLEMS:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        f"\nAll {len(DOMAIN_TASKS)} domain fixtures verified: "
        "failing-when-buggy, passing-when-fixed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(verify_all())
