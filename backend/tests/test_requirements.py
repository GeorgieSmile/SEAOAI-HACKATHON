from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.contracts import ContextRequirement, QuestionDraft, QuestionResult
from backend.main import create_app
from backend.report import assemble_review, review_sent_evidence
from backend.report_models import ReviewContent, ReviewReady
from backend.tests.offline_server import offline_review
from backend.tests.test_cases import initial_request, ready, reference, round_request
from backend.tests.test_imports import _upload, stub
from backend.tests.test_reports import finding_selection, minimal_content
from backend.tests.test_review_workflow import _replace_saved_case, retry_request


@pytest.mark.parametrize("buyer_context, excerpt", [
    ("It is false that Screen printing is required.", "Screen printing is required."),
    ("The order must be delivered by 1 December unless we change supplier.", "The order must be delivered by 1 December"),
])
def test_context_proposal_cannot_omit_qualifier(tmp_path, buyer_context, excerpt):
    async def extract(inputs):
        return QuestionResult(reason="Buyer supplied a decision.", questions=[], context_requirements=[ContextRequirement(
            topic="finish", statement=excerpt, quote=excerpt, option_ids=inputs.selected_option_ids,
        )])

    with TestClient(create_app(tmp_path / "case.db", translator=stub, question_generator=extract)) as client:
        snapshot = _upload(client).json()["snapshot"]
        response = client.post("/api/questions", json=initial_request(snapshot, buyer_context=buyer_context)).json()
        assert response["code"] == "invalid_questions"
        saved = client.get("/api/case").json()["case"]
        assert saved["buyer_context"] == buyer_context
        assert not saved.get("requirements")


def test_skip_preserves_scoped_requirement_but_ambiguous_text_reopens_it(tmp_path):
    context = "The logo must be laser engraved."
    explicit_choice = "The logo must be screen printed."

    async def ask(inputs):
        return QuestionResult(
            reason="Check the intended decoration.",
            context_requirements=[ContextRequirement(topic="finish", statement=context, quote=context, option_ids=inputs.selected_option_ids)],
            questions=[QuestionDraft.model_validate({
                "prompt": "What decoration method is required for the first item?",
                "why_ask": "Its ordered decoration matters to your purchase.",
                "topic": "finish", "buyer_facet": "decoration_method", "option_ids": inputs.selected_option_ids[:1],
                "source_ids": [], "choices": [explicit_choice], "requirement_ids": ["context:1"],
                "choice_effects": [{"choice": explicit_choice, "action": "replace", "statement": explicit_choice, "requirement_id": "context:1"}],
            })],
        )

    options = dict(translator=stub, question_generator=ask, review_generator=offline_review)
    database = tmp_path / "case.db"
    with TestClient(create_app(database, **options)) as client:
        snapshot = _upload(client).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=context)))
        requirement, = case["requirements"]
        question, = case["rounds"][0]["questions"]
        requirement_id = requirement["requirement_id"]
        assert question["requirement_ids"] == [requirement_id]
        assert question["requirement_targets"] == [{
            "requirement_id": requirement_id, "statement": context, "option_ids": [case["selected_option_ids"][0]],
        }]

        forged = round_request(case, texts=[explicit_choice])
        forged["submission"]["answers"][0]["requirement_effect"] = {"action": "keep", "requirement_id": requirement_id}
        assert client.post("/api/reviews", json=forged).status_code == 422
        forged = round_request(case, texts=["A different process."])
        forged["submission"]["answers"][0]["requirement_effect"] = {"action": "replace", "requirement_id": "context:1"}
        assert client.post("/api/reviews", json=forged).status_code == 422

        skipped = ready(client.post("/api/reviews", json=round_request(case)))
        assert skipped["requirements"] == [requirement]

        unsure = "We need to decide."
        opened = ready(client.post("/api/reviews", json=round_request(skipped, texts=[unsure])))
        retained, unresolved = opened["requirements"]
        assert retained["status"] == "active"
        assert retained["requirement_id"] == requirement_id
        assert retained["option_ids"] == case["selected_option_ids"][1:]
        assert unresolved["status"] == "unresolved"
        assert unresolved["supplier_target"] is None
        assert unresolved["option_ids"] == case["selected_option_ids"][:1]
        assert unresolved["provenance"][-1]["quote"] == unsure
        assert unresolved["provenance"][-1]["question_text"] == question["prompt"]
        assert opened["report"]["open_preferences"]

        selected = ready(client.post("/api/reviews", json=round_request(opened, texts=[explicit_choice])))
        retained, replaced = selected["requirements"]
        assert retained["requirement_id"] == requirement_id
        assert replaced["requirement_id"] == unresolved["requirement_id"]
        assert replaced["status"] == "active"
        assert replaced["statement"] == explicit_choice
        assert [entry["quote"] for entry in replaced["provenance"]] == [context, explicit_choice]

    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == selected


