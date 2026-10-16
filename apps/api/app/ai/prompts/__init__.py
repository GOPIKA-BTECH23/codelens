"""Versioned prompts. Each prompt is a file `<name>_<version>.md` in this directory.

Why files and versions: a prompt is code that changes model behaviour, so it is reviewed in
pull requests like code, and every review run records which version produced it
(`review_runs.prompt_version`). To change a prompt, add `security_v2.md` instead of editing
v1, then compare the two on the eval set (M13) before switching. Editing a version that has
already been used would make old runs' `prompt_version` lie about what produced them.
"""

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str

    @property
    def id(self) -> str:
        """What is stored in review_runs.prompt_version, e.g. `security_v1`."""
        return f"{self.name}_{self.version}"

    @property
    def sha256(self) -> str:
        """Fingerprint of the exact text (handy in logs to spot an edited version)."""
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]


@lru_cache
def load_prompt(name: str, version: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}_{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt {name}_{version}.md not found in {PROMPTS_DIR}")
    return Prompt(name=name, version=version, text=path.read_text(encoding="utf-8").strip())
