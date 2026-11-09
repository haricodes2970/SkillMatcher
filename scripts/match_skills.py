#!/usr/bin/env python3
"""Rank an installed-skill inventory against a task query and detect overlaps.

How the score works
-------------------
Each skill is converted into a weighted bag of words:

* skill ID + display name  -> 3x weight (names are strong intent signals)
* tags + use-case + domain -> 2x weight (explicit author intent)
* description              -> 1x weight (natural-language detail)

TF-IDF then down-weights words that appear in nearly every skill. The final value is a cosine
similarity, so scores are comparable across queries instead of being normalised until the top
result is always 1.0. That matters: a weak match should look weak, not confident.

The output also contains relative tiers. They are suggestions, not automatic activation:
the agent still applies ``references/disambiguation.md`` before calling the native skill tool.
Tiers make it easy to select "many useful skills" without loading hundreds of weak matches.

Clusters use Jaccard similarity over description tokens. Two skills whose descriptions overlap
by at least 0.35 are treated as competing candidates and must be resolved by use case rather
than both being activated for the same sub-task.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

# Never let the orchestrator select itself. Re-entering this protocol adds no implementation
# value and can create an activation loop.
SELF_IDS = {"skill-matcher"}

# Common English words add noise to short prompts. The list is intentionally small: overly
# aggressive stop-word removal can erase meaningful technical terms such as "build" or "test".
STOP_WORDS = {
    "a", "add", "an", "and", "are", "as", "at", "be", "been", "being", "build", "but", "by",
    "can", "could", "create", "did", "do", "does", "for", "from", "generate", "had", "has",
    "have", "he", "her", "here", "him", "his", "how", "i", "if", "implement", "in", "into",
    "is", "it", "its", "make", "me", "my", "of", "on", "or", "our", "out", "over", "she",
    "should", "simple", "so", "some", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under", "up", "us",
    "use", "used", "using", "very", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "why", "will", "with", "would", "write", "you", "your",
}

# Relative tier thresholds. A skill must clear both the relative and absolute floor so a weak
# query cannot promote dozens of unrelated skills.
CORE_RATIO = 0.55
CORE_FLOOR = 0.05
SUPPORT_RATIO = 0.30
SUPPORT_FLOOR = 0.03
REFERENCE_FLOOR = 0.005

CORE_LIMIT = 5
SUPPORT_LIMIT = 10
CLUSTER_THRESHOLD = 0.35

# A top score below this is a weak suggestion, not a confident match. The protocol tells the
# agent to report that uncertainty instead of pretending the ranking is authoritative.
CONFIDENT_SCORE = 0.18

# Adjacent query words carry intent that bag-of-words scoring loses: "landing page" is a web
# design task, while "landing zone" is cloud infrastructure. A small phrase bonus separates
# those cases without replacing the TF-IDF ranking.
PHRASE_BONUS = 0.12


def tokenize(text: str) -> list[str]:
    """Convert text to lowercase technical tokens.

    Hyphenated terms are kept intact and also split into parts, so ``landing-page`` matches
    both ``landing-page`` and ``landing page`` without a special-case phrase dictionary.
    """
    tokens: list[str] = []
    for raw in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_+.#-]*", text.lower()):
        token = raw.strip(".-_#")
        if len(token) < 2 or token in STOP_WORDS:
            continue
        tokens.append(token)
        if "-" in token:
            tokens.extend(
                part for part in token.split("-") if len(part) > 1 and part not in STOP_WORDS
            )
    return tokens


def tag_list(skill: dict[str, Any]) -> list[str]:
    """Return a skill's tags/use-case/domain as a flat list of strings."""
    values: list[str] = []
    for key in ("tags", "use_case", "domain"):
        raw = skill.get(key)
        if raw is None:
            continue
        if isinstance(raw, list):
            values.extend(str(item) for item in raw)
        else:
            values.extend(part.strip() for part in str(raw).split(",") if part.strip())
    return values


def _skill_id(skill: dict[str, Any]) -> str:
    """Return the activation ID, falling back to name/path for older inventories."""
    if skill.get("id"):
        return str(skill["id"])
    if skill.get("name"):
        return str(skill["name"])
    path = Path(str(skill.get("path", "unknown")))
    return path.parent.name if path.name == "SKILL.md" else path.stem


