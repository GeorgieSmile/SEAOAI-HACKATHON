from collections import defaultdict
from collections.abc import Iterator
from typing import Literal
import re
from uuid import NAMESPACE_URL, uuid4, uuid5

from .brief import written_order_scope
from .contracts import (
    BUYER_FACET_TOPICS, ORDER_CONFLICT_PROMPT, Answer, BriefChange, BuyerFacet, DeliveryCondition, IssuedQuestion, IssuedRound, OrderClarification,
    ListingSnapshot, QuestionInput, QuestionResult, ReviewTopic, RequirementEffect, RequirementProvenance, RequirementStatus, RequirementTarget,
    ResolvedRequirement,
)
from .supplier_targets import (
    DeliveryTarget, OrderTarget, SupplierTarget, order_statement_matches_scope, scoped_order_target, supplier_target_from_answer,
    stated_stock_request, validate_supplier_target,
)


RequirementProjection = Literal["current", "before_buying_brief", "before_review_fixes", "before_order_conflicts"]

_CONTEXT_BOUNDARIES = re.compile(r"[\r\n]+|(?<=[.!?。！？])\s+")


def _derived_id(case_id: str, origin: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"purchase-review:{case_id}:{origin}:{key}"))


def _quote_start(context: str, quote: str) -> int:
    start = context.find(quote)
    if start < 0 or context.find(quote, start + 1) >= 0:
        raise ValueError("A requirement must quote an unambiguous span from buyer context.")
    segment_starts = [0, *(boundary.end() for boundary in _CONTEXT_BOUNDARIES.finditer(context))]
    segment_ends = [*(boundary.start() for boundary in _CONTEXT_BOUNDARIES.finditer(context)), len(context)]
    starts = {offset + len(context[offset:]) - len(context[offset:].lstrip()) for offset in segment_starts}
    ends = {len(context[:offset].rstrip()) for offset in segment_ends}
    if start not in starts or start + len(quote) not in ends:
        raise ValueError("A requirement must quote whole buyer sentences or lines, including any qualifications.")
    return start


def _binding_origin(row: ResolvedRequirement) -> RequirementProvenance | None:
    return next((entry for entry in reversed(row.provenance) if entry.action in {"add", "replace"}), None)


def _with_supplier_target(
    row: ResolvedRequirement, *, selected_option_ids: list[str] | None = None, scoped_answers: bool = True,
    historical_order: bool = False, interpreted_refs: frozenset[str] = frozenset(),
) -> ResolvedRequirement:
    if row.status != "active":
        return row
    target = row.supplier_target
    origin = _binding_origin(row)
    if origin is not None and origin.input_ref in interpreted_refs:
        return row
    if (
        selected_option_ids is not None and origin is not None and origin.quote == row.statement
        and (origin.input_ref == "context" or scoped_answers and origin.input_ref.startswith("answer:"))
    ):
        scope = {"option_ids": origin.option_ids, "selected_option_ids": selected_option_ids}
        if scoped_answers and not order_statement_matches_scope(row.statement, row.topic, **scope):
            return row.model_copy(update={"status": "unresolved", "supplier_target": None})
        if target is None:
            target = scoped_order_target(row.statement, row.topic, row.buyer_facet, historical=not scoped_answers, historical_punctuation=historical_order, **scope)
    if target is None:
        if historical_order and row.topic == "customization":
            target = supplier_target_from_answer(row.statement, row.topic, row.buyer_facet, historical_order=True)
        else:
            target = supplier_target_from_answer(row.statement, row.topic, row.buyer_facet)
    return row.model_copy(update={"supplier_target": target}) if target != row.supplier_target else row


def _validate_source_target(
    target: SupplierTarget | None, quote: str, topic: ReviewTopic | None, facet: BuyerFacet | None, *,
    option_ids: list[str], selected_option_ids: list[str], source: str,
) -> None:
    if not order_statement_matches_scope(quote, topic, option_ids=option_ids, selected_option_ids=selected_option_ids):
        raise ValueError("The named options must match the scope of the buyer's exact statement.")
    supported = scoped_order_target(
        quote, topic, facet, option_ids=option_ids, selected_option_ids=selected_option_ids,
    )
    if target != supported:
        try:
            validate_supplier_target(target, quote, topic, facet)
        except ValueError as error:
            raise ValueError(f"{source}: {error}") from error


