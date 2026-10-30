#!/usr/bin/env python3
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

TYPES = ["feat", "fix", "refactor", "ci", "perf", "test", "chore", "docs"]

IMPERATIVE_BASE_FORMS = {
    "added": "add",
    "adds": "add",
    "fixed": "fix",
    "fixes": "fix",
    "updated": "update",
    "updates": "update",
    "removed": "remove",
    "removes": "remove",
    "changed": "change",
    "changes": "change",
    "created": "create",
    "creates": "create",
    "resolved": "resolve",
    "resolves": "resolve",
    "renamed": "rename",
    "renames": "rename",
    "moved": "move",
    "moves": "move",
    "deleted": "delete",
    "deletes": "delete",
    "improved": "improve",
    "improves": "improve",
    "enhanced": "enhance",
    "enhances": "enhance",
    "implemented": "implement",
    "implements": "implement",
    "refactored": "refactor",
    "refactors": "refactor",
    "supported": "support",
    "supports": "support",
}

EXEMPT_PREFIXES = ("Merge ", "Revert ", "fixup! ", "squash! ")

LOCKFILES = frozenset(
    {
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "flake.lock",
        "go.sum",
        "go.work.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)


def fail(context_line: str, reason: str, label: str = "Invalid commit message:") -> NoReturn:
    print(label, file=sys.stderr)
    print(f"  {context_line}", file=sys.stderr)
    print(file=sys.stderr)
    print(reason, file=sys.stderr)
    sys.exit(1)


def comment_char() -> str:
    out = subprocess.run(
        ["git", "config", "--get", "core.commentChar"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    value = out.stdout.strip()
    if not value or value == "auto":
        return "#"
    return value


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return Path(out.stdout.strip())


def tracked_files(root: Path) -> list:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return [path for path in out.stdout.split("\0") if path]


def layout(root: Path):
    names = set()
    inside = {}
    for path in tracked_files(root):
        first, sep, rest = path.partition("/")
        names.add(first)
        if not sep:
            continue
        entry = inside.setdefault(first, {"has_files": False, "units": set()})
        unit, nested, _ = rest.partition("/")
        if nested:
            entry["units"].add(unit)
        else:
            entry["has_files"] = True
    groups = {
        name: entry["units"]
        for name, entry in inside.items()
        if not entry["has_files"] and not name.startswith(".")
    }
    return names, groups


def valid_scopes(names, groups) -> list:
    scopes = set(names)
    for units in groups.values():
        scopes |= units
    return sorted(scopes)


def touched_areas(changed_files, groups):
    filtered = [path for path in changed_files if path not in LOCKFILES]
    if not filtered:
        filtered = changed_files

    areas = set()
    for path in filtered:
        first, _, rest = path.partition("/")
        if first in groups and "/" in rest:
            areas.add((first, rest.split("/", 1)[0]))
        else:
            areas.add((None, first))
    return areas


def changed_files_for(sha):
    if sha:
        cmd = ["git", "diff-tree", "--root", "--no-commit-id", "-r", sha]
    else:
        cmd = ["git", "diff", "--cached"]
    cmd += ["--name-only", "--no-renames", "-z"]
    out = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return [path for path in out.stdout.split("\0") if path]


def main() -> None:
    msg_file = sys.argv[1]
    sha = sys.argv[2] if len(sys.argv) > 2 else None

    char = comment_char()
    lines = [
        line
        for line in Path(msg_file).read_text(encoding="utf-8").splitlines()
        if not line.startswith(char)
    ]
    header = lines[0] if lines else ""

    if header.startswith(EXEMPT_PREFIXES):
        return

    if len(header) > 50:
        fail(
            header,
            f"Subject line is {len(header)} characters, must be 50 or fewer.",
        )

    if len(lines) > 1 and lines[1].strip() != "":
        fail(
            header,
            "Missing blank line: leave an empty line between the subject "
            "and the body.",
        )

    for line in lines[2:]:
        if len(line) > 72:
            fail(
                line,
                f"Body line is {len(line)} characters, must be 72 or fewer.",
                label="Invalid commit message body:",
            )

    names, groups = layout(repo_root())
    scopes = valid_scopes(names, groups)
    types_re = "|".join(TYPES)
    scope_re = "|".join(re.escape(s) for s in scopes) if scopes else "(?!)"
    scope_part = rf"(?:\((?P<scope>{scope_re})\))?"
    pattern = rf"^(?P<type>{types_re}){scope_part}: (?P<desc>[a-z0-9].*[^.\s])$"

    match = re.match(pattern, header)
    if not match:
        fail(
            header,
            "Expected: <type>(<scope>): <imperative description>\n"
            "  scope is optional: <type>: <description> is also valid\n"
            "  scope must match a file or folder name exactly, as it appears in the repo\n"
            f"  type:  {' '.join(TYPES)}\n"
            f"  scope: {' '.join(scopes)}\n"
            "  example: fix(windows): handle null pointer dereference in shell enumeration",
        )

    commit_type = match.group("type")
    written_scope = match.group("scope")
    description = match.group("desc")

    first_word = description.split(" ", 1)[0]
    base_form = IMPERATIVE_BASE_FORMS.get(first_word)
    if base_form:
        fail(
            header,
            f"Use imperative mood: '{first_word}' should be '{base_form}' "
            f"(write what the commit does, not what it did).",
        )

    changed_files = changed_files_for(sha)
    if not changed_files:
        return

    areas = touched_areas(changed_files, groups)
    touched = sorted(name for _, name in areas)
    touched_groups = {group for group, _ in areas}
    files_note = f"  files touched: {' '.join(changed_files)}"

    if len(touched) == 1:
        if written_scope and written_scope != touched[0]:
            fail(
                header,
                f"This commit only touches '{touched[0]}', but the scope says "
                f"'{written_scope}'.\n{files_note}",
            )
        if touched[0] == ".github" and commit_type != "ci":
            fail(
                header,
                f"This commit only touches '.github', use type 'ci' (not "
                f"'{commit_type}') so the changelog skips it.\n{files_note}",
            )
    elif len(touched_groups) == 1 and None not in touched_groups:
        group = next(iter(touched_groups))
        if written_scope and written_scope != group:
            fail(
                header,
                f"This commit touches multiple units of '{group}' "
                f"({' '.join(touched)}); use scope '{group}' or omit the "
                f"scope.\n{files_note}",
            )
    else:
        if written_scope:
            fail(
                header,
                f"This commit touches multiple unrelated areas "
                f"({' '.join(touched)}); omit the scope.\n{files_note}",
            )


if __name__ == "__main__":
    main()
