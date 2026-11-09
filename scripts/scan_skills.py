#!/usr/bin/env python3
"""Inventory every skill OpenCode can see and emit it as JSON.

Why this exists
---------------
A fixed hard-coded list of skills becomes stale the moment a user installs another skill.
This scanner mirrors OpenCode V2's dynamic discovery rules, so the matcher sees the current
inventory every time it runs: 50 skills today, 100 tomorrow, no code changes required.

Discovery sources (lowest to highest precedence)
------------------------------------------------
1. Legacy standalone directories (``~/.opencode/skills``, ``./skills``) kept for compatibility.
2. Global compatibility directories: ``~/.claude/skills`` and ``~/.agents/skills``.
3. OpenCode's global source: ``~/.config/opencode/skills``.
4. Project compatibility sources: ``.claude/skills`` and ``.agents/skills`` in every ancestor.
5. Project OpenCode sources: ``.opencode/skills`` in every ancestor.
6. Local paths listed in the ``skills`` array of any ``opencode.json(c)``.
7. Optional paths from ``--extra-path`` or ``SKILL_MATCHER_EXTRA_PATHS``.

Higher-precedence sources override lower-precedence sources that use the same skill ID, which
matches OpenCode's "later source wins" selection rule. The JSON still contains every other
skill, because the matcher needs the complete inventory before choosing a subset.

Skill layouts supported
-----------------------
* ``<source>/git-release/SKILL.md``          (directory skill, ID ``git-release``)
* ``<source>/teams/release/SKILL.md``        (grouped skill, ID ``release``)
* ``<source>/review.md``                     (flat markdown skill, ID ``review``)

Hidden directories, ``node_modules``, and anything deeper than six levels below a source root
are skipped, exactly like OpenCode. A directory containing ``SKILL.md`` is treated as one skill
and is not searched for nested skills.

Only the Python standard library is used. Warnings go to stderr so stdout stays valid JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# OpenCode stops searching six directories below a source root. Keeping the same limit avoids
# accidentally treating an unrelated deep SKILL.md as installed.
MAX_DISCOVERY_DEPTH = 6

# These directories are never skills, even if they contain a stray SKILL.md.
SKIP_DIR_NAMES = {"node_modules", "__pycache__", ".git"}

# Skill IDs are path-derived in OpenCode. This pattern is a portable safety recommendation,
# not a hard requirement in OpenCode V2, so the scanner reports IDs without rejecting them.
PORTABLE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _unquote(value: str) -> str:
    """Remove matching single/double quotes from a scalar YAML value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_frontmatter(file_path: Path) -> dict[str, Any] | None:
    """Parse the small YAML subset used by SKILL.md frontmatter.

    A full YAML dependency would make the skill harder to install on machines without PyYAML.
    Skill frontmatter is simple: top-level scalars, optional nested ``metadata:`` entries, and
    folded/literal block scalars. This parser intentionally supports only that subset.

    Args:
        file_path: Markdown file to read.

    Returns:
        Parsed frontmatter as a dictionary, or ``None`` when the file is missing, unreadable,
        or has no complete ``---`` block.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    data: dict[str, Any] = {}
    block_markers = {">", ">-", ">+", "|", "|-", "|+"}
    current_key: str | None = None
    current_is_block = False
    in_metadata = False
    metadata_indent: int | None = None

    for line in parts[1].splitlines():
        line_str = line.rstrip()
        stripped = line_str.strip()

        # Blank lines and YAML comments carry no useful metadata for matching.
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line_str) - len(line_str.lstrip())

        # Top-level "key: value" entries.
        if indent == 0 and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()

            if key == "metadata":
                in_metadata = True
                metadata_indent = None
                data["metadata"] = {}
                current_key = "metadata"
                current_is_block = False
                continue

            in_metadata = False
            current_key = key
            if raw_value in block_markers:
                # Folded/literal blocks are accumulated from following indented lines.
                data[key] = ""
                current_is_block = True
            else:
                data[key] = _unquote(raw_value)
                current_is_block = False
            continue

        # Nested metadata entries, e.g. version, domain, triggers, opencode/autoinvoke.
        if in_metadata and indent > 0:
            if metadata_indent is None:
                metadata_indent = indent
            if indent >= metadata_indent and ":" in stripped:
                key, raw_value = stripped.split(":", 1)
                data["metadata"][key.strip()] = _unquote(raw_value)
            continue

        # Continuation lines for descriptions and other block scalars.
        if current_key and current_key != "metadata" and current_key in data:
            text = stripped
            if current_is_block and text[:1] in (">", "|"):
                text = text[1:].strip()
            text = _unquote(text)
            separator = " " if data[current_key] else ""
            data[current_key] = (data[current_key] + separator + text).strip()

    return data


def _split_tags(raw: Any) -> list[str]:
    """Normalise tags/triggers into a list, tolerating commas or YAML-like lists."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _parse_skill_file(skill_file: Path, skill_id: str, source: str) -> dict[str, Any] | None:
    """Turn one SKILL.md or flat markdown file into an inventory record."""
    frontmatter = parse_frontmatter(skill_file)
    if frontmatter is None:
        return None

    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    # OpenCode uses the path-derived ID for activation. The frontmatter name is only a display
    # label, so keep both fields instead of conflating them.
    display_name = str(frontmatter.get("name") or skill_id)
    description = str(frontmatter.get("description") or "")

    tags = _split_tags(
        metadata.get("tags")
        or frontmatter.get("tags")
        or metadata.get("triggers")
        or frontmatter.get("triggers")
    )
    use_case = (
        metadata.get("use-case")
        or metadata.get("use_case")
        or frontmatter.get("use-case")
        or frontmatter.get("use_case")
    )
    version = metadata.get("version") or frontmatter.get("version")
    domain = metadata.get("domain") or frontmatter.get("domain")

    # OpenCode hides these skills from the model's advertised list, but still allows an explicit
    # load. The matcher must not auto-activate a skill its author marked as hidden.
    autoinvoke = metadata.get("opencode/autoinvoke", frontmatter.get("disable-model-invocation"))
    advertised = str(autoinvoke).strip().lower() not in {"false", "0", "no", "off"}

    record: dict[str, Any] = {
        "id": skill_id,
        "name": display_name,
        "description": description,
        "path": str(skill_file.resolve()),
        "source": source,
        "advertised": advertised,
    }
    if tags:
        record["tags"] = ", ".join(tags)
    if use_case:
        record["use_case"] = str(use_case)
    if version:
        record["version"] = str(version)
    if domain:
        record["domain"] = str(domain)
    if not PORTABLE_ID_PATTERN.fullmatch(skill_id):
        # Do not reject the skill; surface the portability issue for the user instead.
        record["id_warning"] = "ID is not portable lowercase kebab-case"
    return record