@pytest.mark.parametrize("source", ["empty", "context", "answer", "ambiguous"])
def test_legacy_review_only_reuses_decisions_with_direct_stored_provenance(tmp_path, source):
    database = tmp_path / "legacy.db"
    quote = "The logo must be laser engraved."
    buyer_context = quote if source == "context" else ""

    async def ask(inputs):
        questions = []
        if source in {"answer", "ambiguous"}:
            questions.append(QuestionDraft(
                topic=None, prompt="What artwork method applies?", why_ask="It determines the expected product.",
                option_ids=inputs.selected_option_ids[:1], source_ids=[], choices=[quote, "No."],
            ))
        return QuestionResult(reason="Check decisions affecting the order.", questions=questions)

    async def old_review(inputs, images):
        assert inputs.requirements is None
        content = minimal_content(inputs, topic="finish")
        if source in {"context", "answer"}:
            input_ref = "context" if source == "context" else f"answer:{inputs.answers[0].question_id}"
            selected = inputs.selected_option_ids if source == "context" else inputs.selected_option_ids[:1]
            content["buyer_requirements"] = [{"requirement_id": "legacy-id", "topic": "finish", "input_ref": input_ref, "quote": quote}]
            content["findings"] = [finding_selection(selected, topic="finish", requirement_ids=["legacy-id"])]
        evidence = review_sent_evidence(inputs, images)
        exact = ReviewContent.model_validate(content)
        return ReviewReady(report=assemble_review(inputs, exact, sent_evidence=evidence), content=exact, sent_evidence=evidence)

    with TestClient(create_app(database, translator=stub, question_generator=ask, review_generator=old_review)) as client:
        snapshot = _upload(client).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=buyer_context)))
        command = {**reference(case), "operation_id": str(uuid4()), "submission": None}
        if source in {"answer", "ambiguous"}:
            command = round_request(case, texts=[quote if source == "answer" else "No."])
        previous = ready(client.post("/api/reviews", json=command))

    _replace_saved_case(database, lambda record: record.pop("requirement_version"))

    async def forbidden(*args):
        pytest.fail("Startup must not call question or report generation.")

    with TestClient(create_app(database, translator=stub, question_generator=forbidden, review_generator=forbidden)) as client:
        reopened = client.get("/api/case").json()["case"]
        assert reference(reopened) == reference(previous)
        assert reopened["rounds"] == previous["rounds"]
        if source == "ambiguous":
            assert reopened["last_operation"]["code"] == "review_regeneration_required"
            assert reopened["report"] is None
            unresolved, = reopened["requirements"]
            assert unresolved["status"] == "unresolved"
            assert unresolved["provenance"][0]["quote"] == "No."
        else:
            assert reopened["phase"] == "reviewed"
            if source == "empty":
                assert reopened["requirements"] == []
            else:
                row, = reopened["requirements"]
                assert row["status"] == "active"
                assert row["statement"] == quote
                assert row["option_ids"] == previous["report"]["buyer_requirements"][0]["option_ids"]
                shown, = reopened["report"]["buyer_requirements"]
                assert shown["requirement_id"] == row["requirement_id"]
                assert reopened["report"]["findings"][0]["requirement_ids"] == [row["requirement_id"]]

    with TestClient(create_app(database, translator=stub, question_generator=forbidden, review_generator=offline_review)) as client:
        assert client.get("/api/case").json()["case"] == reopened
        if source == "ambiguous":
            regenerated = ready(client.post("/api/case/retry", json=retry_request(reopened)))
            assert regenerated["requirements"] == reopened["requirements"]
            assert regenerated["report"]["buyer_requirements"] == []
            assert regenerated["report"]["open_preferences"]
        if source == "answer":
            changed = ready(client.post("/api/reviews", json=round_request(reopened, texts=["No."])))
            assert changed["requirements"][0]["status"] == "unresolved"
            assert changed["report"]["buyer_requirements"] == []
