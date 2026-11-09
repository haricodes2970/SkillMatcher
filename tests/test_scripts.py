"""Behaviour tests for the dynamic skill inventory and matcher.

These tests intentionally use temporary directory trees instead of the developer's real
skill folders. That keeps the suite deterministic while still exercising the same discovery
rules used against ~/.config/opencode/skills and project-local skill sources.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import match_skills
import scan_skills


def write_skill(path: Path, *, name: str, description: str, extra: str = "") -> None:
    """Create a minimal SKILL.md fixture with valid frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_scan_source_discovers_nested_and_flat_skills(tmp_path: Path) -> None:
    """Both supported layouts must be indexed, including grouped directories."""
    write_skill(
        tmp_path / "group" / "release" / "SKILL.md",
        name="Release Helper",
        description="Prepare changelogs and releases",
    )
    (tmp_path / "review.md").write_text(
        "---\nname: Review\ndescription: Review pull requests\n---\n", encoding="utf-8"
    )

    skills = scan_skills.scan_source(tmp_path, source="test")

    assert {skill["id"] for skill in skills} == {"release", "review"}
    assert all(skill["source"] == "test" for skill in skills)


def test_scan_source_skips_hidden_node_modules_and_deep_trees(tmp_path: Path) -> None:
    """Noise directories and skills deeper than the six-level discovery limit are ignored."""
    write_skill(tmp_path / ".hidden" / "SKILL.md", name="Hidden", description="hidden")
    write_skill(tmp_path / "node_modules" / "pkg" / "SKILL.md", name="Node", description="node")
    write_skill(tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "SKILL.md", name="Deep", description="deep")
    write_skill(tmp_path / "visible" / "SKILL.md", name="Visible", description="visible")

    skills = scan_skills.scan_source(tmp_path, source="test")

    assert {skill["id"] for skill in skills} == {"visible"}


def test_scan_source_has_no_fixed_skill_limit(tmp_path: Path) -> None:
    """The inventory must grow with the install base instead of capping at a magic number."""
    for index in range(60):
        write_skill(
            tmp_path / f"skill-{index:02d}" / "SKILL.md",
            name=f"Skill {index}",
            description=f"Capability number {index}",
        )

    skills = scan_skills.scan_source(tmp_path, source="test")

    assert len(skills) == 60
    assert skills[0]["id"] == "skill-00"
    assert skills[-1]["id"] == "skill-59"