def _document_tokens(skill: dict[str, Any]) -> list[str]:
    """Build the weighted token list used for TF-IDF scoring."""
    name_tokens = tokenize(_skill_id(skill)) + tokenize(str(skill.get("name", "")))
    tag_tokens = tokenize(" ".join(tag_list(skill)))
    description_tokens = tokenize(str(skill.get("description", "")))
    # Repetition is the weight: names 3x, author tags 2x, descriptions 1x.
    return (name_tokens * 3) + (tag_tokens * 2) + description_tokens


def _description_tokens(skill: dict[str, Any]) -> set[str]:
    """Description-only tokens used for Jaccard overlap clustering."""
    return set(tokenize(str(skill.get("description", ""))))


def _bigrams(tokens: list[str]) -> set[str]:
    """Return adjacent token pairs, e.g. ['landing', 'page'] -> {'landing page'}."""
    return {f"{tokens[index]} {tokens[index + 1]}" for index in range(len(tokens) - 1)}


def _phrase_bonus(query_bigrams: set[str], skill: dict[str, Any]) -> float:
    """Reward skills whose searchable text contains an exact adjacent query phrase."""
    haystack = " ".join(
        tokenize(
            " ".join(
                [
                    _skill_id(skill),
                    str(skill.get("name", "")),
                    str(skill.get("description", "")),
                    " ".join(tag_list(skill)),
                ]
            )
        )
    )
    matches = sum(1 for phrase in query_bigrams if phrase in haystack)
    return min(0.36, matches * PHRASE_BONUS)


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    """Return Jaccard similarity, treating two empty sets as non-overlapping."""
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _prepare_skills(skills: Iterable[dict[str, Any]], include_hidden: bool) -> list[dict[str, Any]]:
    """Drop self-references and hidden skills before scoring."""
    prepared: list[dict[str, Any]] = []
    for skill in skills:
        skill_id = _skill_id(skill)
        if skill_id in SELF_IDS:
            continue
        if not include_hidden and skill.get("advertised") is False:
            continue
        prepared.append(skill)
    return prepared