def _validate_topic_facet(topic: ReviewTopic | None, facet: BuyerFacet | None) -> None:
    expected = BUYER_FACET_TOPICS.get(facet) if facet is not None else None
    if topic is not None and expected is not None and topic != expected:
        raise ValueError("Use the topic that matches the buyer decision facet.")


def _context_rows(inputs: QuestionInput, result: QuestionResult) -> list[ResolvedRequirement]:
    rows = []
    seen = set()
    for index, seed in enumerate(result.context_requirements or [], start=1):
        _validate_topic_facet(seed.topic, seed.buyer_facet)
        scope = set(seed.option_ids)
        if len(scope) != len(seed.option_ids) or not scope.issubset(inputs.selected_option_ids):
            raise ValueError("Context requirements must apply to distinct selected options.")
        start = _quote_start(inputs.buyer_context, seed.quote)
        if seed.statement != seed.quote:
            raise ValueError("A context requirement statement must be the verbatim buyer excerpt.")
        keys = {(seed.statement, seed.topic, option_id) for option_id in scope}
        if seen & keys:
            raise ValueError("Duplicate context requirements overlap in scope.")
        seen.update(keys)
        if seed.supplier_target is not None and seed.status != "active":
            raise ValueError("Unresolved context cannot supply a supplier target.")
        if seed.status == "active":
            _validate_source_target(
                seed.supplier_target, seed.quote, seed.topic, seed.buyer_facet,
                option_ids=seed.option_ids, selected_option_ids=inputs.selected_option_ids, source=f"Context requirement {index}",
            )
        if seed.topic == "customization" and seed.status == "active":
            named = written_order_scope(seed.quote, inputs.snapshot, inputs.selected_option_ids)
            if named is not None and set(named) != scope:
                raise ValueError("Context order decisions must stay within the named option scope.")
        # Stamp newly recognized wording when issuing the decision. Reopening an older
        # confirmed case must not reinterpret its previously untyped requirement.
        supplier_target = seed.supplier_target
        if seed.status == "active" and supplier_target is None:
            supplier_target = stated_stock_request(seed.quote, seed.topic, seed.buyer_facet)
        row = _with_supplier_target(ResolvedRequirement(
            requirement_id=f"context:{index}", statement=seed.statement, option_ids=seed.option_ids,
            status=seed.status, topic=seed.topic, buyer_facet=seed.buyer_facet, supplier_target=supplier_target,
            provenance=[RequirementProvenance(
                input_ref="context", quote=seed.quote, question_text=None,
                action="add" if seed.status == "active" else "unresolved", option_ids=seed.option_ids,
                context_start=start,
            )],
        ), selected_option_ids=inputs.selected_option_ids)
        if row.supplier_target is not None:
            row = row.model_copy(update={"buyer_facet": row.supplier_target.kind})
        rows.append(row)
    return rows


def validate_requirement_plan(inputs: QuestionInput, result: QuestionResult) -> None:
    has_initial = result.context_requirements is not None
    if has_initial and inputs.round_number != 1:
        raise ValueError("Only the first question response can propose context requirements.")
    modern = has_initial or inputs.requirements is not None
    targets = {row.requirement_id: row for row in (*_context_rows(inputs, result), *(inputs.requirements or []))}
    previous_targets: set[tuple[str, str]] = set()
    for question_index, question in enumerate(result.questions, start=1):
        _validate_topic_facet(question.topic, question.buyer_facet)
        requirement_ids = question.requirement_ids
        effects = question.choice_effects
        if not modern:
            if requirement_ids is not None or effects is not None:
                raise ValueError("Question effects need stored context requirement resolution.")
            continue
        if requirement_ids is None or effects is None or question.topic is None:
            raise ValueError("Modern questions must include their topic, requirement IDs, and choice effects.")
        if len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("A question must refer to each requirement only once.")
        scope = set(question.option_ids)
        for requirement_id in requirement_ids:
            target = targets.get(requirement_id)
            if target is None or not scope.issubset(target.option_ids):
                raise ValueError("A question must refer to requirements covering its issued SKU scope.")
            if question.topic != target.topic and target.topic != "other":
                raise ValueError("A follow-up topic must match its requirement.")
            target_facet = target.buyer_facet
            if (
                target_facet is not None and BUYER_FACET_TOPICS.get(target_facet) == target.topic
                and question.buyer_facet not in {None, target_facet}
            ):
                raise ValueError("A follow-up facet must match its requirement.")
            scoped_targets = {(requirement_id, option_id) for option_id in scope}
            if previous_targets & scoped_targets:
                raise ValueError("A question round must not target the same requirement within overlapping SKU scope.")
            previous_targets.update(scoped_targets)
        effect_choices = {effect.choice for effect in effects}
        if len(effect_choices) != len(effects) or not effect_choices.issubset(question.choices):
            raise ValueError("Each effect must belong to a distinct issued answer choice.")
        for effect_index, effect in enumerate(effects, start=1):
            _validate_effect_target(effect, requirement_ids)
            if effect.supplier_target is not None and effect.statement is None:
                raise ValueError("Only an add or replacement choice can declare a new supplier target.")
            _validate_source_target(
                effect.supplier_target, effect.choice, question.topic, question.buyer_facet,
                option_ids=question.option_ids, selected_option_ids=inputs.selected_option_ids,
                source=f"Question {question_index}, choice effect {effect_index}",
            )


