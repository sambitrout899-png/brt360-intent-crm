"""Personal details masked before a prompt leaves the server, put back in the answer.

The AI gateway runs every message of a call to an external model through
mask(): email addresses, IBANs, payment card numbers (checked with Luhn)
and phone numbers become placeholders like [EMAIL_1]. The model works with
the placeholders - it only needs to know there is an email, not whose - and
unmask() puts the real values back wherever the answer repeats them, so an
entity the model extracted still comes back as the customer's real email.
Calls to the model on this server are not masked: nothing leaves it.
"""

from __future__ import annotations

import re
from typing import Any

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# 9-15 digits, written as a phone number is: a leading + or 0, or digits grouped by spaces, dashes or brackets
_PHONE = re.compile(r"(?<![\w+])(?:\+\d[\d\s().-]{7,18}\d|0\d{8,14}|\(?\d{2,4}\)?[\s.-]\d{2,4}[\s.-]\d{2,6}(?:[\s.-]\d{2,6})?)(?![\w@])")
_PLACEHOLDER = re.compile(r"\[(EMAIL|IBAN|CARD|PHONE)_\d+\]")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _luhn(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


class Redactor:
    """One call's masking: the same value always gets the same placeholder."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}  # placeholder -> real value
        self._seen: dict[str, str] = {}  # real value -> placeholder
        self._counts: dict[str, int] = {}

    def _swap(self, kind: str, value: str) -> str:
        if value not in self._seen:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            placeholder = f"[{kind}_{self._counts[kind]}]"
            self._seen[value] = placeholder
            self.values[placeholder] = value
        return self._seen[value]

    def mask_text(self, text: str) -> str:
        text = _EMAIL.sub(lambda m: self._swap("EMAIL", m.group()), text)
        text = _IBAN.sub(lambda m: self._swap("IBAN", m.group()), text)

        def card(m: re.Match) -> str:
            digits = _digits(m.group())
            return self._swap("CARD", m.group()) if 13 <= len(digits) <= 19 and _luhn(digits) else m.group()

        text = _CARD.sub(card, text)

        def phone(m: re.Match) -> str:
            return self._swap("PHONE", m.group()) if 9 <= len(_digits(m.group())) <= 15 else m.group()

        return _PHONE.sub(phone, text)

    def mask(self, body: dict[str, Any]) -> dict[str, Any]:
        """The chat-completions body with every message's text masked."""
        messages = []
        for message in body.get("messages") or []:
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                message = {**message, "content": self.mask_text(message["content"])}
            messages.append(message)
        return {**body, "messages": messages}

    def unmask_text(self, text: str) -> str:
        if not self.values:
            return text
        return _PLACEHOLDER.sub(lambda m: self.values.get(m.group(), m.group()), text)

    def unmask(self, answer: dict[str, Any]) -> dict[str, Any]:
        """The model's answer with the real values back in each choice's text."""
        if not self.values:
            return answer
        choices = []
        for choice in answer.get("choices") or []:
            message = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                choice = {**choice, "message": {**message, "content": self.unmask_text(message["content"])}}
            choices.append(choice)
        return {**answer, "choices": choices}

    @property
    def count(self) -> int:
        return len(self.values)
