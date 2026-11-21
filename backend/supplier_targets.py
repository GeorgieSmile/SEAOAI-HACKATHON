"""Bounded, source-grounded details that may be included in a supplier question."""

from datetime import date
import re
from typing import Annotated, Literal, Self
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, model_validator


_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class OrderTarget(_Target):
    kind: Literal["order_type"] = "order_type"
    value: Literal["ready_made_only", "custom_orders_acceptable"]


class DecorationTarget(_Target):
    kind: Literal["decoration_method"] = "decoration_method"
    method: Literal["screen_printing", "laser_engraving", "applied_graphics", "embroidery"]
    intent: Literal["require", "avoid"]


class SurfaceTarget(_Target):
    kind: Literal["surface_finish"] = "surface_finish"
    finish: Literal["matte", "glossy"]
    intent: Literal["require", "avoid"]


class CapacityTarget(_Target):
    kind: Literal["capacity"] = "capacity"
    amount: Annotated[float, Field(gt=0, le=1_000_000)]
    unit: Literal["ml", "L", "fl oz"]


class DeliveryTarget(_Target):
    kind: Literal["delivery_deadline"] = "delivery_deadline"
    day: Annotated[int, Field(ge=1, le=31)]
    month: Annotated[int, Field(ge=1, le=12)]
    year: Annotated[int, Field(ge=1, le=9999)] | None

    @model_validator(mode="after")
    def valid_date(self) -> Self:
        date(self.year if self.year is not None else 2000, self.month, self.day)
        return self

    def display_date(self) -> str:
        return f"{self.day} {_MONTH_NAMES[self.month]}" + (f" {self.year}" if self.year is not None else "")


SupplierTarget = OrderTarget | DecorationTarget | SurfaceTarget | CapacityTarget | DeliveryTarget

_STOCK = r"ready[- ]made(?: stock| options?)?"
_CUSTOM = r"custom (?:orders?|production)"
_ONLY_STOCK = re.compile(
    rf"(?:i only (?:want|accept|need) {_STOCK}|(?:keep )?{_STOCK} only|only {_STOCK}|{_STOCK} (?:is|are) required)"
)
_CUSTOM_ALLOWED = re.compile(rf"(?:(?:i|we) (?:can accept|would consider) (?:a )?{_CUSTOM}|{_CUSTOM} (?:is|are) acceptable)")
_METHODS = {
    "screen_printing": r"screen[- ]print(?:ed|ing)?",
    "laser_engraving": r"laser[- ]engrav(?:e[ds]?|ing)",
    "applied_graphics": r"applied graphics?|decals?",
    "embroidery": r"embroider(?:ed|y)",
}
_SURFACES = {"matte": r"matte (?:finish|texture)", "glossy": r"glossy (?:finish|texture)"}
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_MONTHS = {name[:length].lower(): index for index, name in enumerate(_MONTH_NAMES) if index for length in (3, len(name))}
_MONTH = r"(?P<month>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_YEAR = r"(?:,? (?P<year>\d{4}))?"
_DATE_FORMATS = (
    re.compile(rf"{_DAY} {_MONTH}{_YEAR}"),
    re.compile(rf"{_MONTH} {_DAY}{_YEAR}"),
    re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"),
)
_DELIVERY_PREFIX = re.compile(
    r"(?:(?:the |our )?(?:(?:delivery )?deadline (?:is|has moved to|moved to)|"
    r"(?:order|shipment|items?|goods) (?:must|needs? to) (?:arrive|(?:be )?delivered) by)|"
    r"(?:we|i) (?:need|require) (?:the )?(?:(?:order|items?|goods) (?:to arrive|delivered)|delivery) by|"
    r"(?:please )?(?:deliver|arrive|delivery) by|by) "
)
_CAPACITY = r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>ml|l|fl oz)"


def _direction(text: str, term: str) -> Literal["require", "avoid"] | None:
    # Whole statements prevent a mere mention or permissive wording from becoming a direction.
    subject = r"(?:(?:the|my|our|this) (?:logo|design|artwork|product|surface|item|decoration)|it)"
    if re.fullmatch(
        rf"(?:(?:i|we) (?:need|require) (?:a |an )?(?:{term})|"
        rf"(?:a |an )?(?:{term}) (?:is required|only)|"
        rf"{subject} must (?:be |have (?:a |an )?)(?:{term}))", text
    ):
        return "require"
    if re.fullmatch(
        rf"(?:(?:a |an )?(?:{term}) (?:is unacceptable|is not acceptable)|"
        rf"(?:i|we) (?:cannot accept|do not accept) (?:a |an )?(?:{term})|"
        rf"no (?:{term})|{subject} must not (?:be |have (?:a |an )?)(?:{term}))", text
    ):
        return "avoid"
    return None


def _delivery_target(text: str) -> DeliveryTarget | None:
    date_text = _DELIVERY_PREFIX.sub("", text, count=1)
    for pattern in _DATE_FORMATS:
        match = pattern.fullmatch(date_text)
        if match is None:
            continue
        month = _MONTHS.get(match["month"])
        if month is None:
            month = int(match["month"])
        try:
            return DeliveryTarget(day=int(match["day"]), month=month, year=int(match["year"]) if match["year"] else None)
        except ValueError:
            return None
    return None


def supplier_target_from_answer(answer_text: str, topic: str | None, buyer_facet: str | None) -> SupplierTarget | None:
    text = " ".join(normalize("NFKC", answer_text).casefold().split()).removesuffix(".")
    if topic == "customization" and buyer_facet in (None, "order_type"):
        if _ONLY_STOCK.fullmatch(text):
            return OrderTarget(value="ready_made_only")
        if _CUSTOM_ALLOWED.fullmatch(text):
            return OrderTarget(value="custom_orders_acceptable")
    if topic == "finish":
        if buyer_facet in (None, "decoration_method"):
            for method, term in _METHODS.items():
                if intent := _direction(text, term):
                    return DecorationTarget(method=method, intent=intent)
        if buyer_facet in (None, "surface_finish"):
            for finish, term in _SURFACES.items():
                if intent := _direction(text, term):
                    return SurfaceTarget(finish=finish, intent=intent)
    if topic == "capacity" and buyer_facet in (None, "capacity"):
        match = re.fullmatch(rf"(?:i (?:need|require) |exactly )?{_CAPACITY}(?: (?:is required|only))?", text)
        if match and 0 < (amount := float(match["amount"])) <= 1_000_000:
            return CapacityTarget(amount=amount, unit={"l": "L"}.get(match["unit"], match["unit"]))
    if topic == "deadline" and (
        buyer_facet == "delivery_deadline" or (buyer_facet is None and _DELIVERY_PREFIX.match(text))
    ):
        return _delivery_target(text)
    return None


def validate_supplier_target(
    target: SupplierTarget | None, exact_quote: str, topic: str | None, buyer_facet: str | None
) -> None:
    if target is not None and target != supplier_target_from_answer(exact_quote, topic, buyer_facet):
        raise ValueError("Supplier detail must be supported by the complete quoted buyer statement for this decision.")