def _rank(skills: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Score every skill with TF-IDF cosine similarity and return sorted records."""
    query_tokens = tokenize(query)
    if not query_tokens or not skills:
        return []

    documents = [_document_tokens(skill) for skill in skills]
    document_frequency = Counter(token for document in documents for token in set(document))
    total_documents = len(documents)

    # Smooth IDF: a token present in every skill still has a small non-zero weight instead of
    # collapsing to zero and making the entire vector empty.
    idf = {
        token: math.log((total_documents + 1) / (frequency + 1)) + 1.0
        for token, frequency in document_frequency.items()
    }

    query_counts = Counter(query_tokens)
    query_vector = {
        token: (1.0 + math.log(count)) * idf.get(token, 1.0)
        for token, count in query_counts.items()
    }
    query_norm = math.sqrt(sum(value * value for value in query_vector.values())) or 1.0
    query_set = set(query_tokens)
    query_bigrams = _bigrams(query_tokens)

    ranked: list[dict[str, Any]] = []
    for skill, document in zip(skills, documents):
        counts = Counter(document)
        vector = {
            token: (1.0 + math.log(count)) * idf.get(token, 1.0)
            for token, count in counts.items()
        }
        dot_product = sum(
            query_vector[token] * vector[token] for token in query_vector if token in vector
        )
        document_norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        cosine = dot_product / (query_norm * document_norm)

        # A small name-overlap bonus reflects intent: a query mentioning "React" should prefer a
        # skill named react-expert even when both descriptions discuss components.
        name_tokens = set(tokenize(_skill_id(skill)) + tokenize(str(skill.get("name", ""))))
        name_overlap = len(name_tokens & query_set) / max(len(query_set), 1)
        score = min(1.0, cosine + (0.10 * name_overlap) + _phrase_bonus(query_bigrams, skill))

        if score <= 0:
            continue

        record = dict(skill)
        record["id"] = _skill_id(skill)
        record["score"] = round(score, 4)
        record["matched_terms"] = sorted(query_set & set(document))
        ranked.append(record)

    ranked.sort(key=lambda item: (-item["score"], item["id"]))
    return ranked


def _build_clusters(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group skills whose description Jaccard similarity is at least 0.35.

    Greedy clustering is enough here: the goal is to surface overlap, not to produce a perfect
    graph partition. A skill appears in at most one cluster so the agent has an unambiguous
    "these compete with each other" signal.
    """
    tokens_by_id = {skill["id"]: _description_tokens(skill) for skill in ranked}
    used: set[str] = set()
    clusters: list[dict[str, Any]] = []

    for index, skill in enumerate(ranked):
        if skill["id"] in used:
            continue
        members = [skill["id"]]
        similarities: list[float] = []

        for other in ranked[index + 1 :]:
            if other["id"] in used:
                continue
            similarity = jaccard_similarity(tokens_by_id[skill["id"]], tokens_by_id[other["id"]])
            if similarity >= CLUSTER_THRESHOLD:
                members.append(other["id"])
                similarities.append(similarity)
                used.add(other["id"])

        if len(members) > 1:
            used.add(skill["id"])
            clusters.append(
                {
                    "members": members,
                    "similarity": round(min(similarities), 4) if similarities else 0.0,
                    "reason": f"Description Jaccard similarity >= {CLUSTER_THRESHOLD}",
                }
            )

    return clusters


def _build_tiers(ranked: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split ranked skills into relative Core/Support/Reference tiers.

    Core contains the strongest matches (max 5), Support adds cross-cutting skills (max 10),
    and Reference keeps the long tail available for lookup without loading it. The limits are
    enforced here as a guardrail even if the agent ignores the prompt protocol.
    """
    if not ranked:
        return {"core": [], "support": [], "reference": []}

    top_score = ranked[0]["score"]
    core_threshold = max(CORE_FLOOR, top_score * CORE_RATIO)
    support_threshold = max(SUPPORT_FLOOR, top_score * SUPPORT_RATIO)

    core: list[dict[str, Any]] = []
    support: list[dict[str, Any]] = []
    reference: list[dict[str, Any]] = []

    for skill in ranked:
        if len(core) < CORE_LIMIT and skill["score"] >= core_threshold:
            core.append(skill)
        elif len(support) < SUPPORT_LIMIT and skill["score"] >= support_threshold:
            support.append(skill)
        elif skill["score"] >= REFERENCE_FLOOR:
            reference.append(skill)

    return {"core": core, "support": support, "reference": reference}


def score_skills(
    skills: Iterable[dict[str, Any]],
    query: str,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """Score a skill inventory against one query.

    Args:
        skills: Inventory records produced by ``scan_skills.py``.
        query: Atomic sub-task text (not the whole user prompt, when possible).
        include_hidden: Include skills whose author set ``opencode/autoinvoke: false``.

    Returns:
        JSON-ready dictionary with ranked skills, clusters, relative tiers, and confidence.
    """
    prepared = _prepare_skills(skills, include_hidden=include_hidden)
    ranked = _rank(prepared, query)
    tiers = _build_tiers(ranked)
    top_score = ranked[0]["score"] if ranked else 0.0
    confident = bool(ranked) and top_score >= CONFIDENT_SCORE

    return {
        "query": query,
        "total_skills": len(prepared),
        "query_terms": sorted(set(tokenize(query))),
        "confident": confident,
        "top_score": top_score,
        "ranked_skills": ranked,
        "tiers": tiers,
        "clusters": _build_clusters(ranked),
        "note": None
        if confident
        else "No skill reached the confidence floor; treat these as weak suggestions only.",
    }


def run_match(
    skills_json: Path | str,
    query: str,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """Load a saved inventory, score it, and return the result dictionary.

    Kept as a separate function so tests and other tools can call the matcher without spawning
    a process or parsing stdout.
    """
    path = Path(skills_json).expanduser()
    with path.open("r", encoding="utf-8") as handle:
        skills = json.load(handle)
    if not isinstance(skills, list):
        raise ValueError(f"{path} must contain a JSON array of skills")
    return score_skills(skills, query, include_hidden=include_hidden)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Emits only JSON on stdout."""
    parser = argparse.ArgumentParser(
        description="Rank installed skills against a query and identify overlapping clusters."
    )
    parser.add_argument("skills_json", help="Inventory file produced by scan_skills.py")
    parser.add_argument(
        "query",
        nargs="+",
        help="Task or sub-task text. Quote it when it contains spaces.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include skills marked opencode/autoinvoke: false.",
    )
    args = parser.parse_args(argv)

    try:
        result = run_match(args.skills_json, " ".join(args.query), include_hidden=args.include_hidden)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
