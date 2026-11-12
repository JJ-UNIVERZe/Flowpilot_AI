"""Offline keyword retrieval for fictional demo policy documents."""

from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)
DEFAULT_KB_PATH = Path(__file__).resolve().parent / "kb"
MAX_FILE_BYTES = 100 * 1024
MAX_CHUNK_CHARACTERS = 600
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
        "can", "could", "do", "does", "for", "from", "had", "has", "have",
        "how", "i", "in", "into", "is", "it", "item", "long", "longer", "me", "my",
        "of", "on", "or",
        "our", "please", "the", "their", "there", "they", "this", "to",
        "until", "was", "we", "what", "when", "where", "which", "who", "why",
        "will", "with", "you", "your",
    }
)


@dataclass(frozen=True)
class Passage:
    doc_id: str
    title: str
    text: str
    score: float


@dataclass(frozen=True)
class _Chunk:
    doc_id: str
    title: str
    text: str
    position: int
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class _Index:
    chunks: tuple[_Chunk, ...]
    document_frequencies: dict[str, int]
    average_length: float


_INDEX_CACHE: dict[Path, _Index] = {}


def _stem(token: str) -> str:
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
        if len(token) > 2 and token[-1] == token[-2]:
            token = token[:-1]
    elif len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
        if len(token) > 2 and token[-1] == token[-2]:
            token = token[:-1]
        if token.endswith("v"):
            token += "e"
    elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def _tokenize(text: str) -> tuple[str, ...]:
    return tuple(
        token
        for raw_token in TOKEN_PATTERN.findall(text.lower())
        if (token := _stem(raw_token)) not in STOPWORDS
    )


def _split_long_paragraph(paragraph: str, available: int) -> list[str]:
    words = paragraph.split()
    pieces: list[str] = []
    current = ""
    for word in words:
        if len(word) > available:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(
                word[start : start + available]
                for start in range(0, len(word), available)
            )
            continue
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > available:
            pieces.append(current)
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _document_chunks(path: Path) -> list[tuple[str, str]]:
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        LOGGER.warning("Skipping unreadable policy document %s: %s", path.name, error)
        return []

    title = next(
        (
            line.strip()[2:].strip()
            for line in contents.splitlines()
            if line.strip().startswith("# ") and not line.strip().startswith("##")
        ),
        path.stem.replace("_", " ").title(),
    )
    sections: list[tuple[str, list[str]]] = []
    current_heading = title
    current_paragraph: list[str] = []

    def finish_paragraph() -> None:
        if current_paragraph:
            paragraph = " ".join(current_paragraph).strip()
            if paragraph:
                if sections and sections[-1][0] == current_heading:
                    sections[-1][1].append(paragraph)
                else:
                    sections.append((current_heading, [paragraph]))
            current_paragraph.clear()

    for line in contents.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            finish_paragraph()
            heading = stripped.lstrip("#").strip()
            if heading:
                if not sections and current_heading == title and heading == title:
                    continue
                current_heading = heading
            continue
        if not stripped or stripped.startswith("FICTIONAL DEMO POLICY."):
            finish_paragraph()
            continue
        current_paragraph.append(stripped)
    finish_paragraph()

    chunks: list[tuple[str, str]] = []
    for heading, paragraphs in sections:
        prefix = heading[: MAX_CHUNK_CHARACTERS - 2]
        body = ""
        for paragraph in paragraphs:
            available = MAX_CHUNK_CHARACTERS - len(prefix) - 1
            parts = _split_long_paragraph(paragraph, available)
            for part in parts:
                combined = f"{body}\n{part}".strip() if body else part
                if len(prefix) + 1 + len(combined) > MAX_CHUNK_CHARACTERS and body:
                    chunks.append((title, f"{prefix}\n{body}"))
                    body = part
                elif len(prefix) + 1 + len(combined) > MAX_CHUNK_CHARACTERS:
                    chunks.append((title, f"{prefix}\n{part[:available]}"))
                else:
                    body = combined
        if body:
            chunks.append((title, f"{prefix}\n{body}"))
    return chunks


