"""Small helpers shared by the pipeline stages: paths, URLs, prompts, JSON files, logging."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

DATA_DIR = Path("data")
"""Generated data (a checkout of the ``data`` branch in the workflow)."""
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

UMLAUTS = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"}
)
"""German umlauts as ASCII, for ids and comparisons."""


def domain_of(url: str) -> str:
    """Lower-case host without ``www.``."""
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def is_blocked(url: str, blocked: Iterable[str]) -> bool:
    """True if the URL's host is one of the ``blocked`` domains or a subdomain of one."""
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in blocked)


def prompt_env(prompts_dir: Path = PROMPTS_DIR) -> Environment:
    """Jinja2 environment for the prompt templates (strict: a missing variable is an error)."""
    return Environment(
        loader=FileSystemLoader(prompts_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def load_json[T: BaseModel](
    path: Path, model: type[T], error: type[Exception] = ValueError
) -> T | None:
    """The file parsed as ``model``; None if it does not exist; ``error`` if it is invalid."""
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise error(f"{path} is invalid: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    """Write ``data`` as indented UTF-8 JSON (a model is dumped first)."""
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def logger(prefix: str) -> Callable[[str], None]:
    """A function that prints ``<prefix>: <message>`` to stderr."""

    def log(message: str) -> None:
        print(f"{prefix}: {message}", file=sys.stderr, flush=True)

    return log
