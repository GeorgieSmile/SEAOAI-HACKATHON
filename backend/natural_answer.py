"""Interpret ordinary buyer answers without widening the issued SKU scope."""

from dataclasses import dataclass
import re
from typing import Literal
from unicodedata import normalize

from .brief import _COLLECTIVE_SCOPE, _COLORS, _OPTION_REFERENCE, _named_options, correction_from_text
from .contracts import (
    BUYER_FACET_TOPICS, DeliveryCondition, IssuedQuestion, ListingSnapshot, RequirementEffect, ReviewTopic,
)
from .supplier_targets import OrderTarget, SupplierTarget, parse_arrival_date, supplier_target_from_answer


@dataclass(frozen=True)
class NaturalAnswerDecision:
    status: Literal["decided", "clarify", "unrelated"]
    effect: RequirementEffect | None = None
    option_ids: list[str] | None = None
    supplier_target: SupplierTarget | None = None
    delivery_condition: DeliveryCondition | None = None
    clarification: str | None = None
    may_use_model: bool = False


_UNKNOWN = re.compile(
    r"(?:unknown|unsure|skip(?: this)?|not (?:sure|decided)(?: yet)?|"
    r"i(?: am|'m) not sure(?: yet)?|i (?:don't|do not) know(?: yet)?|(?:i )?haven't decided(?: yet)?)"
)
_KEEP = re.compile(
    r"(?:keep(?: it| that| the current (?:requirement|deadline|date|rule))?|"
    r"keep it as is|no change|same as before|still required|still applies|that still works|"
    r"i haven't changed my mind|nothing has changed)"
)
_WAIVE = re.compile(
    r"(?:no (?:longer need (?:it|that|this)|(?:specific |fixed |hard )?(?:deadline|delivery date|"
    r"requirement|preference|finish|capacity))|"
    r"(?:any (?:finish|decoration|method|texture|date|capacity)|either(?: one)?|"
    r"(?:both )?options) (?:is |are )?(?:fine|fine with me|acceptable|okay)|"
    r"it (?:isn't|is not) required(?: anymore)?|it (?:doesn't|does not) matter|"
    r"i (?:don't|do not) have (?:a|any) (?:preference|requirement))"
)
_WAIVER_TOPICS = {
    "deadline": re.compile(r"\b(?:deadline|delivery date|date)\b"),
    "finish": re.compile(r"\b(?:finish|decoration|method|texture)\b"),
    "capacity": re.compile(r"\bcapacity\b"),
}
_READY = re.compile(
    r"(?:(?:no|(?:i|we) (?:can't|cannot|don't|do not) accept) custom(?: orders?| production)?|"
    r"custom(?: orders?| production)? (?:isn't|aren't|is not|are not) (?:fine|acceptable|allowed|okay|ok)|"
    r"(?:in-stock|in stock|off-the-shelf|off the shelf|stock) only|"
    r"only (?:in-stock|in stock|off-the-shelf|off the shelf)(?: items| stock)?)"
)
_CUSTOM = re.compile(
    r"(?:custom(?: orders?| production)? (?:works?|(?:is |are )?(?:fine|acceptable|allowed|okay|ok))|"
    r"(?:i am|i'm|we are|we're) (?:open to|fine with|okay with|ok with) custom(?: orders?| production)?|"
    r"(?:we|i) can (?:go custom|(?:do|accept) custom(?: orders?| production)?))"
)
_DATE_PREFERENCE = re.compile(
    r"(?:(?:i'd|we'd) prefer |(?:i|we) can do |my latest is )?(?P<date>.+?)"
    r"(?: is (?:fine|acceptable|okay|ok|the latest date)| works(?: for me| for us)?| at the latest)?"
)
_ARRIVAL = re.compile(r"(?:(?:it|they) )?(?:must|needs? to|has to|have to) (?:arrive|be delivered) by (?P<date>.+)")
_CONDITIONAL_ORDER = re.compile(
    r"(?P<order>.+?) (?:only if|if|as long as|provided that) (?:it |they )?"
    r"(?:can )?(?:still )?(?:arrives?|be delivered) by (?P<date>.+)"
)
_PREFIX_SCOPE = re.compile(r"for (?P<label>[^,:]+)[,:]\s*(?P<statement>.+)")
_SUBJECT_ARRIVAL = re.compile(r"(?P<label>.+?) (?P<arrival>must (?:arrive|be delivered) by .+)")
_SCOPE_TAIL = re.compile(r"\s+(?:only if|if|as long as|provided that|unless)\s+")
_EXPLICIT_ALIAS = re.compile(r"\b(?:options?|skus?|variants?) [a-z]\b")
_COLOR_NOUN = r"(?:cups?|bottles?|options?|skus?|variants?|one|ones)"
_COLOR_WORDS = "|".join(_COLORS)
_COLOR_LABEL = re.compile(rf"(?P<colors>(?:{_COLOR_WORDS})(?:[ -](?:{_COLOR_WORDS}))*)(?:[ -]{_COLOR_NOUN})?")
_COLOR_MENTION = re.compile(rf"\b(?:the )?(?:{_COLOR_WORDS})[ -]{_COLOR_NOUN}\b")
_UNSAFE_SCOPE = re.compile(r"\b(?:except(?: for)?|excluding|other than|not for)\b")
_INDEFINITE_SCOPE = re.compile(
    r"\b(?:(?:one|some|two) of (?:them|these|those|the (?:cups|bottles|options|skus))|"
    r"(?:that|this|the other) (?:one|cup|bottle|option|sku))\b"
)
_TENTATIVE = re.compile(r"\b(?:maybe|might|if|unless|depends|probably|possibly|or)\b")
_CONFIRM_PROMPT = re.compile(
    r"(?:(?:do you|would you) still (?:want|need|require|prefer)|"
    r"(?:is|does) (?:this|that|it|the current (?:requirement|deadline)) still)\b"
)
_GENERAL_ANSWER = re.compile(r"(?:(?:i|we) (?:need|require|want) |must |for )\S.+")
_CUSTOM_PROMPT = re.compile(
    r"(?:(?:would|can|could|will|do) you (?:consider|accept|allow) (?:a )?custom(?: orders?| production)?"
    r"|(?:are|is) custom(?: orders?| production)? (?:acceptable|okay|allowed))(?: for .+)?"
)
_STOCK_PROMPT = re.compile(
    r"(?:do|would) you (?:need|require|want|accept) (?:only ready[- ]made(?: stock)?"
    r"|ready[- ]made(?: stock)? only)(?: for .+)?"
)
_GENERIC_YES_NO = re.compile(
    r"(?:(?:do|would) you (?:need|require|want) .+|(?:is|are) .+ (?:required|necessary))"
)
_NEGATIVE_OR_CONDITIONAL_QUESTION = re.compile(
    r"\b(?:no|not|skip|avoid|without|if|unless|or|except)\b|n['’]t\b"
)
_MODEL_QUESTION = re.compile(r"(?:what|which|when|who|how|can|could|should|would|do|does|is|are)\b[^?<>\r\n]*\?", re.I)
_CUSTOM_WORDS = re.compile(r"\b(?:bespoke|custom(?:ized|ised)?|made[- ]to[- ]order|made to our specifications)\b")
_STOCK_WORDS = re.compile(r"\b(?:ready[- ]made|ready[- ]to[- ]ship|premade|pre[- ]made|prebuilt|"
                          r"in[- ]stock|existing stock|off[- ]the[- ]shelf)\b")