def _resolve_path(folder: str | os.PathLike[str] | None) -> Path:
    selected = folder if folder is not None else os.environ.get("KB_PATH", DEFAULT_KB_PATH)
    return Path(selected).expanduser().resolve()


def _build_index(folder: Path) -> _Index:
    if not folder.is_dir():
        LOGGER.warning("Policy knowledge base folder does not exist: %s", folder)
        return _Index((), {}, 0.0)

    chunks: list[_Chunk] = []
    for path in sorted(folder.glob("*.md"), key=lambda item: item.name.casefold()):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                LOGGER.warning("Skipping oversized policy document %s", path.name)
                continue
        except OSError as error:
            LOGGER.warning("Skipping unavailable policy document %s: %s", path.name, error)
            continue

        document_position = 0
        for title, text in _document_chunks(path):
            tokens = _tokenize(text)
            if tokens:
                chunks.append(
                    _Chunk(
                        doc_id=path.stem,
                        title=title,
                        text=text,
                        position=document_position,
                        tokens=tokens,
                    )
                )
                document_position += 1

    frequencies: Counter[str] = Counter()
    for chunk in chunks:
        frequencies.update(set(chunk.tokens))
    average_length = (
        sum(len(chunk.tokens) for chunk in chunks) / len(chunks) if chunks else 0.0
    )
    return _Index(tuple(chunks), dict(frequencies), average_length)


def _get_index(folder: str | os.PathLike[str] | None) -> _Index:
    path = _resolve_path(folder)
    if path not in _INDEX_CACHE:
        _INDEX_CACHE[path] = _build_index(path)
    return _INDEX_CACHE[path]


def reset_index_cache() -> None:
    """Clear cached indexes, primarily for tests and document updates."""
    _INDEX_CACHE.clear()


def search(
    query: str,
    k: int = 3,
    min_score: float = 0.1,
    folder: str | os.PathLike[str] | None = None,
) -> list[Passage]:
    """Return BM25-ranked policy passages without interpreting their contents."""
    if not isinstance(query, str) or not query.strip() or k <= 0:
        return []

    query_tokens = _tokenize(query[:500])
    if not query_tokens:
        return []

    index = _get_index(folder)
    if not index.chunks:
        return []

    query_frequencies = Counter(query_tokens)
    count = len(index.chunks)
    scored: list[tuple[float, _Chunk]] = []
    for chunk in index.chunks:
        term_frequencies = Counter(chunk.tokens)
        title_frequencies = Counter(_tokenize(chunk.title))
        length_ratio = (
            len(chunk.tokens) / index.average_length if index.average_length else 0.0
        )
        score = 0.0
        for token, query_frequency in query_frequencies.items():
            frequency = term_frequencies[token]
            if not frequency:
                continue
            document_frequency = index.document_frequencies[token]
            inverse_frequency = math.log(
                1 + (count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length_ratio)
            score += (
                inverse_frequency
                * (frequency * (1.5 + 1) / denominator)
                * query_frequency
            )
            if title_frequencies[token]:
                score += (
                    inverse_frequency
                    * 2.0
                    * min(title_frequencies[token], query_frequency)
                    / len(title_frequencies)
                )
        if title_frequencies and all(
            query_frequencies[token] for token in title_frequencies
        ):
            score += sum(
                math.log(
                    1
                    + (
                        count
                        - index.document_frequencies.get(token, 0)
                        + 0.5
                    )
                    / (index.document_frequencies.get(token, 0) + 0.5)
                )
                for token in title_frequencies
            )
        if score >= min_score:
            scored.append((score, chunk))

    scored.sort(key=lambda result: (-result[0], result[1].doc_id, result[1].position))
    return [
        Passage(chunk.doc_id, chunk.title, chunk.text, score)
        for score, chunk in scored[:k]
    ]