def scan_source(source_path: Path, source: str = "custom") -> list[dict[str, Any]]:
    """Scan one source root for skills using OpenCode's depth/layout rules.

    Args:
        source_path: Directory that may contain flat markdown skills or nested SKILL.md files.
        source: Human-readable source label stored on each record (for example
            ``global:opencode`` or ``project:.opencode``).

    Returns:
        A deterministic list of skill records sorted by skill ID. An unreadable/missing source
        returns an empty list rather than failing the whole inventory.
    """
    root = source_path.expanduser()
    try:
        root = root.resolve()
    except OSError:
        return []
    if not root.is_dir():
        return []

    found: dict[str, dict[str, Any]] = {}
    visited_dirs: set[Path] = set()

    for current_dir, dir_names, file_names in os.walk(root, topdown=True, followlinks=True):
        current = Path(current_dir)

        # Symlinked compatibility directories are common (~/.agents/skills/foo -> another dir).
        # Track resolved paths so a symlink loop cannot make the scan run forever.
        try:
            real_current = current.resolve()
        except OSError:
            dir_names[:] = []
            continue
        if real_current in visited_dirs:
            dir_names[:] = []
            continue
        visited_dirs.add(real_current)

        relative = current.relative_to(root)
        depth = len(relative.parts)

        # Keep walking deterministic and skip hidden/noise directories. A skill directory is
        # pruned immediately below, because OpenCode treats it as one skill and does not search
        # inside it for more skills.
        dir_names[:] = sorted(
            name
            for name in dir_names
            if not name.startswith(".") and name not in SKIP_DIR_NAMES
        )
        if depth >= MAX_DISCOVERY_DEPTH:
            dir_names[:] = []

        if "SKILL.md" in file_names:
            skill_id = root.name if current == root else current.name
            record = _parse_skill_file(current / "SKILL.md", skill_id, source)
            if record is not None and skill_id not in found:
                found[skill_id] = record
            # Do not descend into a directory that already defines a skill.
            dir_names[:] = []

        # Flat markdown skills are only valid at the source root. SKILL.md was handled above.
        if depth == 0:
            for file_name in sorted(file_names):
                if file_name == "SKILL.md" or file_name.startswith("."):
                    continue
                if not file_name.lower().endswith(".md"):
                    continue
                skill_id = Path(file_name).stem
                record = _parse_skill_file(current / file_name, skill_id, source)
                if record is not None and skill_id not in found:
                    found[skill_id] = record

    return [found[skill_id] for skill_id in sorted(found)]


