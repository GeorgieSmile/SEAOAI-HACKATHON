from collections import defaultdict
import re
from uuid import NAMESPACE_URL, uuid4, uuid5

from .contracts import (
    BUYER_FACET_TOPICS, ORDER_CONFLICT_PROMPT, Answer, BuyerFacet, IssuedQuestion, IssuedRound, OrderClarification,
    QuestionInput, QuestionResult, ReviewTopic, RequirementEffect, RequirementProvenance, RequirementStatus, RequirementTarget,
    ResolvedRequirement,
)
from .supplier_targets import (
    OrderTarget, SupplierTarget, order_statement_matches_scope, scoped_order_target, supplier_target_from_answer,
    validate_supplier_target,
)


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
) -> ResolvedRequirement:
    if row.status != "active":
        return row
    target = row.supplier_target
    origin = _binding_origin(row)
    if (
        selected_option_ids is not None and origin is not None and origin.quote == row.statement
        and (origin.input_ref == "context" or scoped_answers and origin.input_ref.startswith("answer:"))
    ):
        scope = {"option_ids": origin.option_ids, "selected_option_ids": selected_option_ids}
        if scoped_answers and not order_statement_matches_scope(row.statement, row.topic, **scope):
            return row.model_copy(update={"status": "unresolved", "supplier_target": None})
        if target is None:
            target = scoped_order_target(row.statement, row.topic, row.buyer_facet, historical=not scoped_answers, **scope)
    if target is None:
        target = supplier_target_from_answer(row.statement, row.topic, row.buyer_facet)
    return row.model_copy(update={"supplier_target": target}) if target != row.supplier_target else row