def issue_requirement_plan(
    inputs: QuestionInput, result: QuestionResult, initial: list[ResolvedRequirement] | None,
) -> tuple[list[ResolvedRequirement] | None, list[IssuedQuestion]]:
    context_ids = {}
    if result.context_requirements is not None:
        initial = []
        for row in _context_rows(inputs, result):
            stable_id = _derived_id(inputs.case_id, "context", row.requirement_id)
            context_ids[row.requirement_id] = stable_id
            initial.append(row.model_copy(update={"requirement_id": stable_id}))
    targets = {row.requirement_id: row for row in [*(initial or []), *(inputs.requirements or [])]}
    questions = []
    for question in result.questions:
        draft = question.model_dump()
        if question.requirement_ids is not None:
            requirement_ids = [context_ids.get(target, target) for target in question.requirement_ids]
            draft["requirement_ids"] = requirement_ids
            draft["requirement_targets"] = [
                RequirementTarget(
                    requirement_id=target, statement=targets[target].statement, option_ids=question.option_ids,
                ).model_dump()
                for target in requirement_ids
            ]
            for effect in draft["choice_effects"]:
                if effect["statement"] is not None and effect["supplier_target"] is None:
                    stock = stated_stock_request(effect["statement"], question.topic, question.buyer_facet)
                    if stock is not None:
                        effect["supplier_target"] = stock.model_dump()
                target = effect["requirement_id"]
                effect["requirement_id"] = context_ids.get(target, target)
        questions.append(IssuedQuestion(question_id=str(uuid4()), **draft))
    return initial, questions


def _answer_matches_scope(question: IssuedQuestion, answer_text: str | None, selected_option_ids: list[str]) -> bool:
    return answer_text is None or order_statement_matches_scope(
        answer_text, question.topic, option_ids=question.option_ids, selected_option_ids=selected_option_ids,
    )


def validate_submission_effect(
    question: IssuedQuestion, answer: Answer, *, selected_option_ids: list[str] | None = None,
    snapshot: ListingSnapshot | None = None,
) -> None:
    if decision := answer.natural_decision:
        _validate_effect_target(decision.effect, question.requirement_ids or [])
        if not set(decision.option_ids).issubset(question.option_ids) or answer.answer_text in question.choices:
            raise ValueError("An interpreted answer must stay within its issued question and option scope.")
        topic = question.topic or BUYER_FACET_TOPICS.get(question.buyer_facet)
        target = decision.supplier_target
        if target is not None and (
            BUYER_FACET_TOPICS.get(target.kind) != topic or question.buyer_facet not in {None, target.kind}
        ):
            raise ValueError("The inferred term must answer the issued question's buyer facet.")
        if decision.delivery_condition is not None and (
            topic != "customization" or question.buyer_facet not in {None, "order_type"}
        ):
            raise ValueError("A custom order condition must answer an order-type question.")
        return
    effect = answer.requirement_effect
    if effect is not None:
        if question.requirement_ids is None:
            raise ValueError("This older question did not issue structured requirement effects.")
        if answer.answer_text in question.choices:
            raise ValueError("Submit the issued choice without a separate requirement effect.")
        _validate_effect_target(effect, question.requirement_ids)
    if selected_option_ids is None or answer.answer_text is None:
        return
    if not _answer_matches_scope(question, answer.answer_text, selected_option_ids):
        raise ValueError("The options named in your answer must match the options shown for this question.")
    if snapshot is not None and question.topic == "customization":
        named = written_order_scope(answer.answer_text, snapshot, selected_option_ids)
        if named is not None and set(named) != set(question.option_ids):
            raise ValueError("The options named in your answer must match the options shown for this question.")