_ACCEPTANCE = re.compile(r"\b(?:works?|fine|acceptable|okay|ok|happy with|can live with|open to|can go with|"
                         r"suits? (?:us|me)|will (?:work|do))\b")
_EXCLUSIVITY = re.compile(r"\b(?:only|exclusively|stick (?:with|to)|must|have to|has to|has got to)\b")
_MODEL_ORDER_AMBIGUITY = re.compile(
    r"[;:!?]|\.\s|\b(?:not|no|without|never|either|neither|except|however|but|"
    r"if|unless|depending|depends|provided that|as long as|can't|cannot|isn't|aren't|don't|wouldn't)\b"
)


def _normalized(text: str) -> str:
    return " ".join(normalize("NFKC", text).casefold().replace("’", "'").split()).rstrip(".!?。！？")


def _topic(question: IssuedQuestion) -> ReviewTopic | None:
    return question.topic or BUYER_FACET_TOPICS.get(question.buyer_facet)


def _option_labels(label: str, snapshot: ListingSnapshot, selected: list[str]) -> tuple[bool, list[str]]:
    plain = label.removeprefix("the ")
    if plain in _COLLECTIVE_SCOPE or plain in {"all 3", "all 2"}:
        count = {"both": 2, "all two": 2, "all 2": 2, "all three": 3, "all 3": 3}.get(plain)
        return True, selected if count is None or len(selected) == count else []
    alias_label = re.sub(r"\bvariants? ", "option ", plain)
    parts = re.split(r",? and |,\s*", alias_label)
    aliases = all(_OPTION_REFERENCE.fullmatch(part) or re.fullmatch(r"(?:options?|skus?) [a-z]", part)
                  for part in parts)
    if aliases:
        matches = _named_options(alias_label, snapshot, selected)
        return True, matches if len(matches) == len(set(matches)) else []
    exact = [
        option.option_id for option in snapshot.options if option.option_id in selected and any(
            name and _normalized(name) in {label, plain} for name in (option.name_original, option.name_en)
        )
    ]
    if exact:
        return True, exact if len(exact) == 1 else []
    if color := _COLOR_LABEL.fullmatch(plain):
        matches = _named_options(f"{color['colors']} option", snapshot, selected)
        return True, matches if len(matches) == 1 else []
    looks_named = bool(_EXPLICIT_ALIAS.search(label) or re.search(rf"\b{_COLOR_NOUN}$", label))
    return looks_named, []


