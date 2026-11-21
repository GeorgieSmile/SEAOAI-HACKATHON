"""Validate free-text buying-brief corrections against the buyer's actual wording."""

import re
from unicodedata import normalize

from .contracts import BriefProposal, ListingSnapshot
from .supplier_targets import DeliveryTarget, parse_arrival_date


_COLORS = {
    "blue": "蓝", "white": "白", "black": "黑", "pink": "粉", "yellow": "黄",
    "red": "红", "green": "绿", "purple": "紫", "gray": "灰", "orange": "橙",
}
_ORDER = (
    r"(?:(?P<custom>custom(?: orders?)? (?:is |are )?(?:fine|acceptable|allowed|okay))"
    r"|(?P<stock>ready[- ]made(?: stock)? only))"
)
_ORDER_TEXT = re.compile(
    rf"(?:actually, )?(?:for (?P<prefix_scope>[^,:]+)[,:] )?{_ORDER}"
    r"(?: for (?P<scope>.+?))?(?: if (?:it|they) (?:still )?arrives? by (?P<cutoff>.+))?"
)
_DEADLINE_TEXT = re.compile(
    r"(?:actually, )?(?:(?:change|move) (?:the )?(?:shared |delivery |arrival )?deadline to |"
    r"(?:the )?(?:shared |delivery |arrival )?deadline (?:is|has moved to|moved to|should be)|"
    r"(?:all(?: (?:selected )?options| three| two)?|(?P<subject>.+?)) must arrive by|"
    r"deliver by|arrive by|by)\s*(?P<date>.+?)(?: for (?P<scope>.+))?"
)
_OPTION_REFERENCE = re.compile(r"(?:options? |skus? )?([a-c])")
_COLLECTIVE_SCOPE = {"all", "all three", "all two", "both", "all options", "all selected options"}


def _named_options(label: str, snapshot: ListingSnapshot, selected: list[str]) -> list[str]:
    scope = label.removeprefix("the ")
    if scope in _COLLECTIVE_SCOPE:
        return selected
    references = [_OPTION_REFERENCE.fullmatch(part) for part in re.split(r",? and |,\s*", scope)]
    if references and all(references):
        letters = [match[1] for match in references if match]
        aliases = dict(zip("abc", selected))
        return [item for letter in letters if (item := aliases.get(letter))] if all(letter in aliases for letter in letters) else []
    matches = []
    colors = set(re.findall(r"[a-z]+", scope)) & _COLORS.keys()
    color_label = re.fullmatch(r"(?:[a-z]+[ -])+?(?:cup|bottle|option|sku)", scope)
    for option in snapshot.options:
        if option.option_id not in selected:
            continue
        names = [normalize("NFKC", name).casefold() for name in (option.name_original, option.name_en) if name]
        translated_words = set(re.findall(r"[a-z]+", " ".join(names)))
        if scope in names or (colors and color_label and all(
            color in translated_words or any(_COLORS[color] in name for name in names) for color in colors
        )):
            matches.append(option.option_id)
    return matches


def written_order_scope(text: str, snapshot: ListingSnapshot, selected: list[str]) -> list[str] | None:
    statement = " ".join(normalize("NFKC", text).casefold().split()).rstrip(".!。！")
    order = _ORDER_TEXT.fullmatch(statement)
    if order is None:
        return None
    label = order["prefix_scope"] or order["scope"]
    if label:
        label = re.split(r"\s+(?:if|unless|except)\s+", label, maxsplit=1)[0]
    return _named_options(label, snapshot, selected) if label else None


def correction_from_text(
    text: str, snapshot: ListingSnapshot, selected: list[str], option_id: str | None,
) -> tuple[BriefProposal, str | None]:
    """Bound the first slice to attributable order and arrival instructions."""
    if not text.strip():
        raise ValueError("Enter a correction for the buying brief.")
    if option_id is not None and option_id not in selected:
        raise ValueError("Choose an option from the current buying brief.")

    def unclear(question: str, scope: list[str]) -> tuple[BriefProposal, str]:
        return BriefProposal(kind="unclear", option_ids=scope or selected, date=None), question

    statement = " ".join(normalize("NFKC", text).casefold().split()).rstrip(".!。！")
    order = _ORDER_TEXT.fullmatch(statement)
    deadline = _DEADLINE_TEXT.fullmatch(statement)
    if order is None and deadline is None:
        return unclear(
            "What should change, and for which options? Give the order type or the date the order must arrive.",
            [option_id] if option_id else selected,
        )

    if order is not None:
        label = order["prefix_scope"] or order["scope"]
        if order["prefix_scope"] and order["scope"]:
            return unclear("Which option should this order instruction apply to?", selected)
    else:
        assert deadline is not None
        label = deadline["subject"] or deadline["scope"]
        if deadline["subject"] and deadline["scope"]:
            return unclear("Which option should this deadline apply to?", selected)

    scope = _named_options(label, snapshot, selected) if label else selected
    if option_id is not None:
        if label and (option_id not in scope or label in _COLLECTIVE_SCOPE):
            raise ValueError("The chosen option must match the option named in your correction.")
        scope = [option_id]
    if not scope or (label and len(scope) > 1 and label not in _COLLECTIVE_SCOPE and not all(
        _OPTION_REFERENCE.fullmatch(part) for part in re.split(r",? and |,\s*", label)
    )):
        return unclear("Which selected option did you mean? Choose the exact option.", scope)

    date: DeliveryTarget | None = None
    if order is not None:
        cutoff = order["cutoff"]
        if cutoff and order["stock"]:
            return unclear("What order type should apply if this option can arrive on time?", scope)
        if cutoff and cutoff != "then":
            date = parse_arrival_date(cutoff)
            if date is None:
                return unclear("What is the latest arrival date for the custom order?", scope)
        kind = "custom_by_deadline" if cutoff else "custom" if order["custom"] else "ready_made"
    else:
        assert deadline is not None
        date = parse_arrival_date(deadline["date"])
        if date is None:
            return unclear("What date must the selected options arrive by? Include the day and month.", scope)
        kind = "deadline"
    return BriefProposal(kind=kind, date=date, option_ids=[item for item in selected if item in scope]), None
