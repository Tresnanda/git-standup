"""Author alias helpers for merging identities in standup output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

_ALIAS_ASSIGNMENT_RE = re.compile(r"^\s*(?P<canonical>[^=]+?)\s*=\s*(?P<aliases>.+)\s*$")


def normalize_identity(value: str) -> str:
    """Return a stable comparison key for an author name, email, or login."""
    return re.sub(r"\s+", " ", value.strip()).casefold()


@dataclass(frozen=True)
class AuthorAliases:
    """A small canonical-author roster.

    ``groups`` maps the display name to alternate names, emails, or logins that
    should be treated as that same person. Canonical names match themselves too.
    """

    groups: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_mapping(cls, groups: Mapping[str, Sequence[str]] | None) -> "AuthorAliases":
        if not groups:
            return cls({})
        cleaned: dict[str, tuple[str, ...]] = {}
        for canonical, aliases in groups.items():
            canonical_name = str(canonical).strip()
            if not canonical_name:
                continue
            values: list[str] = []
            seen: set[str] = {normalize_identity(canonical_name)}
            for alias in aliases:
                value = str(alias).strip()
                key = normalize_identity(value)
                if value and key not in seen:
                    values.append(value)
                    seen.add(key)
            cleaned[canonical_name] = tuple(values)
        return cls(cleaned)

    def merge(self, other: "AuthorAliases") -> "AuthorAliases":
        """Return aliases with ``other`` appended, preserving existing order."""
        merged: dict[str, list[str]] = {
            canonical: list(aliases) for canonical, aliases in self.groups.items()
        }
        for canonical, aliases in other.groups.items():
            bucket = merged.setdefault(canonical, [])
            seen = {normalize_identity(canonical), *(normalize_identity(item) for item in bucket)}
            for alias in aliases:
                key = normalize_identity(alias)
                if key not in seen:
                    bucket.append(alias)
                    seen.add(key)
        return AuthorAliases.from_mapping(merged)

    def canonical_for(
        self,
        *,
        name: str | None = None,
        email: str | None = None,
        login: str | None = None,
    ) -> str | None:
        """Return the canonical display name for a matching identity, if any."""
        lookup = self._lookup()
        for value in (name, email, login):
            if not value:
                continue
            canonical = lookup.get(normalize_identity(value))
            if canonical:
                return canonical
        return None

    def expand_filter(self, author: str | None) -> str | None:
        """Expand an author filter to include configured aliases.

        Existing filters are returned unchanged when no alias group matches. A
        pipe-separated filter expands each part independently.
        """
        if not author or not self.groups:
            return author
        parts = [part.strip() for part in author.split("|") if part.strip()]
        if not parts:
            return author
        expanded: list[str] = []
        changed = False
        for part in parts:
            group = self._group_for_identity(part)
            if group is None:
                expanded.append(part)
                continue
            changed = True
            canonical, aliases = group
            expanded.extend((canonical, *aliases))
        if not changed:
            return author
        return "|".join(_dedupe_preserving_order(expanded))

    def _lookup(self) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for canonical, aliases in self.groups.items():
            lookup[normalize_identity(canonical)] = canonical
            for alias in aliases:
                lookup[normalize_identity(alias)] = canonical
        return lookup

    def _group_for_identity(self, identity: str) -> tuple[str, tuple[str, ...]] | None:
        key = normalize_identity(identity)
        for canonical, aliases in self.groups.items():
            if key == normalize_identity(canonical) or key in {
                normalize_identity(alias) for alias in aliases
            }:
                return canonical, aliases
        return None


def parse_alias_assignments(values: Iterable[str] | None) -> AuthorAliases:
    """Parse repeated ``Canonical=alias,alias`` CLI assignments."""
    parsed: dict[str, list[str]] = {}
    for raw_value in values or []:
        match = _ALIAS_ASSIGNMENT_RE.match(raw_value)
        if not match:
            raise ValueError("author aliases must use CANONICAL=ALIAS[,ALIAS...] format")
        canonical = match.group("canonical").strip()
        aliases = [
            item.strip()
            for item in re.split(r"[,|]", match.group("aliases"))
            if item.strip()
        ]
        if not canonical or not aliases:
            raise ValueError("author aliases must include a canonical name and at least one alias")
        parsed.setdefault(canonical, []).extend(aliases)
    return AuthorAliases.from_mapping(parsed)


def canonicalize_commit_authors(
    commits: Iterable[dict[str, object]],
    aliases: AuthorAliases,
) -> None:
    """Rewrite commit author names in place to their configured canonical name."""
    if not aliases.groups:
        return
    for commit in commits:
        canonical = aliases.canonical_for(
            name=str(commit.get("author_name") or ""),
            email=str(commit.get("author_email") or ""),
            login=str(commit.get("author_login") or ""),
        )
        if canonical:
            commit["author_name"] = canonical


def _dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