def _ancestors_far_to_near(start: Path) -> list[Path]:
    """Return the current directory and every ancestor, farthest first.

    OpenCode searches project sources from the project root toward the current directory. We
    include all ancestors so a skill installed at any inherited project level is visible; later
    (nearer) sources win when IDs collide.
    """
    return list(reversed([start, *start.parents]))


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments plus trailing commas so json.loads can read JSONC."""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0

    while index < len(text):
        char = text[index]

        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < len(text):
            next_char = text[index + 1]
            if next_char == "/":
                index += 2
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if next_char == "*":
                index += 2
                while index + 1 < len(text) and text[index : index + 2] != "*/":
                    index += 1
                index += 2
                continue

        output.append(char)
        index += 1

    # Trailing commas are legal in JSONC but not in JSON.
    return re.sub(r",(\s*[}\]])", r"\1", "".join(output))


def _config_skill_paths(cwd: Path, home: Path) -> list[tuple[Path, str]]:
    """Read local entries from every opencode.json/c ``skills`` array.

    HTTP catalogs are intentionally skipped: downloading arbitrary catalogs during a local
    inventory would add latency and network failure modes. Local directories cover installed
    skills; remote catalog support can be added without changing the rest of the protocol.
    """
    config_files: list[Path] = [
        home / ".config" / "opencode" / "opencode.json",
        home / ".config" / "opencode" / "opencode.jsonc",
    ]

    # Direct config overrides .opencode config, so collect direct files first.
    ancestors = _ancestors_far_to_near(cwd)
    config_files.extend(path / "opencode.json" for path in ancestors)
    config_files.extend(path / "opencode.jsonc" for path in ancestors)
    config_files.extend(path / ".opencode" / "opencode.json" for path in ancestors)
    config_files.extend(path / ".opencode" / "opencode.jsonc" for path in ancestors)

    paths: list[tuple[Path, str]] = []
    seen_files: set[Path] = set()
    for config_file in config_files:
        try:
            real_config = config_file.resolve()
        except OSError:
            continue
        if real_config in seen_files or not real_config.is_file():
            continue
        seen_files.add(real_config)

        try:
            config = json.loads(_strip_jsonc(real_config.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            print(f"warning: cannot read {real_config}: {error}", file=sys.stderr)
            continue

        entries = config.get("skills", [])
        if not isinstance(entries, list):
            print(f"warning: 'skills' in {real_config} is not an array; ignored", file=sys.stderr)
            continue

        for entry in entries:
            if not isinstance(entry, str):
                continue
            if entry.startswith(("http://", "https://")):
                print(f"warning: remote skill catalog not scanned: {entry}", file=sys.stderr)
                continue
            path = Path(entry).expanduser()
            if not path.is_absolute():
                # OpenCode resolves relative explicit paths from the working directory, not
                # from the config file that declared them.
                path = cwd / path
            paths.append((path, f"explicit:{real_config}"))

    return paths


def discover_sources(
    cwd: Path | None = None,
    home: Path | None = None,
    include_config: bool = True,
) -> list[tuple[Path, str]]:
    """Build the ordered, de-duplicated list of skill source roots to scan.

    The order is lowest precedence to highest. Duplicate source paths keep their last
    occurrence, which mirrors OpenCode's "later source wins" behaviour.
    """
    cwd = (cwd or Path.cwd()).expanduser().resolve()
    home = (home or Path.home()).expanduser().resolve()
    ancestors = _ancestors_far_to_near(cwd)

    sources: list[tuple[Path, str]] = [
        (home / ".opencode" / "skills", "legacy:global"),
        (cwd / "skills", "legacy:project"),
    ]

    # Compatibility sources are global first, then every project ancestor.
    sources.append((home / ".claude" / "skills", "global:claude"))
    sources.extend((path / ".claude" / "skills", f"project:claude:{path}") for path in ancestors)
    sources.append((home / ".agents" / "skills", "global:agents"))
    sources.extend((path / ".agents" / "skills", f"project:agents:{path}") for path in ancestors)

    # Native OpenCode sources.
    sources.append((home / ".config" / "opencode" / "skills", "global:opencode"))
    sources.extend((path / ".opencode" / "skills", f"project:opencode:{path}") for path in ancestors)

    if include_config:
        sources.extend(_config_skill_paths(cwd, home))

    # Highest precedence: explicit user/test additions.
    extra_env = os.environ.get("SKILL_MATCHER_EXTRA_PATHS", "")
    for entry in extra_env.split(os.pathsep):
        if entry.strip():
            sources.append((Path(entry).expanduser(), "extra:env"))

    # Keep the last occurrence of a source path and move it to the end of the scan order, so a
    # higher-precedence alias (for example a project ancestor that is also the global home
    # source) really wins instead of keeping its earlier position.
    deduped: dict[str, tuple[Path, str]] = {}
    for path, label in sources:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path.expanduser())
        if key in deduped:
            del deduped[key]
        deduped[key] = (path.expanduser(), label)
    return list(deduped.values())


def scan_skills(
    cwd: Path | None = None,
    extra_paths: Iterable[Path] = (),
    include_config: bool = True,
    home: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan all discovered sources and merge duplicate IDs by precedence.

    Args:
        cwd: Active project directory. Defaults to the process working directory.
        extra_paths: Additional source roots from the CLI. These have highest precedence.
        include_config: Whether to read ``skills`` arrays from opencode.json(c).
        home: Override the home directory. Primarily useful for isolated tests.

    Returns:
        Every discoverable skill record, sorted by ID. Same-ID records are resolved in favour
        of the later source, matching OpenCode.
    """
    cwd = (cwd or Path.cwd()).expanduser().resolve()
    sources = discover_sources(cwd=cwd, home=home, include_config=include_config)
    sources.extend((Path(path).expanduser(), "extra:cli") for path in extra_paths)

    merged: dict[str, dict[str, Any]] = {}
    for source_path, source_label in sources:
        for skill in scan_source(source_path, source=source_label):
            # Later sources override earlier sources with the same ID.
            merged[skill["id"]] = skill

    return [merged[skill_id] for skill_id in sorted(merged)]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. JSON goes to stdout; diagnostics go to stderr."""
    parser = argparse.ArgumentParser(
        description="Inventory OpenCode skills from every configured source as JSON."
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Project directory used for project-local discovery (default: current directory).",
    )
    parser.add_argument(
        "--extra-path",
        action="append",
        default=[],
        help="Additional skill source root. Repeat for multiple roots.",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore explicit 'skills' arrays in opencode.json(c).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print source and skill counts to stderr.",
    )
    args = parser.parse_args(argv)

    cwd = Path(args.cwd).expanduser().resolve()
    extra_paths = [Path(path) for path in args.extra_path]
    skills = scan_skills(cwd=cwd, extra_paths=extra_paths, include_config=not args.no_config)

    if args.stats:
        sources = discover_sources(cwd=cwd, include_config=not args.no_config)
        sources.extend((path, "extra:cli") for path in extra_paths)
        print(f"scanned {len(sources)} source roots", file=sys.stderr)
        print(f"found {len(skills)} unique skills", file=sys.stderr)

    json.dump(skills, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
