"""Small, position-preserving lexer for ISO-10303-21 text."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


class StepLexError(ValueError):
    pass


_NUMBER = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[Ee][+-]?\d+)?")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_REFERENCE = re.compile(r"#\d+")


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    length = len(source)
    punctuation = {"(": "LPAREN", ")": "RPAREN", ",": "COMMA", ";": "SEMICOLON", "=": "EQUALS"}
    while i < length:
        char = source[i]
        if char.isspace():
            i += 1
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise StepLexError(f"unterminated comment at offset {i}")
            i = end + 2
            continue
        if char in punctuation:
            tokens.append(Token(punctuation[char], char, i, i + 1))
            i += 1
            continue
        # These exchange-structure keywords contain hyphens, unlike ordinary
        # STEP identifiers.  Keep them as one token so the parser can accept
        # the canonical envelope without special handling in the value grammar.
        for keyword in ("END-ISO-10303-21", "ISO-10303-21"):
            if source.startswith(keyword, i):
                tokens.append(Token("IDENTIFIER", keyword, i, i + len(keyword)))
                i += len(keyword)
                break
        else:
            keyword = None
        if keyword is not None:
            continue
        if char == "#":
            # Positional match against `source`, never `re.match(..., source[i:])`:
            # that slice copies the whole tail of the file, once per reference, so a
            # file with many `#nnn` refs lexes in O(n^2). It cost 51s on a 143-record
            # STEP file. Same trick as _NUMBER/_IDENTIFIER just below.
            match = _REFERENCE.match(source, i)
            if not match:
                raise StepLexError(f"invalid entity reference at offset {i}")
            value = match.group(0)
            tokens.append(Token("REFERENCE", value, i, match.end()))
            i = match.end()
            continue
        if char == "'":
            start = i
            i += 1
            while i < length:
                if source[i] != "'":
                    i += 1
                    continue
                if i + 1 < length and source[i + 1] == "'":
                    i += 2
                    continue
                i += 1
                break
            else:
                raise StepLexError(f"unterminated string at offset {start}")
            tokens.append(Token("STRING", source[start:i], start, i))
            continue
        if char == "." and i + 1 < length and source[i + 1].isdigit():
            number = _NUMBER.match(source, i)
            if number is not None:
                tokens.append(Token("NUMBER", number.group(0), i, number.end()))
                i = number.end()
                continue
        if char == ".":
            end = source.find(".", i + 1)
            if end < 0:
                raise StepLexError(f"unterminated enumeration at offset {i}")
            value = source[i : end + 1]
            if len(value) == 2:
                raise StepLexError(f"empty enumeration at offset {i}")
            tokens.append(Token("ENUM", value, i, end + 1))
            i = end + 1
            continue
        if char in "$*":
            tokens.append(Token("NULL" if char == "$" else "OMITTED", char, i, i + 1))
            i += 1
            continue
        number = _NUMBER.match(source, i)
        if number:
            value = number.group(0)
            tokens.append(Token("NUMBER", value, i, number.end()))
            i = number.end()
            continue
        identifier = _IDENTIFIER.match(source, i)
        if identifier:
            value = identifier.group(0)
            tokens.append(Token("IDENTIFIER", value, i, identifier.end()))
            i = identifier.end()
            continue
        raise StepLexError(f"unexpected character {char!r} at offset {i}")
    tokens.append(Token("EOF", "", length, length))
    return tokens
