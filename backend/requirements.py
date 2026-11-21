from collections.abc import Iterator
import re
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from .contracts import (
    Answer, BuyerFacet, IssuedQuestion, IssuedRound, QuestionInput, QuestionResult, ReviewTopic,
    RequirementEffect, RequirementProvenance, RequirementStatus, RequirementTarget,
    ResolvedRequirement,
)
from .supplier_targets import OrderTarget, supplier_target_from_answer, validate_supplier_target


RequirementProjection = Literal["current", "before_review_fixes", "before_order_conflicts"]

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


def _with_supplier_target(row: ResolvedRequirement) -> ResolvedRequirement:
    if row.status != "active" or row.supplier_target is not None:
        return row
    target = supplier_target_from_answer(row.statement, row.topic, row.buyer_facet)
    return row.model_copy(update={"supplier_target": target}) if target is not None else row


def _context_rows(inputs: QuestionInput, result: QuestionResult) -> list[ResolvedRequirement]:
    rows = []
    seen = set()
    for index, seed in enumerate(result.context_requirements or [], start=1):
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
        if seed.supplier_target is not None:
            if seed.status != "active":
                raise ValueError("Unresolved context cannot supply a supplier target.")
            validate_supplier_target(seed.supplier_target, seed.quote, seed.topic, seed.buyer_facet)
        row = _with_supplier_target(ResolvedRequirement(
            requirement_id=f"context:{index}", statement=seed.statement, option_ids=seed.option_ids,
            status=seed.status, topic=seed.topic, buyer_facet=seed.buyer_facet, supplier_target=seed.supplier_target,
            provenance=[RequirementProvenance(
                input_ref="context", quote=seed.quote, question_text=None,
                action="add" if seed.status == "active" else "unresolved", option_ids=seed.option_ids,
                context_start=start,
            )],
        ))
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
            if target.buyer_facet not in {None, "other"} and question.buyer_facet not in {None, target.buyer_facet}:
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
            if effect.supplier_target is not None:
                if effect.statement is None:
                    raise ValueError("Only an add or replacement choice can declare a new supplier target.")
                validate_supplier_target(effect.supplier_target, effect.choice, question.topic, question.buyer_facet)


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


def validate_submission_effect(question: IssuedQuestion, answer: Answer) -> None:
    effect = answer.requirement_effect
    if effect is None:
        return
    if question.requirement_ids is None:
        raise ValueError("This older question did not issue structured requirement effects.")
    if answer.answer_text in question.choices:
        raise ValueError("Submit the issued choice without a separate requirement effect.")
    _validate_effect_target(effect, question.requirement_ids)


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
    case_id: str, rows: dict[str, ResolvedRequirement],
) -> dict[str, tuple[ResolvedRequirement, tuple[str, ...]]]:
    by_option: dict[str, list[tuple[str, OrderTarget]]] = {}
    for row in rows.values():
        target = _with_supplier_target(row).supplier_target
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
            provenance=[entry for source in sources for entry in rows[source].provenance],
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


def resolve_requirements(
    case_id: str, initial: list[ResolvedRequirement], rounds: list[IssuedRound], *, buyer_context: str,
    projection: RequirementProjection = "current",
) -> list[ResolvedRequirement]:
    rows = {row.requirement_id: row for row in initial if row.provenance[0].input_ref == "context"}
    for row in rows.values():
        if row.status == "active":
            origin = row.provenance[0]
            if origin.quote != row.statement or origin.context_start != _quote_start(buyer_context, row.statement):
                raise ValueError("A stored context requirement must match its original buyer statement.")
    migrated_answers = {row.provenance[0].input_ref: row for row in initial if row.provenance[0].input_ref.startswith("answer:")}
    conflict_forks: dict[str, dict[str, str]] = {}
    for issued_round in rounds:
        if issued_round.answers is None:
            break
        order_conflicts = _order_conflicts(case_id, rows)
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
            effect = choice or answer.requirement_effect
            action = effect.action if effect is not None else "unresolved"
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
                rows[requirement_id] = ResolvedRequirement(
                    requirement_id=requirement_id, statement=answer.answer_text or question.prompt,
                    option_ids=question.option_ids, status=status,
                    topic=topic, buyer_facet=question.buyer_facet, provenance=[provenance],
                    supplier_target=supplier_target,
                )
                continue
            for target_id, target_scope in _scoped_targets(target_ids, question.option_ids, conflict_forks):
                if conflict := order_conflicts.get(target_id):
                    if projection != "current" and action not in {"replace", "waive"}:
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
                    rows[resolved_id] = decision.model_copy(update={
                        "requirement_id": resolved_id, "option_ids": target_scope,
                        "statement": answer.answer_text if action == "replace" else decision.statement,
                        "status": status,
                        "supplier_target": supplier_target,
                        "provenance": [*decision.provenance, provenance],
                    })
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
                    "buyer_facet": question.buyer_facet if action == "replace" else row.buyer_facet,
                    "supplier_target": retained_target if status == "active" else None,
                    "provenance": [*row.provenance, provenance],
                })
                if remaining:
                    fork_id = _derived_id(case_id, target_id, question.question_id)
                    rows[target_id] = row.model_copy(update={"option_ids": remaining})
                    rows[fork_id] = updated.model_copy(update={"requirement_id": fork_id})
                else:
                    rows[target_id] = updated
    if projection != "before_order_conflicts":
        # Keep the original decisions for replay. Expose one unresolved decision
        # for each overlapping scope, with both source statements attached.
        for conflict, sources in _order_conflicts(case_id, rows).values():
            _remove_conflicting_scope(rows, sources, conflict.option_ids)
            rows[conflict.requirement_id] = conflict
    resolved = {key: _with_supplier_target(row) for key, row in rows.items()}
    # Keep suppressed rows addressable by already-issued follow-ups during replay.
    return _visible_requirements(resolved) if projection == "current" else list(resolved.values())


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