def _scoped_statement(
    question: IssuedQuestion, text: str, snapshot: ListingSnapshot, selected: list[str],
) -> tuple[str, list[str], str | None]:
    statement = _normalized(text).removeprefix("actually, ").removeprefix("please ")
    current = question.option_ids
    if _UNSAFE_SCOPE.search(statement):
        return statement, current, "Which exact options does this answer apply to? Name each option you want to change."
    scopes = []
    if match := _PREFIX_SCOPE.fullmatch(statement):
        _, scope = _option_labels(match["label"], snapshot, selected)
        scopes.append(scope)
        statement = match["statement"]
    if match := _SUBJECT_ARRIVAL.fullmatch(statement):
        named, scope = _option_labels(match["label"], snapshot, selected)
        if named:
            scopes.append(scope)
            statement = match["arrival"]
    body, separator, label_and_condition = statement.rpartition(" for ")
    if separator:
        condition = _SCOPE_TAIL.search(label_and_condition)
        label = label_and_condition[:condition.start()] if condition else label_and_condition
        named, scope = _option_labels(label, snapshot, selected)
        if named:
            scopes.append(scope)
            statement = body + (label_and_condition[condition.start():] if condition else "")
    unresolved_scope = bool(
        _EXPLICIT_ALIAS.search(statement) or _COLOR_MENTION.search(statement)
        or re.search(r"\b(?:options?|skus?)\s+[a-z]\s*(?:/|&)\s*[a-z]\b", statement)
    )
    if len(scopes) > 1 or (scopes and not scopes[0]) or unresolved_scope:
        return statement, current, "Which exact option or options did you mean? Use the option letters shown above."
    if scopes and not set(scopes[0]).issubset(current):
        return statement, current, "Answer for the options shown in this question. Change any other option in the buying brief."
    scope = [option_id for option_id in current if not scopes or option_id in scopes[0]]
    return statement, scope, None