def _validate_effect_target(effect: RequirementEffect, target_ids: list[str]) -> None:
    if effect.requirement_id is not None and effect.requirement_id not in target_ids:
        raise ValueError("The answer effect must target a requirement issued for this question.")
    if effect.action == "waive" and effect.requirement_id is None and target_ids:
        raise ValueError("Choose which issued requirement to waive.")


def legacy_context_rows(case_id: str, context: str, selected: list[str]) -> list[ResolvedRequirement]:
    if not context.strip():
        return []
    return [ResolvedRequirement(
        requirement_id=_derived_id(case_id, "context", "legacy"), statement=context, option_ids=selected,
        status="unresolved", topic="other",
        provenance=[RequirementProvenance(
            input_ref="context", quote=context, question_text=None, action="unresolved", option_ids=selected,
            context_start=0,
        )],
    )]


def recover_direct_legacy_requirement(
    *, case_id: str, input_ref: str, quote: str, question_text: str | None, option_ids: list[str],
    topic: ReviewTopic, buyer_facet: BuyerFacet | None, context_start: int | None,
) -> ResolvedRequirement | None:
    origin = "context" if input_ref == "context" else "question"
    row = _with_supplier_target(ResolvedRequirement(
        requirement_id=_derived_id(case_id, origin, "migrated" if origin == "context" else input_ref[7:]),
        statement=quote, option_ids=option_ids, status="active", topic=topic,
        buyer_facet=buyer_facet,
        provenance=[RequirementProvenance(
            input_ref=input_ref, quote=quote, question_text=question_text, action="add",
            option_ids=option_ids, context_start=context_start,
        )],
    ))
    if row.supplier_target is None:
        return None
    return row.model_copy(update={"buyer_facet": row.supplier_target.kind})


def _order_conflicts(
    case_id: str, rows: dict[str, ResolvedRequirement], *, historical: bool = False,
    interpreted_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[ResolvedRequirement, tuple[str, ...]]]:
    by_option: dict[str, list[tuple[str, OrderTarget]]] = {}
    for row in rows.values():
        target = _with_supplier_target(row, historical_order=historical, interpreted_refs=interpreted_refs).supplier_target
        if row.delivery_condition is not None:
            target = OrderTarget(value="custom_orders_acceptable")
        if row.status == "active" and isinstance(target, OrderTarget):
            for option_id in row.option_ids:
                by_option.setdefault(option_id, []).append((row.requirement_id, target))

    scopes: dict[tuple[str, ...], list[str]] = {}
    for option_id, entries in by_option.items():
        if len({target.value for _, target in entries}) > 1:
            sources = tuple(sorted(requirement_id for requirement_id, _ in entries))
            scopes.setdefault(sources, []).append(option_id)

    conflicts = {}
    for sources, option_ids in scopes.items():
        requirement_id = _derived_id(case_id, "order-conflict", ":".join(sources))
        conflicts[requirement_id] = (ResolvedRequirement(
            requirement_id=requirement_id,
            statement="Decide whether custom orders are acceptable.",
            option_ids=option_ids, status="unresolved", topic="customization", buyer_facet="order_type",
            provenance=[entry for source in (sources if historical else rows) if source in sources
                        for entry in rows[source].provenance],
            order_conflict=None if historical else True,
        ), sources)
    return conflicts


def _remove_conflicting_scope(
    rows: dict[str, ResolvedRequirement], sources: tuple[str, ...], option_ids: list[str],
) -> None:
    for source in sources:
        row = rows[source]
        remaining = [option_id for option_id in row.option_ids if option_id not in option_ids]
        if remaining:
            rows[source] = row.model_copy(update={"option_ids": remaining})
        else:
            del rows[source]


