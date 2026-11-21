from pathlib import Path

import pytest

from backend.contracts import IssuedQuestion, RequirementEffect, RequirementTarget
from backend.intake import parse_listing
from backend.natural_answer import NaturalAnswerDecision, interpret_natural_answer, validate_natural_proposal
from backend.supplier_targets import DeliveryTarget, OrderTarget


@pytest.fixture(scope="module")
def variants():
    archive = Path(__file__).resolve().parents[2] / "samples/raw-save/raw-save-redacted.zip"
    snapshot = parse_listing(archive.read_bytes()).snapshot
    ids = {option.name_original: option.option_id for option in snapshot.options}
    names = ["暮色316-贴画黑白", "暮色316-贴画蓝白", "暮色316-贴画黑粉", "暮色316-字母蓝白"]
    return snapshot, [ids[name] for name in names]


def make_question(option_ids, *, topic="customization", facet="order_type", current="Ready-made stock only.",
                  prompt="What order terms work for these options?"):
    target = RequirementTarget(requirement_id="stock", statement=current, option_ids=option_ids) if current else None
    return IssuedQuestion(
        question_id="issued", prompt=prompt, why_ask="Order terms matter.", option_ids=option_ids,
        source_ids=[], choices=[], topic=topic, buyer_facet=facet,
        requirement_ids=["stock"] if target else [], requirement_targets=[target] if target else [],
    )


@pytest.mark.parametrize(("text", "indices", "direction"), [
    ("Actually, custom is fine for the blue cup.", [1], "custom_orders_acceptable"),
    ("Custom works for blue.", [1], "custom_orders_acceptable"),
    ("Custom works for Option B and Option C.", [1, 2], "custom_orders_acceptable"),
    ("For Option B, we can go custom.", [1], "custom_orders_acceptable"),
    ("Custom is not acceptable for Option B.", [1], "ready_made_only"),
])
def test_order_answers_apply_only_to_named_options(variants, text, indices, direction):
    snapshot, options = variants
    scope = options[:3]
    current = "Custom orders are acceptable." if direction == "ready_made_only" else "Ready-made stock only."
    decision = interpret_natural_answer(make_question(scope, current=current), text, snapshot, scope)
    assert decision.status == "decided"
    assert decision.effect == RequirementEffect(action="replace", requirement_id="stock")
    assert decision.option_ids == [scope[index] for index in indices]
    assert decision.supplier_target == OrderTarget(value=direction)


@pytest.mark.parametrize("text", [
    "Custom is fine for the blue cup.",
    "Custom is fine for Option D.",
    "Custom is fine for Option A except Option B.",
    "For Option A, custom is fine for Option B.",
    "Custom is fine?",
])
def test_ambiguous_or_mismatched_sku_does_not_reach_model(variants, text):
    snapshot, all_options = variants
    scope = [all_options[0], all_options[1], all_options[3]]
    decision = interpret_natural_answer(make_question(scope), text, snapshot, scope)
    assert decision.status == "clarify"
    assert decision.clarification
    assert decision.effect is None and not decision.may_use_model


def test_new_date_paraphrase_changes_only_the_scoped_requirement(variants):
    snapshot, options = variants
    scope = options[:3]
    question = make_question(scope, topic="deadline", facet="delivery_deadline",
                             current="The order must be delivered by 1 December.",
                             prompt="What is the latest arrival date?")
    updated = interpret_natural_answer(question, "15 December is fine for the blue cup.", snapshot, scope)
    assert updated.effect == RequirementEffect(action="replace", requirement_id="stock")
    assert updated.option_ids == [scope[1]]
    assert updated.supplier_target == DeliveryTarget(day=15, month=12, year=None)
    confirmed = interpret_natural_answer(question, "1 December is fine.", snapshot, scope)
    assert confirmed.effect == RequirementEffect(action="keep", requirement_id="stock")
    assert confirmed.supplier_target is None


def test_conditional_order_represents_a_fixed_date_or_a_link_to_the_deadline(variants):
    snapshot, options = variants
    scope = options[:3]
    question = make_question(scope)
    relative = interpret_natural_answer(
        question, "Custom is fine for the blue cup only if it still arrives by then.", snapshot, scope,
    )
    assert relative.option_ids == [scope[1]]
    assert relative.delivery_condition.follows_deadline
    assert relative.delivery_condition.latest_arrival is None
    assert relative.supplier_target is None
    fixed = interpret_natural_answer(
        question, "For Option B, custom is fine as long as it can be delivered by 15 December.", snapshot, scope,
    )
    assert fixed.option_ids == [scope[1]]
    assert not fixed.delivery_condition.follows_deadline
    assert fixed.delivery_condition.latest_arrival == DeliveryTarget(day=15, month=12, year=None)