def test_scan_skills_higher_precedence_source_wins_duplicate_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate ID from a higher-precedence source must replace the lower one."""
    cwd = tmp_path / "project"
    home = tmp_path / "home"
    write_skill(
        home / ".config" / "opencode" / "skills" / "duplicate" / "SKILL.md",
        name="Global duplicate",
        description="Global copy",
    )
    extra = tmp_path / "extra" / "duplicate" / "SKILL.md"
    write_skill(extra, name="Extra duplicate", description="Higher precedence copy")
    monkeypatch.setenv("HOME", str(home))

    skills = scan_skills.scan_skills(
        cwd=cwd,
        home=home,
        extra_paths=[extra.parent],
        include_config=False,
    )

    duplicate = next(skill for skill in skills if skill["id"] == "duplicate")
    assert duplicate["name"] == "Extra duplicate"
    assert duplicate["source"] == "extra:cli"


def test_frontmatter_reads_metadata_and_block_scalars(tmp_path: Path) -> None:
    """Parser must handle folded descriptions plus nested metadata without PyYAML."""
    skill_file = tmp_path / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\n"
        "name: Demo\n"
        "description: >-\n"
        "  Line one that explains the skill\n"
        "  and continues on another line.\n"
        "metadata:\n"
        "  version: '2.1.0'\n"
        "  domain: testing\n"
        "  triggers: pytest, coverage\n"
        "  opencode/autoinvoke: false\n"
        "---\n",
        encoding="utf-8",
    )

    parsed = scan_skills.parse_frontmatter(skill_file)

    assert parsed is not None
    assert parsed["description"] == "Line one that explains the skill and continues on another line."
    assert parsed["metadata"]["version"] == "2.1.0"
    assert parsed["metadata"]["opencode/autoinvoke"] == "false"


def test_match_ranks_relevant_skill_first_and_reports_tiers() -> None:
    """A clear Python request must outrank unrelated frontend skills."""
    skills = [
        {
            "id": "python-pro",
            "name": "python-pro",
            "description": "Build typed Python applications, scripts, tests, and packaging",
            "path": "/skills/python-pro/SKILL.md",
            "tags": "python, pytest, typing",
        },
        {
            "id": "react-expert",
            "name": "react-expert",
            "description": "Build React components and frontend interfaces",
            "path": "/skills/react-expert/SKILL.md",
            "tags": "react, frontend, ui",
        },
    ]

    result = match_skills.score_skills(skills, "build a Python command line calculator")

    assert result["ranked_skills"][0]["id"] == "python-pro"
    assert result["tiers"]["core"][0]["id"] == "python-pro"
    assert result["total_skills"] == 2


def test_match_excludes_itself_and_clusters_similar_descriptions() -> None:
    """Self-selection must never happen, and identical descriptions must cluster."""
    shared = "Build landing pages, marketing sites, hero sections, and conversion funnels"
    skills = [
        {
            "id": "skill-matcher",
            "name": "skill-matcher",
            "description": "Build landing pages and orchestrate skills",
            "path": "/skills/skill-matcher/SKILL.md",
        },
        {
            "id": "landing-a",
            "name": "landing-a",
            "description": shared,
            "path": "/skills/landing-a/SKILL.md",
        },
        {
            "id": "landing-b",
            "name": "landing-b",
            "description": shared,
            "path": "/skills/landing-b/SKILL.md",
        },
    ]

    result = match_skills.score_skills(skills, "build a landing page")

    ranked_ids = [skill["id"] for skill in result["ranked_skills"]]
    assert "skill-matcher" not in ranked_ids
    assert result["clusters"]
    assert set(result["clusters"][0]["members"]) == {"landing-a", "landing-b"}


def test_generic_construction_verbs_are_ignored() -> None:
    """Words such as build/create should not make unrelated skills look relevant."""
    skills = [
        {
            "id": "python-pro",
            "name": "python-pro",
            "description": "Python scripts and tests",
            "path": "/skills/python-pro/SKILL.md",
        }
    ]

    result = match_skills.score_skills(skills, "build a python script")

    assert "build" not in result["query_terms"]
    assert result["query_terms"] == ["python", "script"]


def test_specific_python_skill_beats_framework_that_mentions_python() -> None:
    """A generic stack mention must not beat a skill whose name is the requested stack."""
    skills = [
        {
            "id": "fastapi-expert",
            "name": "fastapi-expert",
            "description": (
                "Use when building high-performance async Python APIs with FastAPI and "
                "Pydantic. Create REST endpoints and Python API integrations."
            ),
            "path": "/skills/fastapi-expert/SKILL.md",
            "tags": "FastAPI, Pydantic, async Python, Python API, REST API Python",
        },
        {
            "id": "python-pro",
            "name": "python-pro",
            "description": (
                "Use when building Python applications requiring type safety and robust "
                "error handling. Writes type-annotated Python code and pytest tests."
            ),
            "path": "/skills/python-pro/SKILL.md",
            "tags": "Python development, type hints, pytest, Python best practices",
        },
    ]

    result = match_skills.score_skills(skills, "build a simple calculator in Python")

    assert result["ranked_skills"][0]["id"] == "python-pro"


def test_phrase_match_beats_unrelated_landing_zone() -> None:
    """'landing page' must prefer page design over cloud 'landing zone' terminology."""
    skills = [
        {
            "id": "cloud-architect",
            "name": "cloud-architect",
            "description": "Designs cloud landing zones, migrations, and disaster recovery.",
            "path": "/skills/cloud-architect/SKILL.md",
            "tags": "AWS, Azure, landing zone, cloud architecture",
        },
        {
            "id": "designing-frontend-interfaces",
            "name": "designing-frontend-interfaces",
            "description": "Use for landing pages, marketing sites, web apps, and UI layouts.",
            "path": "/skills/designing-frontend-interfaces/SKILL.md",
        },
    ]

    result = match_skills.score_skills(skills, "build a landing page")

    assert result["ranked_skills"][0]["id"] == "designing-frontend-interfaces"


def test_match_hides_autoinvoke_false_skills_by_default() -> None:
    """Skills hidden from OpenCode's advertised list must not be auto-selected."""
    skills = [
        {
            "id": "secret",
            "name": "secret",
            "description": "Python helper",
            "path": "/skills/secret/SKILL.md",
            "advertised": False,
        },
        {
            "id": "python-pro",
            "name": "python-pro",
            "description": "Python helper",
            "path": "/skills/python-pro/SKILL.md",
        },
    ]

    default_result = match_skills.score_skills(skills, "python helper")
    hidden_result = match_skills.score_skills(skills, "python helper", include_hidden=True)

    assert [s["id"] for s in default_result["ranked_skills"]] == ["python-pro"]
    assert {s["id"] for s in hidden_result["ranked_skills"]} == {"python-pro", "secret"}


def test_cli_accepts_saved_inventory(tmp_path: Path) -> None:
    """The documented command must work with a JSON file on disk."""
    inventory = [
        {
            "id": "python-pro",
            "name": "python-pro",
            "description": "Python scripts and tests",
            "path": "/skills/python-pro/SKILL.md",
        }
    ]
    inventory_file = tmp_path / "skills.json"
    inventory_file.write_text(json.dumps(inventory), encoding="utf-8")

    result = match_skills.run_match(inventory_file, "python script")

    assert result["ranked_skills"][0]["id"] == "python-pro"