def _visible_requirements(rows: dict[str, ResolvedRequirement]) -> list[ResolvedRequirement]:
    settled: dict[tuple[ReviewTopic, BuyerFacet], set[str]] = {}
    for row in rows.values():
        if row.status not in {"active", "waived"}:
            continue
        target = row.supplier_target
        if row.status == "waived" and row.buyer_facet is None:
            # Waiving removes the supplier target while keeping the settled decision.
            target = supplier_target_from_answer(row.statement, row.topic, None)
        facet = target.kind if target is not None else row.buyer_facet
        if facet not in {None, "other"}:
            settled.setdefault((row.topic, facet), set()).update(row.option_ids)

    visible = []
    for row in rows.values():
        source = row.provenance[0]
        if (
            row.status == "unresolved" and len(row.provenance) == 1
            and source.input_ref.startswith("answer:") and source.quote is None
            and row.buyer_facet not in {None, "other"}
        ):
            covered = settled.get((row.topic, row.buyer_facet), set())
            open_scope = [option_id for option_id in row.option_ids if option_id not in covered]
            if not open_scope:
                continue
            row = row.model_copy(update={"option_ids": open_scope})
        visible.append(row)
    return visible


def _scoped_targets(
    target_ids: list[str], option_ids: list[str], forks: dict[str, dict[str, str]],
) -> Iterator[tuple[str, list[str]]]:
    for target_id in target_ids:
        fork_by_option = forks.get(target_id, {})
        scopes: dict[str, list[str]] = {}
        for option_id in option_ids:
            scoped_target = fork_by_option.get(option_id, target_id)
            scopes.setdefault(scoped_target, []).append(option_id)
        yield from scopes.items()


def _save_scoped_change(
    case_id: str, rows: dict[str, ResolvedRequirement], original: ResolvedRequirement,
    updated: ResolvedRequirement, source_key: str,
) -> None:
    original_id = original.requirement_id
    remaining = [option_id for option_id in original.option_ids if option_id not in updated.option_ids]
    if remaining:
        fork_id = _derived_id(case_id, original_id, source_key)
        rows[original_id] = original.model_copy(update={"option_ids": remaining})
        rows[fork_id] = updated.model_copy(update={"requirement_id": fork_id})
    else:
        rows[original_id] = updated



def _apply_order_clarification(
    case_id: str, rows: dict[str, ResolvedRequirement], clarification: OrderClarification,
    forks: dict[str, dict[str, str]], interpreted_refs: frozenset[str],
) -> None:
    option_id = clarification.option_id
    target_id = clarification.requirement_id
    scope = [option_id]
    branch = _order_conflicts(case_id, rows, interpreted_refs=interpreted_refs).get(target_id)
    if branch is not None:
        decision, sources = branch
        candidates = [rows[key] for key in sources if option_id in rows[key].option_ids]
    else:
        decision = rows.get(target_id)
        sources = (target_id,)
        candidates = [decision] if decision is not None else []
    if decision is None or not decision.order_conflict or option_id not in decision.option_ids:
        raise ValueError("Choose an open order-type conflict for this option.")
    conditional = next(
        (row for row in candidates if row.delivery_condition is not None), None,
    ) if clarification.choice == "custom_orders_acceptable" else None

    source = RequirementProvenance(
        input_ref=f"clarification:{clarification.clarification_id}", quote=clarification.answer_text,
        question_text=ORDER_CONFLICT_PROMPT, action="keep" if conditional else "replace", option_ids=scope,
    )
    new_id = _derived_id(case_id, target_id, source.input_ref)
    resolved = decision.model_copy(update={
        "requirement_id": new_id, "option_ids": scope, "status": "active", "order_conflict": None,
        "statement": conditional.statement if conditional else clarification.answer_text,
        "delivery_condition": conditional.delivery_condition if conditional else None,
        "supplier_target": None if conditional else OrderTarget(value=clarification.choice),
        "provenance": [entry for entry in decision.provenance if option_id in entry.option_ids] + [source],
    })
    _remove_conflicting_scope(rows, sources, scope)
    rows[new_id] = resolved
    forks.setdefault(target_id, {})[option_id] = new_id