def answer_option_scope(
    question: IssuedQuestion, text: str, snapshot: ListingSnapshot, selected_option_ids: list[str],
) -> tuple[list[str], str | None]:
    """Give the scope that can be grounded in the answer and the issued question."""
    _, scope, clarification = _scoped_statement(question, text, snapshot, selected_option_ids)
    return scope, clarification


def _order_or_arrival(
    question: IssuedQuestion, statement: str, snapshot: ListingSnapshot, selected: list[str],
) -> tuple[SupplierTarget | None, DeliveryCondition | None]:
    topic = _topic(question)
    existing = supplier_target_from_answer(statement, topic, question.buyer_facet)
    if existing is not None:
        return existing, None
    if topic == "deadline":
        match = _ARRIVAL.fullmatch(statement) or _DATE_PREFERENCE.fullmatch(statement)
        date = parse_arrival_date(match["date"] if match else statement)
        if date:
            return date, None
    if topic == "customization" and question.buyer_facet in {None, "order_type"}:
        if _READY.fullmatch(statement):
            return OrderTarget(value="ready_made_only"), None
        if _CUSTOM.fullmatch(statement):
            return OrderTarget(value="custom_orders_acceptable"), None
        if conditional := _CONDITIONAL_ORDER.fullmatch(statement):
            order = conditional["order"]
            grounded = supplier_target_from_answer(order, topic, question.buyer_facet)
            if _CUSTOM.fullmatch(order) or grounded == OrderTarget(value="custom_orders_acceptable"):
                cutoff = conditional["date"]
                parsed_date = parse_arrival_date(cutoff) if cutoff != "then" else None
                if parsed_date is not None or cutoff == "then":
                    return None, DeliveryCondition(follows_deadline=cutoff == "then", latest_arrival=parsed_date)
    if topic in {"customization", "deadline"}:
        proposal, clarification = correction_from_text(statement, snapshot, selected, None)
        if clarification is not None:
            return None, None
        if topic == "deadline" and proposal.kind == "deadline":
            return proposal.date, None
        if topic == "customization" and question.buyer_facet in {None, "order_type"}:
            if proposal.kind == "ready_made":
                return OrderTarget(value="ready_made_only"), None
            if proposal.kind == "custom":
                return OrderTarget(value="custom_orders_acceptable"), None
            if proposal.kind == "custom_by_deadline":
                return None, DeliveryCondition(follows_deadline=proposal.date is None, latest_arrival=proposal.date)
    return None, None


def _clarify(question: IssuedQuestion, statement: str) -> NaturalAnswerDecision:
    topic = _topic(question)
    if topic == "customization":
        prompt = (
            "What is the latest date the custom order may arrive? Give the day and month, or refer to the existing delivery date."
            if re.search(r"\b(?:custom|deadline|arrive)\b", statement) and _TENTATIVE.search(statement)
            else "Should these options be ready-made only, or are custom orders acceptable?"
        )
    elif topic == "deadline":
        prompt = "What is the latest date these options may arrive? Include the day and month, or say there is no fixed deadline."
    else:
        prompt = "What requirement should apply to the options shown? Say what is required, or what can be flexible."
    return NaturalAnswerDecision("clarify", clarification=prompt)


def _effect(
    question: IssuedQuestion, scope: list[str], action: Literal["change", "keep", "waive"],
    target: SupplierTarget | None = None, condition: DeliveryCondition | None = None,
    *, prior_target: SupplierTarget | None = None, prior_condition: DeliveryCondition | None = None,
) -> NaturalAnswerDecision:
    targets = question.requirement_ids or []
    if len(targets) > 1:
        prompt = (
            "This question covers several requirements. Choose one of the specific answers shown, or leave this answer open."
            if question.choices
            else "This question covers several requirements. Leave this answer open to keep them, then review the buying brief."
        )
        return NaturalAnswerDecision("clarify", clarification=prompt)
    target_id = targets[0] if targets else None
    if action == "keep" and target_id is None:
        return _clarify(question, "")
    if action == "change" and target_id is not None and (
        target is not None and target == prior_target or condition is not None and condition == prior_condition
    ):
        return NaturalAnswerDecision("decided", RequirementEffect(action="keep", requirement_id=target_id), scope)
    if action == "change":
        effect = RequirementEffect(action="replace" if target_id else "add", requirement_id=target_id)
    else:
        effect = RequirementEffect(action=action, requirement_id=target_id)
    return NaturalAnswerDecision("decided", effect, scope, target, condition)


