"""Terminal prompts. Each prompt has a key, so an answers file can supply it:
that is what makes `valheim new --answers file.json` scriptable and testable."""

from __future__ import annotations

import getpass
from typing import Any, Callable, Sequence

BOLD, GOLD, RED, GREEN, OFF = "\033[1m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"


class UI:
    show_info = True

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers

    @property
    def scripted(self) -> bool:
        return self.answers is not None

    def heading(self, text: str) -> None:
        print(f"\n{BOLD}{GOLD}{text}{OFF}")

    def say(self, text: str = "") -> None:
        print(text)

    def warn(self, text: str) -> None:
        print(f"{RED}{text}{OFF}")

    def ok(self, text: str) -> None:
        print(f"{GREEN}{text}{OFF}")

    def ask(self, key: str, prompt: str, default: str = "",
            check: Callable[[str], Any] | None = None) -> str:
        if self.scripted:
            value = str(self.answers.get(key, default))
            if not value and not check:
                raise SystemExit(f"The answers file has no value for {key!r}.")
            if check:
                try:
                    check(value)
                except ValueError as exc:
                    raise SystemExit(f"{key}: {exc}") from exc
            return value
        suffix = f" [{default}]" if default else ""
        while True:
            value = input(f"{prompt}{suffix}: ").strip() or default
            try:
                if check:
                    check(value)
                if value:
                    return value
                self.warn("  A value is required.")
            except ValueError as exc:
                self.warn(f"  {exc}")

    def secret(self, key: str, prompt: str) -> str:
        if self.scripted:
            if key not in self.answers:
                raise SystemExit(f"The answers file has no value for {key!r}.")
            return str(self.answers[key])
        return getpass.getpass(f"{prompt}: ").strip()

    def confirm(self, key: str, prompt: str, default: bool = False) -> bool:
        if self.scripted:
            return bool(self.answers.get(key, default))
        hint = "Y/n" if default else "y/N"
        value = input(f"{prompt} [{hint}]: ").strip().lower()
        return default if not value else value in ("y", "yes")

    def choose(self, key: str, prompt: str, options: Sequence[tuple[str, str]], default: int = 0) -> str:
        """options are (value, label). Returns the value."""
        if self.scripted:
            value = self.answers.get(key, options[default][0])
            if value not in [o[0] for o in options]:
                raise SystemExit(f"{key}: {value!r} is not one of {[o[0] for o in options]}")
            return value
        print(f"\n{prompt}")
        for i, (_, label) in enumerate(options, 1):
            print(f"  {i}) {label}")
        while True:
            raw = input(f"Choose 1-{len(options)} [{default + 1}]: ").strip()
            if not raw:
                return options[default][0]
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1][0]
            self.warn("  Enter one of the numbers above.")