def _apply_brief_change(case_id: str, rows: dict[str, ResolvedRequirement], change: BriefChange) -> None:
    proposal = change.proposal
    kind = proposal.kind
    topic, facet = ("deadline", "delivery_deadline") if kind == "deadline" else ("customization", "order_type")
    if kind == "unclear":
        topic, facet = "other", None
    target = proposal.date if kind == "deadline" else None
    if kind in {"ready_made", "custom"}:
        target = OrderTarget(value="ready_made_only" if kind == "ready_made" else "custom_orders_acceptable")
    condition = DeliveryCondition(
        follows_deadline=proposal.date is None, latest_arrival=proposal.date,
    ) if kind == "custom_by_deadline" else None
    ref = f"brief:{change.change_id}"
    source = RequirementProvenance(
        input_ref=ref, quote=change.text, question_text=None, option_ids=proposal.option_ids,
        action="unresolved" if kind == "unclear" else "add",
    )
    if kind == "unclear":
        requirement_id = _derived_id(case_id, ref, "open")
        rows[requirement_id] = ResolvedRequirement(
            requirement_id=requirement_id, statement=change.text, option_ids=proposal.option_ids,
            status="unresolved", topic="other", provenance=[source],
        )
        return
    related_source = []
    if change.resolves_change_id is not None:
        prior_id = _derived_id(case_id, f"brief:{change.resolves_change_id}", "open")
        prior = rows.pop(prior_id)
        if not set(prior.option_ids).issuperset(proposal.option_ids):
            raise ValueError("The clarification must stay within the open correction's option scope.")
        related_source = prior.provenance
    replace = kind in {"deadline", "custom_by_deadline"} or change.text.lstrip().casefold().startswith("actually,")
    for option_id in proposal.option_ids:
        scope = [option_id]
        related = list(related_source)
        if replace:
            for row in list(rows.values()):
                if row.topic != topic or row.buyer_facet not in {None, facet} or option_id not in row.option_ids:
                    continue
                # A legacy customization note without an order target may concern a different subject.
                if topic == "customization" and row.buyer_facet is None and not (
                    isinstance(row.supplier_target, OrderTarget) or row.delivery_condition is not None
                ):
                    continue
                related.extend(entry for entry in row.provenance if entry not in related)
                remaining = [item for item in row.option_ids if item != option_id]
                if remaining:
                    rows[row.requirement_id] = row.model_copy(update={"option_ids": remaining})
                else:
                    del rows[row.requirement_id]
        requirement_id = _derived_id(case_id, ref, option_id)
        rows[requirement_id] = ResolvedRequirement(
            requirement_id=requirement_id, statement=change.text, option_ids=scope,
            status="unresolved" if kind == "unclear" else "active", topic=topic, buyer_facet=facet,
            supplier_target=target, delivery_condition=condition, provenance=[*related, source],
        )


def arrival_cutoff(rows: list[ResolvedRequirement], option_ids: list[str]) -> DeliveryTarget | None:
    scope = set(option_ids)
    dates = {
        row.supplier_target if isinstance(row.supplier_target, DeliveryTarget) else None
        for row in rows if row.topic == "deadline" and row.buyer_facet in {None, "delivery_deadline"}
        and row.status in {"active", "unresolved"} and scope.issubset(row.option_ids)
    }
    return next(iter(dates)) if len(dates) == 1 else None


def _link_arrival_conditions(rows: list[ResolvedRequirement], interpreted_refs: frozenset[str]) -> list[ResolvedRequirement]:
    linked = []
    for row in rows:
        condition = row.delivery_condition
        if condition is None or not condition.follows_deadline:
            linked.append(row)
            continue
        origin = _binding_origin(row)
        if origin is not None and origin.input_ref in interpreted_refs:
            dates = {arrival_cutoff(rows, [option_id]) for option_id in row.option_ids}
            date = next(iter(dates)) if len(dates) == 1 else None
        else:
            date = arrival_cutoff(rows, row.option_ids)
        linked.append(row.model_copy(update={
            "status": "active" if row.status == "active" and date is not None else "unresolved",
            "delivery_condition": condition.model_copy(update={"latest_arrival": date}),
        }))
    return linked