def interpret_natural_answer(
    question: IssuedQuestion, text: str, snapshot: ListingSnapshot, selected_option_ids: list[str],
) -> NaturalAnswerDecision:
    """Resolve clear answers and preserve deliberate unknowns without a model call."""
    normalized = _normalized(text)
    if not normalized or text in question.choices or _UNKNOWN.fullmatch(normalized):
        return NaturalAnswerDecision("unrelated")
    if normalize("NFKC", text).rstrip().endswith("?"):
        return _clarify(question, normalized)
    statement, scope, clarification = _scoped_statement(question, text, snapshot, selected_option_ids)
    if len(selected_option_ids) > 1 and _INDEFINITE_SCOPE.search(normalized):
        prompt = (
            "Which option could be custom-made? Name the option and say whether a custom order is acceptable."
            if _topic(question) == "customization"
            else "Which options does this answer apply to? Name the options and the requirement you want to set."
        )
        return NaturalAnswerDecision("clarify", clarification=prompt)
    if clarification:
        return NaturalAnswerDecision("clarify", clarification=clarification)
    statement = statement.removeprefix("yes, ").removeprefix("no, ")
    if _UNKNOWN.fullmatch(statement):
        return NaturalAnswerDecision("unrelated")
    if _KEEP.fullmatch(statement):
        return _effect(question, scope, "keep")
    topic = _topic(question)
    if _WAIVE.fullmatch(statement):
        waived_topic = next((item for item, terms in _WAIVER_TOPICS.items() if terms.search(statement)), None)
        if waived_topic is not None and waived_topic != topic:
            return _clarify(question, statement)
        return _effect(question, scope, "waive")

    prior_target, prior_condition = None, None
    if question.requirement_targets and len(question.requirement_targets) == 1:
        prior, _, _ = _scoped_statement(question, question.requirement_targets[0].statement, snapshot, selected_option_ids)
        prior_target, prior_condition = _order_or_arrival(question, prior, snapshot, selected_option_ids)
    if statement in {"yes", "no"}:
        prompt, prompt_scope, unclear_scope = _scoped_statement(question, question.prompt, snapshot, selected_option_ids)
        if unclear_scope or prompt_scope != question.option_ids:
            return _clarify(question, statement)
        if question.requirement_ids and _CONFIRM_PROMPT.match(prompt) and " or " not in prompt:
            return _effect(question, scope, "keep" if statement == "yes" else "waive")
        if topic == "customization" and " or " not in prompt:
            if _CUSTOM_PROMPT.fullmatch(prompt):
                order = OrderTarget(value="custom_orders_acceptable" if statement == "yes" else "ready_made_only")
                return _effect(question, scope, "change", order, prior_target=prior_target)
            if _STOCK_PROMPT.fullmatch(prompt):
                if statement == "no":
                    return _effect(question, scope, "waive")
                return _effect(question, scope, "change", OrderTarget(value="ready_made_only"), prior_target=prior_target)
        if (
            topic not in {"customization", "deadline", "capacity", "finish"}
            and _GENERIC_YES_NO.fullmatch(prompt)
            and not _NEGATIVE_OR_CONDITIONAL_QUESTION.search(prompt)
        ):
            return _effect(question, scope, "change" if statement == "yes" else "waive")
        return _clarify(question, statement)
    target, condition = _order_or_arrival(question, statement, snapshot, selected_option_ids)
    if target is not None or condition is not None:
        return _effect(question, scope, "change", target, condition,
                       prior_target=prior_target, prior_condition=prior_condition)
    if topic in {"customization", "deadline", "capacity", "finish"} and _TENTATIVE.search(statement):
        return _clarify(question, statement)
    if topic not in {"customization", "deadline", "capacity", "finish"} and _GENERAL_ANSWER.fullmatch(statement):
        return _effect(question, scope, "change")
    return NaturalAnswerDecision("unrelated", may_use_model=True)