def test_waiver_skip_and_contextual_affirmation_have_distinct_outcomes(variants):
    snapshot, options = variants
    scope = options[:3]
    question = make_question(scope, current="The order must be delivered by 1 December.",
                             topic="deadline", facet="delivery_deadline")
    waived = interpret_natural_answer(question, "No deadline for option B.", snapshot, scope)
    assert waived.effect == RequirementEffect(action="waive", requirement_id="stock")
    assert waived.option_ids == [scope[1]]
    for text in ("I don't know yet.", "I'm not sure.", "I’m not sure yet."):
        skipped = interpret_natural_answer(question, text, snapshot, scope)
        assert skipped == NaturalAnswerDecision("unrelated")
    unrelated_topic = interpret_natural_answer(question, "Any finish is fine.", snapshot, scope)
    assert unrelated_topic.status == "clarify" and unrelated_topic.effect is None

    custom_question = make_question(scope, current=None, prompt="Would you consider a custom order?")
    accepted = interpret_natural_answer(custom_question, "Yes", snapshot, scope)
    assert accepted.effect == RequirementEffect(action="add", requirement_id=None)
    assert accepted.supplier_target == OrderTarget(value="custom_orders_acceptable")

    uncertain = interpret_natural_answer(custom_question, "Maybe for one of the cups.", snapshot, scope)
    assert uncertain.status == "clarify"
    assert "Which option" in uncertain.clarification and "custom order" in uncertain.clarification
    unclear_date = interpret_natural_answer(question, "15 December works only for that one.", snapshot, scope)
    assert unclear_date.status == "clarify" and "Which options" in unclear_date.clarification

    sample_question = make_question(scope, current=None, topic="sample", facet=None,
                                    prompt="Would you need to approve a sample before production?")
    sample = interpret_natural_answer(sample_question, "Yes", snapshot, scope)
    assert sample.effect == RequirementEffect(action="add", requirement_id=None)
    assert sample.option_ids == scope
    alternative = sample_question.model_copy(update={"prompt": "Would you prefer a sample or a photograph?"})
    assert interpret_natural_answer(alternative, "Yes", snapshot, scope).status == "clarify"


def test_model_order_must_match_exact_scope_and_direction(variants):
    snapshot, options = variants
    scope = options[:3]
    question = make_question(scope)
    reply = "A bespoke production run works for us for the blue variant."
    assert interpret_natural_answer(question, reply, snapshot, scope).may_use_model

    accepted = NaturalAnswerDecision("decided", RequirementEffect(action="replace", requirement_id="stock"),
                                     [scope[1]], OrderTarget(value="custom_orders_acceptable"))
    assert validate_natural_proposal(question, reply, snapshot, scope, accepted) == accepted
    for invalid in [
        NaturalAnswerDecision("decided", accepted.effect, scope, accepted.supplier_target),
        NaturalAnswerDecision("decided", accepted.effect, [scope[1]], OrderTarget(value="ready_made_only")),
        NaturalAnswerDecision("decided", RequirementEffect(action="add", requirement_id=None),
                              [scope[1]], accepted.supplier_target),
        NaturalAnswerDecision("decided", RequirementEffect(action="replace", requirement_id="unissued"),
                              [scope[1]], accepted.supplier_target),
    ]:
        with pytest.raises(ValueError):
            validate_natural_proposal(question, reply, snapshot, scope, invalid)
    with pytest.raises(ValueError):
        validate_natural_proposal(question, "A bespoke production run does not work for us for Option B.",
                                  snapshot, scope, accepted)


def test_validated_model_can_return_a_focused_question_for_unrecognized_wording(variants):
    snapshot, options = variants
    scope = options[:3]
    question = make_question(scope, topic="finish", facet="surface_finish", current=None)
    reply = "Something subtle and not too shiny."
    requested = NaturalAnswerDecision("clarify", clarification="Would you prefer a matte surface for these options?")
    assert validate_natural_proposal(question, reply, snapshot, scope, requested) == requested
    with pytest.raises(ValueError):
        validate_natural_proposal(question, reply, snapshot, scope,
                                  NaturalAnswerDecision("clarify", clarification="Do this now. Is matte okay?"))