def resolve_requirements(
    case_id: str, initial: list[ResolvedRequirement], rounds: list[IssuedRound], *, buyer_context: str,
    projection: RequirementProjection = "current", selected_option_ids: list[str] | None = None,
    brief_changes: tuple[BriefChange, ...] = (), order_clarifications: tuple[OrderClarification, ...] = (),
) -> list[ResolvedRequirement]:
    historical = projection != "current"
    selected = selected_option_ids or []
    interpreted_refs = frozenset(
        f"answer:{answer.question_id}"
        for round in rounds for answer in round.answers or [] if answer.natural_decision is not None
    )
    rows = {row.requirement_id: row for row in initial if row.provenance[0].input_ref == "context"}
    for key, row in rows.items():
        if row.status == "active":
            origin = row.provenance[0]
            if origin.quote != row.statement or origin.context_start != _quote_start(buyer_context, row.statement):
                raise ValueError("A stored context requirement must match its original buyer statement.")
            if not historical:
                rows[key] = _with_supplier_target(row, selected_option_ids=selected)
    migrated_answers = {row.provenance[0].input_ref: row for row in initial if row.provenance[0].input_ref.startswith("answer:")}
    conflict_forks: dict[str, dict[str, str]] = {}
    events = defaultdict(list)
    for event in [*brief_changes, *order_clarifications]:
        events[event.after_round].append(event)

    def apply_events(after_round: int) -> None:
        for event in sorted(events.pop(after_round, []), key=lambda item: item.revision):
            if isinstance(event, BriefChange):
                _apply_brief_change(case_id, rows, event)
            else:
                _apply_order_clarification(case_id, rows, event, conflict_forks, interpreted_refs)

    apply_events(0)
    for issued_round in rounds:
        if issued_round.answers is None:
            break
        order_conflicts = _order_conflicts(case_id, rows, historical=historical, interpreted_refs=interpreted_refs)
        # Questions in the same round may target disjoint subsets of a row. They use
        # the IDs issued before that round, even as earlier questions split a row.
        for question, answer in zip(issued_round.questions, issued_round.answers, strict=True):
            validate_submission_effect(question, answer)
            input_ref = f"answer:{question.question_id}"
            migrated = migrated_answers.get(input_ref)
            if migrated is not None:
                source = migrated.provenance[0]
                if (answer.answer_text, question.prompt, question.option_ids) == (source.quote, source.question_text, source.option_ids):
                    rows[migrated.requirement_id] = migrated
                    continue
            if answer.answer_text is None and question.requirement_ids:
                # The saved answer records the skip, while the earlier decision still stands.
                continue
            choice = next((item for item in question.choice_effects or [] if item.choice == answer.answer_text), None)
            natural = answer.natural_decision
            if answer.answer_mode == "natural" and natural is None and question.requirement_ids:
                continue
            effect = natural.effect if natural is not None else choice or answer.requirement_effect
            scope = answer.effective_scope(question.option_ids)
            wrong_scope = natural is None and not historical and not _answer_matches_scope(question, answer.answer_text, selected)
            if wrong_scope:
                # Keep older issued references replayable without letting a wrong-SKU answer alter the decision.
                for target_id, target_scope in _scoped_targets(
                    [effect.requirement_id] if effect and effect.requirement_id else question.requirement_ids or [],
                    question.option_ids, conflict_forks,
                ):
                    if target_id in rows:
                        previous = rows[target_id]
                        _save_scoped_change(case_id, rows, previous, previous.model_copy(update={"option_ids": target_scope}), question.question_id)
                continue
            action = effect.action if effect is not None else "unresolved"
            provenance = RequirementProvenance(
                input_ref=input_ref, quote=answer.answer_text,
                question_text=question.prompt, action=action, option_ids=scope,
            )
            topic = question.topic or (BUYER_FACET_TOPICS.get(question.buyer_facet) if natural is not None else None) or "other"
            supplier_target = (
                natural.supplier_target if natural is not None
                else choice.supplier_target if choice is not None and action in {"add", "replace"} else None
            )
            delivery_condition = natural.delivery_condition if natural is not None else None
            facet = question.buyer_facet
            if natural is not None and facet is None:
                facet = "order_type" if delivery_condition is not None else supplier_target.kind if supplier_target is not None else None
            if effect is not None and effect.requirement_id is not None:
                target_ids = [effect.requirement_id]
            elif effect is None and question.requirement_ids:
                target_ids = question.requirement_ids
            else:
                requirement_id = _derived_id(case_id, "question", question.question_id)
                status = {"add": "active", "waive": "waived"}.get(action, "unresolved")
                row = ResolvedRequirement(
                    requirement_id=requirement_id, statement=answer.answer_text or question.prompt,
                    option_ids=scope, status=status,
                    topic=topic, buyer_facet=facet, provenance=[provenance],
                    supplier_target=supplier_target, delivery_condition=delivery_condition,
                )
                rows[requirement_id] = _with_supplier_target(
                    row, selected_option_ids=selected, interpreted_refs=interpreted_refs,
                ) if not historical else row
                continue
            for target_id, target_scope in _scoped_targets(target_ids, scope, conflict_forks):
                if conflict := order_conflicts.get(target_id):
                    if projection in {"before_review_fixes", "before_order_conflicts"} and action not in {"replace", "waive"}:
                        continue
                    decision, sources = conflict
                    if not set(target_scope).issubset(decision.option_ids):
                        raise ValueError("An order decision cannot extend beyond its conflicting options.")
                    _remove_conflicting_scope(rows, sources, target_scope)
                    status = {"replace": "active", "waive": "waived"}.get(action, "unresolved")
                    resolved_id = (
                        target_id if status == "unresolved" and set(target_scope) == set(decision.option_ids)
                        else _derived_id(case_id, target_id, question.question_id)
                    )
                    resolved = decision.model_copy(update={
                        "requirement_id": resolved_id, "option_ids": target_scope,
                        "statement": answer.answer_text if action == "replace" else decision.statement,
                        "status": status,
                        "supplier_target": supplier_target,
                        "delivery_condition": delivery_condition,
                        "provenance": [*decision.provenance, provenance],
                        "order_conflict": decision.order_conflict if status == "unresolved" else None,
                    })
                    rows[resolved_id] = _with_supplier_target(
                        resolved, selected_option_ids=selected, interpreted_refs=interpreted_refs,
                    ) if not historical else resolved
                    if status == "unresolved" and resolved_id != target_id:
                        # An existing issued round may still refer to the original conflict scope.
                        conflict_forks.setdefault(target_id, {}).update(
                            (option_id, resolved_id) for option_id in target_scope
                        )
                    continue
                row = rows[target_id]
                if not set(target_scope).issubset(row.option_ids):
                    raise ValueError("A requirement change cannot extend beyond the issued scope.")
                remaining = [option_id for option_id in row.option_ids if option_id not in target_scope]
                status = _updated_status(row, effect)
                retained_target = supplier_target if action == "replace" else row.supplier_target
                updated = row.model_copy(update={
                    "statement": answer.answer_text if action == "replace" else row.statement,
                    "option_ids": target_scope, "status": status, "topic": topic if action == "replace" else row.topic,
                    "buyer_facet": facet if action == "replace" else row.buyer_facet,
                    "supplier_target": retained_target if status == "active" else None,
                    "provenance": [*row.provenance, provenance],
                    "order_conflict": row.order_conflict if status == "unresolved" else None,
                    "delivery_condition": delivery_condition if action == "replace" else None if action == "waive" else row.delivery_condition,
                })
                if not historical:
                    updated = _with_supplier_target(updated, selected_option_ids=selected, interpreted_refs=interpreted_refs)
                if remaining:
                    fork_id = _derived_id(case_id, target_id, question.question_id)
                    rows[target_id] = row.model_copy(update={"option_ids": remaining})
                    rows[fork_id] = updated.model_copy(update={"requirement_id": fork_id})
                else:
                    rows[target_id] = updated
        apply_events(issued_round.round_number)
    if events:
        raise ValueError("Brief changes must follow completed question rounds.")
    if not historical:
        rows = {row.requirement_id: row for row in _link_arrival_conditions(list(rows.values()), interpreted_refs)}
    if projection != "before_order_conflicts":
        # Keep the original decisions for replay. Expose one unresolved decision
        # for each overlapping scope, with both source statements attached.
        for conflict, sources in _order_conflicts(
            case_id, rows, historical=historical, interpreted_refs=interpreted_refs,
        ).values():
            _remove_conflicting_scope(rows, sources, conflict.option_ids)
            rows[conflict.requirement_id] = conflict
    resolved = {
        key: _with_supplier_target(row, historical_order=historical, interpreted_refs=interpreted_refs)
        for key, row in rows.items()
    }
    # Keep suppressed rows addressable by already-issued follow-ups during replay.
    return _visible_requirements(resolved) if projection in {"current", "before_buying_brief"} else list(resolved.values())


def _updated_status(row: ResolvedRequirement, effect: RequirementEffect | None) -> RequirementStatus:
    if effect is None:
        return "unresolved"
    if effect.action == "waive":
        return "waived"
    if effect.action == "replace":
        return "active"
    if row.status != "unresolved":
        return row.status
    for entry in reversed(row.provenance):
        if entry.action in {"add", "replace"}:
            return "active" if entry.quote == row.statement else "unresolved"
        if entry.action == "waive":
            return "waived"
    return "unresolved"