def _supported_model_order(question: IssuedQuestion, statement: str, target: OrderTarget) -> bool:
    if _topic(question) != "customization" or question.buyer_facet not in {None, "order_type"}:
        return False
    if _MODEL_ORDER_AMBIGUITY.search(statement) or re.search(
        r"\b(?:price|pricing|cost|lead time|sample|availability|available)\b", statement,
    ):
        return False
    custom = _CUSTOM_WORDS.search(statement)
    stock = _STOCK_WORDS.search(statement)
    if target.value == "custom_orders_acceptable":
        return bool(custom and not stock and _ACCEPTANCE.search(statement))
    return bool(stock and not custom and _EXCLUSIVITY.search(statement))


def validate_natural_proposal(
    question: IssuedQuestion, text: str, snapshot: ListingSnapshot, selected_option_ids: list[str],
    proposal: NaturalAnswerDecision,
) -> NaturalAnswerDecision:
    """Validate a model's intent suggestion against issued IDs and written option scope."""
    local = interpret_natural_answer(question, text, snapshot, selected_option_ids)
    if not local.may_use_model:
        if proposal != local:
            raise ValueError("The suggested action does not match the clear or ambiguous buyer answer.")
        return local
    statement, scope, _ = _scoped_statement(question, text, snapshot, selected_option_ids)
    target = proposal.supplier_target
    if proposal.delivery_condition is not None or target is not None and not (
        isinstance(target, OrderTarget) and _supported_model_order(question, statement, target)
    ):
        raise ValueError("The answer does not support the suggested supplier terms.")
    if proposal.status == "clarify":
        if proposal.effect is not None or proposal.option_ids is not None or target is not None:
            raise ValueError("A clarification cannot change a requirement.")
        prompt = proposal.clarification
        if (
            prompt is None or len(prompt) > 200 or not _MODEL_QUESTION.fullmatch(prompt)
            or "://" in prompt or re.search(r"[.!]\s", prompt)
        ):
            raise ValueError("Ask one short, plain question about the unresolved buyer requirement.")
        return NaturalAnswerDecision("clarify", clarification=prompt)
    if proposal.status == "unrelated":
        if proposal.effect is not None or proposal.option_ids is not None or target is not None or proposal.clarification is not None:
            raise ValueError("An unrelated answer cannot change a requirement.")
        return NaturalAnswerDecision("unrelated")
    if proposal.effect is None or proposal.option_ids is None or proposal.clarification is not None:
        raise ValueError("A decided answer needs a structured action and an option scope.")
    if len(proposal.option_ids) != len(scope) or set(proposal.option_ids) != set(scope):
        raise ValueError("The suggested options must match the scope supported by the answer.")
    effect = proposal.effect
    requirement_ids = question.requirement_ids or []
    if effect.requirement_id is not None and effect.requirement_id not in requirement_ids:
        raise ValueError("A suggested action must refer to a requirement issued for this question.")
    if effect.action == "waive" and requirement_ids and effect.requirement_id is None:
        raise ValueError("A waiver must identify which existing requirement it removes.")
    if len(requirement_ids) > 1:
        raise ValueError("The answer must identify the existing requirement before it can change.")
    topic = _topic(question)
    if effect.action in {"keep", "waive"}:
        raise ValueError("Keeping or waiving a requirement requires a clear instruction from the buyer.")
    if topic in {"customization", "deadline"}:
        if effect.action == "add" and requirement_ids:
            raise ValueError("The answer already refers to a requirement that must be replaced.")
        if target is None:
            raise ValueError("The answer needs an unambiguous order type or arrival date before settling that requirement.")
    return NaturalAnswerDecision("decided", effect, scope, target)
