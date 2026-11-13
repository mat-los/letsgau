"""``letsgau init``: set up a fresh instance for a new region.

Asks for (or takes as options) the region label, the municipalities, site name, public URL,
contact email and GitHub owner, then replaces ``instance/`` with fresh files
from ``examples/instance/`` and fills in the values. The result is validated
like any config. Generated data (``data/``, ``geo/``) lives on the ``data``
branch, which a fork does not copy, so a new instance starts without any data
of the original region. Content pages keep ``[TODO: ...]`` markers for text
only the operator can write (about, imprint); ``letsgau check`` lists them.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from letsgau.config import ConfigError, find_placeholders, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "instance"


class InitError(Exception):
    """Invalid answers or an aborted init."""


@dataclass
class Answers:
    label: str
    municipalities: str
    name: str
    base_url: str
    contact: str
    owner: str


QUESTIONS = (
    ("label", "Name of the region for page titles (e.g. Murnau)", str),
    (
        "municipalities",
        "Municipalities, comma-separated, each name and/or postcode "
        "(e.g. Murnau am Staffelsee 82418, Seehausen 82418)",
        str,
    ),
    ("name", "Site name", str),
    ("base_url", "Public URL (e.g. https://<owner>.github.io/letsgau)", str),
    ("contact", "Contact email", str),
    ("owner", "GitHub owner of your fork", str),
)
DEFAULTS: dict[str, str] = {}


def ask(values: dict[str, object], prompt: Callable[[str], str] = input) -> Answers:
    """Fill missing values interactively; validate all of them."""
    filled: dict[str, object] = {}
    for key, question, kind in QUESTIONS:
        value = values.get(key)
        while value in (None, ""):
            default = DEFAULTS.get(key, "")
            raw = prompt(f"{question}{f' [{default}]' if default else ''}: ").strip() or default
            try:
                value = kind(raw) if raw else None
            except ValueError:
                print(f"  not a valid {kind.__name__}: {raw!r}")
                value = None
        filled[key] = kind(value)  # type: ignore[operator]
    answers = Answers(**filled)  # type: ignore[arg-type]
    if not parse_municipalities(answers.municipalities):
        raise InitError("name at least one municipality (name and/or postcode)")
    if not re.match(r"^https?://\S+$", answers.base_url):
        raise InitError("the public URL must start with http:// or https://")
    if "@" not in answers.contact:
        raise InitError("the contact email needs an @")
    return answers


def parse_municipalities(text: str) -> list[tuple[str, str]]:
    """``"Schongau 86956, Altenstadt"`` -> ``[("Schongau", "86956"), ("Altenstadt", "")]``."""
    result = []
    for item in text.split(","):
        words = item.split()
        postcode = next((w for w in words if re.fullmatch(r"\d{4,5}", w)), "")
        name = " ".join(w for w in words if w != postcode)
        if name or postcode:
            result.append((name, postcode))
    return result


def fill_config(text: str, a: Answers) -> str:
    def q(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    replacements = {
        "{{SITE_NAME}}": q(a.name),
        "{{BASE_URL}}": q(a.base_url.rstrip("/")),
        "{{GITHUB_OWNER}}": q(a.owner),
        "{{CONTACT_EMAIL}}": q(a.contact),
        "{{REGION_LABEL}}": q(a.label),
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    items = "".join(
        f'    - {{ name: "{q(name)}", postcode: "{postcode}" }}\n'
        for name, postcode in parse_municipalities(a.municipalities)
    )
    return re.sub(
        r"^  municipalities:.*\{\{MUNICIPALITIES\}\}.*\n"
        r"    - \{ name: \"\{\{MUNICIPALITY\}\}\".*\n",
        lambda _m: (
            "  municipalities:                      # name and/or postcode each"
            " (optional separate: [districts shown as their own area])\n" + items
        ),
        text,
        count=1,
        flags=re.M,
    )


def run(
    values: dict[str, object] | None = None,
    *,
    target: Path = Path("instance"),
    examples: Path = EXAMPLES_DIR,
    yes: bool = False,
    prompt: Callable[[str], str] = input,
) -> Path:
    """Create ``target`` from the examples; returns the path of the new config."""
    answers = ask(values or {}, prompt)
    if target.exists() and not yes:
        reply = prompt(f"{target}/ exists and will be replaced. Continue? [y/N] ").strip()
        if reply.lower() not in ("y", "yes", "j", "ja"):
            raise InitError("aborted, nothing changed")
    staging = target.with_name(target.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(examples, staging)
    config_path = staging / "config.yaml"
    config_path.write_text(
        fill_config(config_path.read_text(encoding="utf-8"), answers), encoding="utf-8"
    )
    left = find_placeholders(config_path.read_text(encoding="utf-8"))
    if left:
        shutil.rmtree(staging)
        raise InitError(f"placeholders left in config.yaml: {left}")
    try:
        load_config(config_path)
    except ConfigError as exc:
        shutil.rmtree(staging)
        raise InitError("the generated config is invalid:\n" + "\n".join(exc.problems)) from exc
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    final = target / "config.yaml"
    print(f"init: wrote {target}/ for {answers.label} ({answers.municipalities})")
    print("Next: fill the [TODO] markers in the content pages (letsgau check lists them),")
    print("then follow the setup steps in README.md (secrets, Pages, first run).")
    return final