def _validate_source_target(
    target: SupplierTarget | None, quote: str, topic: ReviewTopic | None, facet: BuyerFacet | None, *,
    option_ids: list[str], selected_option_ids: list[str],
) -> None:
    if not order_statement_matches_scope(quote, topic, option_ids=option_ids, selected_option_ids=selected_option_ids):
        raise ValueError("The named options must match the scope of the buyer's exact statement.")
    supported = scoped_order_target(
        quote, topic, facet, option_ids=option_ids, selected_option_ids=selected_option_ids,
    )
    if target != supported:
        validate_supplier_target(target, quote, topic, facet)


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
                option_ids=seed.option_ids, selected_option_ids=inputs.selected_option_ids,
            )
        row = _with_supplier_target(ResolvedRequirement(
            requirement_id=f"context:{index}", statement=seed.statement, option_ids=seed.option_ids,
            status=seed.status, topic=seed.topic, buyer_facet=seed.buyer_facet, supplier_target=seed.supplier_target,
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
    for question in result.questions:
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
        for effect in effects:
            _validate_effect_target(effect, requirement_ids)
            if effect.supplier_target is not None and effect.statement is None:
                raise ValueError("Only an add or replacement choice can declare a new supplier target.")
            _validate_source_target(
                effect.supplier_target, effect.choice, question.topic, question.buyer_facet,
                option_ids=question.option_ids, selected_option_ids=inputs.selected_option_ids,
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
) -> None:
    effect = answer.requirement_effect
    if effect is not None:
        if question.requirement_ids is None:
            raise ValueError("This older question did not issue structured requirement effects.")
        if answer.answer_text in question.choices:
            raise ValueError("Submit the issued choice without a separate requirement effect.")
        _validate_effect_target(effect, question.requirement_ids)
    if selected_option_ids is None or answer.answer_text is None:
        return
    choice = next((item for item in question.choice_effects or [] if item.choice == answer.answer_text), None)
    applied = choice or effect
    if applied is not None and not _answer_matches_scope(question, answer.answer_text, selected_option_ids):
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


def _order_conflict_members(
    rows: list[ResolvedRequirement], option_id: str,
) -> list[tuple[ResolvedRequirement, OrderTarget]]:
    members = []
    for row in rows:
        if row.status != "active" or row.topic != "customization" or option_id not in row.option_ids:
            continue
        target = _with_supplier_target(row).supplier_target
        if isinstance(target, OrderTarget):
            members.append((row, target))
    return members if len({target.value for _, target in members}) > 1 else []


def _order_conflict_id(case_id: str, option_id: str) -> str:
    return _derived_id(case_id, "order-conflict", option_id)


def _project_order_conflicts(
    case_id: str, rows: list[ResolvedRequirement], selected_option_ids: list[str],
) -> list[ResolvedRequirement]:
    conflicts = []
    conflicting_options = defaultdict(set)
    for option_id in selected_option_ids:
        members = _order_conflict_members(rows, option_id)
        if not members:
            continue
        sources = []
        for row, _ in members:
            conflicting_options[row.requirement_id].add(option_id)
            origin = next((entry for entry in reversed(row.provenance) if entry.action in {"add", "replace"}), None)
            if origin is None or origin.quote != row.statement:
                raise ValueError("An order conflict must quote the original buyer decisions.")
            if origin not in sources:
                sources.append(origin)
        conflicts.append(ResolvedRequirement(
            requirement_id=_order_conflict_id(case_id, option_id), statement=ORDER_CONFLICT_PROMPT,
            option_ids=[option_id], status="unresolved", topic="customization", buyer_facet="order_type",
            provenance=sources, order_conflict=True,
        ))

    resolved = []
    for row in rows:
        remaining = [option_id for option_id in row.option_ids if option_id not in conflicting_options[row.requirement_id]]
        if remaining:
            resolved.append(row if remaining == row.option_ids else row.model_copy(update={"option_ids": remaining}))
    return [*resolved, *conflicts]


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
) -> None:
    option_id = clarification.option_id
    members = _order_conflict_members(list(rows.values()), option_id)
    if clarification.requirement_id != _order_conflict_id(case_id, option_id) or not members:
        raise ValueError("An order clarification must refer to the conflict that was open for this option.")
    surviving_id = next(row.requirement_id for row, target in members if target.value == clarification.choice)
    for row, target in members:
        kept = row.requirement_id == surviving_id
        scope = [option_id]
        source = RequirementProvenance(
            input_ref=f"clarification:{clarification.clarification_id}", quote=clarification.answer_text,
            question_text=ORDER_CONFLICT_PROMPT, action="replace" if kept else "waive", option_ids=scope,
        )
        updated = row.model_copy(update={
            "statement": clarification.answer_text if kept else row.statement, "option_ids": scope,
            "status": "active" if kept else "waived", "supplier_target": target if kept else None,
            "provenance": [*row.provenance, source],
        })
        _save_scoped_change(case_id, rows, row, updated, source.input_ref)


def resolve_requirements(
    case_id: str, initial: list[ResolvedRequirement], rounds: list[IssuedRound], *, buyer_context: str,
    selected_option_ids: list[str], order_clarifications: list[OrderClarification], include_order_conflicts: bool = True,
    scoped_answers: bool = True,
) -> list[ResolvedRequirement]:
    rows = {row.requirement_id: row for row in initial if row.provenance[0].input_ref == "context"}
    for requirement_id, row in rows.items():
        if row.status == "active":
            origin = row.provenance[0]
            if origin.quote != row.statement or origin.context_start != _quote_start(buyer_context, row.statement):
                raise ValueError("A stored context requirement must match its original buyer statement.")
            rows[requirement_id] = _with_supplier_target(
                row, selected_option_ids=selected_option_ids, scoped_answers=scoped_answers,
            )
    migrated_answers = {row.provenance[0].input_ref: row for row in initial if row.provenance[0].input_ref.startswith("answer:")}
    clarifications_by_round = defaultdict(list)
    for clarification in order_clarifications:
        clarifications_by_round[clarification.after_round].append(clarification)
    for clarification in clarifications_by_round.pop(0, []):
        _apply_order_clarification(case_id, rows, clarification)
    for issued_round in rounds:
        if issued_round.answers is None:
            break
        # Questions in the same round may target disjoint subsets of a row. They use
        # the IDs issued before that round, even as earlier questions split a row.
        for question, answer in zip(issued_round.questions, issued_round.answers, strict=True):
            validate_submission_effect(question, answer)
            input_ref = f"answer:{question.question_id}"
            migrated = migrated_answers.get(input_ref)
            if migrated is not None:
                source = migrated.provenance[0]
                if (answer.answer_text, question.prompt, question.option_ids) == (source.quote, source.question_text, source.option_ids):
                    rows[migrated.requirement_id] = _with_supplier_target(
                        migrated, selected_option_ids=selected_option_ids, scoped_answers=scoped_answers,
                    )
                    continue
            if answer.answer_text is None and question.requirement_ids:
                # The saved answer records the skip, while the earlier decision still stands.
                continue
            choice = next((item for item in question.choice_effects or [] if item.choice == answer.answer_text), None)
            effect = choice or answer.requirement_effect
            wrong_scope = scoped_answers and effect is not None and not _answer_matches_scope(
                question, answer.answer_text, selected_option_ids,
            )
            action = effect.action if effect is not None and not wrong_scope else "unresolved"
            provenance = RequirementProvenance(
                input_ref=input_ref, quote=answer.answer_text,
                question_text=question.prompt, action=action, option_ids=question.option_ids,
            )
            topic = question.topic or "other"
            supplier_target = choice.supplier_target if choice is not None and action in {"add", "replace"} else None
            if effect is not None and effect.requirement_id is not None:
                target_ids = [effect.requirement_id]
            elif effect is None and question.requirement_ids:
                target_ids = question.requirement_ids
            else:
                requirement_id = _derived_id(case_id, "question", question.question_id)
                status = {"add": "active", "waive": "waived"}.get(action, "unresolved")
                row = ResolvedRequirement(
                    requirement_id=requirement_id, statement=answer.answer_text or question.prompt,
                    option_ids=question.option_ids, status=status,
                    topic=topic, buyer_facet=question.buyer_facet, provenance=[provenance],
                    supplier_target=supplier_target,
                )
                rows[requirement_id] = _with_supplier_target(
                    row, selected_option_ids=selected_option_ids, scoped_answers=scoped_answers,
                )
                continue
            for target_id in target_ids:
                row = rows[target_id]
                if not set(question.option_ids).issubset(row.option_ids):
                    raise ValueError("A requirement change cannot extend beyond the issued scope.")
                if wrong_scope:
                    # A previous version could save an effect naming a different SKU. Preserve
                    # the prior scoped decision and replayable ID. The original attempt remains in the round.
                    preserved = row.model_copy(update={"option_ids": question.option_ids})
                    _save_scoped_change(case_id, rows, row, preserved, question.question_id)
                    continue
                status = _updated_status(row.status, row.provenance, effect)
                retained_target = supplier_target if action == "replace" else row.supplier_target
                updated = row.model_copy(update={
                    "statement": answer.answer_text if action == "replace" else row.statement,
                    "option_ids": question.option_ids, "status": status, "topic": topic if action == "replace" else row.topic,
                    "buyer_facet": question.buyer_facet if action == "replace" else row.buyer_facet,
                    "supplier_target": retained_target if status == "active" else None,
                    "provenance": [*row.provenance, provenance],
                })
                updated = _with_supplier_target(updated, selected_option_ids=selected_option_ids, scoped_answers=scoped_answers)
                _save_scoped_change(case_id, rows, row, updated, question.question_id)
        for clarification in clarifications_by_round.pop(issued_round.round_number, []):
            _apply_order_clarification(case_id, rows, clarification)
    if clarifications_by_round:
        raise ValueError("Order clarifications must follow completed question rounds.")
    resolved = [_with_supplier_target(row) for row in rows.values()]
    return _project_order_conflicts(case_id, resolved, selected_option_ids) if include_order_conflicts else resolved


def _updated_status(
    status: RequirementStatus, provenance: list[RequirementProvenance], effect: RequirementEffect | None,
) -> RequirementStatus:
    if effect is None:
        return "unresolved"
    if effect.action == "waive":
        return "waived"
    if effect.action == "replace":
        return "active"
    if status != "unresolved":
        return status
    for entry in reversed(provenance):
        if entry.action in {"add", "replace"}:
            return "active"
        if entry.action == "waive":
            return "waived"
    return "unresolved"
