from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend import case_workflow, requirements
from backend.case_models import CaseView, SavedReview
from backend.case_workflow import _review_digest, _review_input
from backend.contracts import ContextRequirement, ListingSnapshot, QuestionDraft, QuestionResult
from backend.main import create_app
from backend.report import assemble_review, review_sent_evidence
from backend.report_models import CanonicalReviewContent, ReviewContent, ReviewReady
from backend.tests.offline_server import offline_review
from backend.tests.test_cases import confirm_command, finish_review, initial_request, ready, reference, reopen_brief, round_request
from backend.tests.test_imports import RAW, _image_url, _upload, stub
from backend.tests.test_reports import canonical_content, finding_selection, minimal_content
from backend.tests.test_review_workflow import _replace_saved_case, retry_request


def _install_archived_report(database, case, inputs, images, content, *, version):
    evidence = review_sent_evidence(inputs, images)
    report = assemble_review(
        inputs, content, sent_evidence=evidence, historical_decision_labels=version == 6,
        historical_supplier_questions=True,
    ).model_dump(mode="json")
    digest = _review_digest(inputs, case["last_operation"]["operation_id"], images, evidence, version=version)
    saved = SavedReview(version=version, input_digest=digest, content=content, sent_evidence=evidence)
    archive = {**case, "report": report}
    if version < 5:
        archive["phase"] = "reviewed"
        archive["last_operation"] = {**case["last_operation"], "stage": "review"}
        archive.pop("requirements")
        archive.pop("confirmed_revision", None)

    def replace(record):
        record.pop("confirmed_review_digest", None)
        if version < 5:
            record.pop("requirement_version")
            record.pop("initial_requirements")
            record.pop("order_conflict_version")
        record["view"] = archive
        record["saved_review"] = saved.model_dump(mode="json")

    _replace_saved_case(database, replace)
    return archive


def _save_raw_report_fixture(client, database, snapshot, case, content_for):
    legacy_view = CaseView.model_validate(case).model_copy(update={"requirements": None})
    inputs = _review_input(ListingSnapshot.model_validate(snapshot), legacy_view)
    images = {}
    for option in snapshot["options"]:
        if option["option_id"] in inputs.selected_option_ids and option["image_source_id"] is not None:
            response = client.get(_image_url(snapshot, option))
            assert response.status_code == 200
            images[option["image_source_id"]] = (response.headers["content-type"], response.content)
    return _install_archived_report(database, case, inputs, images, content_for(inputs), version=4)


@pytest.mark.parametrize(("source", "topic", "facet", "accepted"), [
    ("question", "sample", "order_type", False),
    ("question", "other", "order_type", False),
    ("context", "sample", "order_type", False),
    ("context", "sample", "other", True),
    ("question", "sample", None, True),
    ("question", "customization", "order_type", True),
])
def test_issue_only_matching_known_buyer_facets(tmp_path, source, topic, facet, accepted):
    prompt = "What should we confirm before the order?"
    excerpt = "I still need to decide."

    async def ask(inputs):
        context_rows = []
        questions = []
        if source == "context":
            context_rows.append(ContextRequirement(
                topic=topic, buyer_facet=facet, status="unresolved", statement=excerpt, quote=excerpt,
                option_ids=inputs.selected_option_ids,
            ))
        else:
            questions.append(QuestionDraft(
                prompt=prompt, why_ask="The answer will affect what we request.", topic=topic, buyer_facet=facet,
                option_ids=inputs.selected_option_ids, source_ids=[], choices=[], requirement_ids=[], choice_effects=[],
            ))
        return QuestionResult(reason="Clarify what would change the order.", questions=questions, context_requirements=context_rows)

    with TestClient(create_app(tmp_path / "case.db", translator=stub, question_generator=ask)) as client:
        snapshot = _upload(client).json()["snapshot"]
        context = excerpt if source == "context" else ""
        response = client.post("/api/questions", json=initial_request(snapshot, buyer_context=context))
        if accepted:
            case = ready(response)
            assert case["phase"] == ("ready_for_review" if source == "context" else "awaiting_answers")
        else:
            assert response.json()["code"] == "invalid_questions"
            case = client.get("/api/case").json()["case"]
            assert case["buyer_context"] == context
            assert not case["rounds"] and not case.get("requirements")


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
        assert finish_review(client, forged).status_code == 422
        forged = round_request(case, texts=["A different process."])
        forged["submission"]["answers"][0]["requirement_effect"] = {"action": "replace", "requirement_id": "context:1"}
        assert finish_review(client, forged).status_code == 422

        skipped = ready(finish_review(client, round_request(case)))
        assert skipped["requirements"] == [requirement]

        unsure = "We need to decide."
        opened = ready(finish_review(client, round_request(skipped, texts=[unsure])))
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

        selected = ready(finish_review(client, round_request(opened, texts=[explicit_choice])))
        retained, replaced = selected["requirements"]
        assert retained["requirement_id"] == requirement_id
        assert replaced["requirement_id"] == unresolved["requirement_id"]
        assert replaced["status"] == "active"
        assert replaced["statement"] == explicit_choice
        assert replaced["supplier_target"] == {
            "kind": "decoration_method", "method": "screen_printing", "intent": "require",
        }
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

    def old_content(inputs):
        assert inputs.requirements is None
        content = minimal_content(inputs, topic="finish")
        if source in {"context", "answer"}:
            input_ref = "context" if source == "context" else f"answer:{inputs.answers[0].question_id}"
            selected = inputs.selected_option_ids if source == "context" else inputs.selected_option_ids[:1]
            content["buyer_requirements"] = [{"requirement_id": "legacy-id", "topic": "finish", "input_ref": input_ref, "quote": quote}]
            content["findings"] = [finding_selection(selected, topic="finish", requirement_ids=["legacy-id"])]
        return ReviewContent.model_validate(content)

    with TestClient(create_app(database, translator=stub, question_generator=ask)) as client:
        snapshot = _upload(client).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=buyer_context)))
        command = {**reference(case), "operation_id": str(uuid4()), "submission": None}
        if source in {"answer", "ambiguous"}:
            command = round_request(case, texts=[quote if source == "answer" else "No."])
        brief = ready(client.post("/api/brief", json=command))
        previous = _save_raw_report_fixture(client, database, snapshot, brief, old_content)

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
            regenerated = ready(client.post("/api/reviews", json=confirm_command(reopen_brief(client, reopened))))
            assert regenerated["requirements"] == reopened["requirements"]
            assert regenerated["report"]["buyer_requirements"] == []
            assert regenerated["report"]["open_preferences"]
        if source == "answer":
            changed = ready(finish_review(client, round_request(reopened, texts=["No."])))
            assert changed["requirements"][0]["status"] == "unresolved"
            assert changed["report"]["buyer_requirements"] == []


@pytest.mark.parametrize(("case_type", "active", "keeps_review"), [
    ("independent", ["Screen printing is required.", "A matte finish is required."], True),
    ("different-sku", ["Screen printing is required.", "The logo must be laser engraved."], True),
    ("same-facet", [], False),
    ("indirect-overlap", [], False),
    ("independent-unknown", ["Screen printing is required."], False),
])
def test_legacy_migration_preserves_independent_direct_decisions(tmp_path, case_type, active, keeps_review):
    database = tmp_path / "legacy.db"
    first_quote = "Screen printing is required."
    second_quote, second_facet = {
        "independent": ("A matte finish is required.", "surface_finish"),
        "different-sku": ("The logo must be laser engraved.", "decoration_method"),
        "same-facet": ("The logo must be laser engraved.", "decoration_method"),
        "indirect-overlap": ("No.", None),
        "independent-unknown": ("No preference on texture yet.", "surface_finish"),
    }[case_type]

    async def ask(inputs):
        return QuestionResult(reason="Check the intended finish.", questions=[
            QuestionDraft(
                prompt="How should the artwork be applied?", why_ask="It determines the expected decoration.",
                buyer_facet="decoration_method", option_ids=inputs.selected_option_ids[:1],
                source_ids=[], choices=[first_quote],
            ),
            QuestionDraft(
                prompt="Which surface texture do you need?" if second_facet == "surface_finish" else "Does the order need a different decoration?",
                why_ask="This affects what to request from the supplier.", buyer_facet=second_facet,
                option_ids=inputs.selected_option_ids[1:2] if case_type == "different-sku" else inputs.selected_option_ids[:1],
                source_ids=[], choices=[second_quote],
            ),
        ])

    def old_content(inputs):
        accepted = {"same-facet": (1,), "indirect-overlap": (0,), "independent-unknown": (0,)}.get(case_type, (0, 1))
        content = minimal_content(inputs, topic="finish")
        for index in accepted:
            answer = inputs.answers[index]
            old_id = f"legacy-{index}"
            content["buyer_requirements"].append({
                "requirement_id": old_id, "topic": "finish", "input_ref": f"answer:{answer.question_id}",
                "quote": answer.answer_text,
            })
            content["findings"].append(finding_selection(
                inputs.answer_option_ids[answer.question_id], finding_id=f"finish-{index}",
                topic="finish", requirement_ids=[old_id],
            ))
        return ReviewContent.model_validate(content)

    with TestClient(create_app(database, translator=stub, question_generator=ask)) as client:
        snapshot = _upload(client).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot)))
        brief = ready(client.post("/api/brief", json=round_request(case, texts=[first_quote, second_quote])))
        old = _save_raw_report_fixture(client, database, snapshot, brief, old_content)

    async def forbidden(*args):
        pytest.fail("Startup must not call a provider.")

    with TestClient(create_app(database, translator=stub, question_generator=forbidden, review_generator=forbidden)) as client:
        recovered = client.get("/api/case").json()["case"]
        assert recovered["rounds"] == old["rounds"]
        assert [row["statement"] for row in recovered["requirements"] if row["status"] == "active"] == active
        if keeps_review:
            assert recovered["phase"] == "reviewed"
            assert {finding["requirement_ids"][0] for finding in recovered["report"]["findings"]} == {
                row["requirement_id"] for row in recovered["requirements"]
            }
        else:
            assert recovered["last_operation"]["code"] == "review_regeneration_required"
            assert recovered["report"] is None
            assert any(row["status"] == "unresolved" for row in recovered["requirements"])

    if not keeps_review:
        with TestClient(create_app(database, translator=stub, question_generator=forbidden, review_generator=offline_review)) as client:
            regenerated = ready(client.post("/api/reviews", json=confirm_command(reopen_brief(client, recovered))))
            assert [row["quote"] for row in regenerated["report"]["buyer_requirements"]] == active
            assert regenerated["report"]["open_preferences"]


@pytest.mark.parametrize("had_review", [True, False])
def test_expanded_supplier_wording_preserves_saved_answer(tmp_path, monkeypatch, had_review):
    database = tmp_path / "case.db"
    quote = "The logo needs to be laser engraved."
    infer = requirements.supplier_target_from_answer

    async def ask(inputs):
        if inputs.round_number > 1:
            return QuestionResult(reason="There are no remaining questions.", questions=[])
        return QuestionResult(reason="Check the intended decoration.", context_requirements=[], questions=[QuestionDraft.model_validate({
            "prompt": "How should the artwork be applied?", "why_ask": "The method affects the expected result.",
            "topic": "finish", "buyer_facet": "decoration_method", "option_ids": inputs.selected_option_ids,
            "source_ids": [], "choices": [quote], "requirement_ids": [],
            "choice_effects": [{"choice": quote, "action": "add", "statement": quote, "requirement_id": None}],
        })])

    def old_infer(answer, topic, facet):
        return None if answer == quote else infer(answer, topic, facet)

    options = dict(translator=stub, question_generator=ask, review_generator=offline_review)
    with monkeypatch.context() as patch:
        patch.setattr(requirements, "supplier_target_from_answer", old_infer)
        with TestClient(create_app(database, **options)) as client:
            snapshot = _upload(client).json()["snapshot"]
            case = ready(client.post("/api/questions", json=initial_request(snapshot)))
            endpoint = "/api/reviews" if had_review else "/api/questions/continue"
            previous = ready(finish_review(client, round_request(case, texts=[quote])) if endpoint == "/api/reviews" else client.post(endpoint, json=round_request(case, texts=[quote])))
            assert previous["requirements"][0]["supplier_target"] is None

    async def forbidden(*args):
        pytest.fail("Recovery must not call a provider.")

    with TestClient(create_app(database, **{**options, "question_generator": forbidden, "review_generator": forbidden})) as client:
        recovered = client.get("/api/case").json()["case"]
        if had_review:
            assert recovered["last_operation"]["code"] == "review_regeneration_required"
        else:
            assert recovered["phase"] == "ready_for_review"
            assert recovered["last_operation"] == previous["last_operation"]
        assert recovered["report"] is None
        assert recovered["rounds"] == previous["rounds"]
        current, = recovered["requirements"]
        assert current["statement"] == quote
        assert current["requirement_id"] == previous["requirements"][0]["requirement_id"]
        assert current["supplier_target"] == {"kind": "decoration_method", "method": "laser_engraving", "intent": "require"}

    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == recovered
        if had_review:
            regenerated = ready(client.post("/api/reviews", json=confirm_command(reopen_brief(client, recovered))))
        else:
            command = {**reference(recovered), "operation_id": str(uuid4()), "submission": None}
            regenerated = ready(finish_review(client, command))
        assert regenerated["requirements"] == recovered["requirements"]
        assert "laser engraving" in regenerated["report"]["supplier_draft"]


@pytest.mark.parametrize("state", ["reviewed", "unfinished", "damaged-review", "qualified-context"])
def test_reopening_replays_initial_context_without_reinterpreting_texture(tmp_path, monkeypatch, state):
    database = tmp_path / "case.db"
    quote = "The logo needs to be laser engraved."
    if state == "qualified-context":
        quote = "The logo needs to be laser engraved if our customer approves it."
    context = f"  {quote}  "
    any_texture = "Any surface texture is acceptable."
    infer = requirements.supplier_target_from_answer

    async def ask(inputs):
        if inputs.round_number > 1:
            return QuestionResult(reason="There are no remaining questions.", questions=[])
        return QuestionResult(
            reason="Check whether the buyer has a surface preference.",
            context_requirements=[ContextRequirement(
                statement=quote, quote=quote, topic="finish", option_ids=inputs.selected_option_ids,
            )],
            questions=[QuestionDraft.model_validate({
                "prompt": "Which surface textures work?", "why_ask": "The texture can vary between items.",
                "topic": "finish", "buyer_facet": "surface_finish", "option_ids": inputs.selected_option_ids,
                "source_ids": [], "choices": [any_texture], "requirement_ids": [],
                "choice_effects": [{"choice": any_texture, "action": "waive", "statement": None, "requirement_id": None}],
            })],
        )

    prior_inputs = []

    async def capture_review(inputs, images):
        prior_inputs.append((inputs, images))
        return await offline_review(inputs, images)

    options = dict(translator=stub, question_generator=ask, review_generator=offline_review)
    with monkeypatch.context() as patch:
        patch.setattr(requirements, "supplier_target_from_answer",
                      lambda text, topic, facet: None if text == quote else infer(text, topic, facet))
        with TestClient(create_app(database, **{**options, "review_generator": capture_review})) as client:
            snapshot = _upload(client, RAW).json()["snapshot"]
            start = initial_request(snapshot, buyer_context=context)
            start["selected_option_ids"].reverse()
            case = ready(client.post("/api/questions", json=start))
            issue = round_request(case, texts=[any_texture])
            endpoint = "/api/questions/continue" if state == "unfinished" else "/api/reviews"
            previous = ready(finish_review(client, issue) if endpoint == "/api/reviews" else client.post(endpoint, json=issue))
            assert previous["requirements"][0]["supplier_target"] is None
            image_url = _image_url(snapshot, snapshot["options"][2])
            image = client.get(image_url)
            assert image.status_code == 200
            original_image = image.content

    if state != "unfinished":
        (inputs, images), = prior_inputs
        content = ReviewContent.model_validate(minimal_content(inputs, topic="finish"))
        previous = _install_archived_report(database, previous, inputs, images, content, version=5)
        assert "laser engraving" not in previous["report"]["supplier_draft"]

    if state == "damaged-review":
        _replace_saved_case(database, lambda record: record["saved_review"].update(input_digest="0" * 64))

    async def forbidden(*args):
        pytest.fail("Recovery must not call a question or review generator.")

    recovery_options = {**options, "question_generator": forbidden, "review_generator": forbidden}
    expected = previous["requirements"]
    if state != "qualified-context":
        expected = [
            {**expected[0], "supplier_target": {
                "kind": "decoration_method", "method": "laser_engraving", "intent": "require",
            }},
            expected[1],
        ]
    for _ in range(2):
        with TestClient(create_app(database, **recovery_options)) as client:
            workspace = client.get("/api/case").json()
            assert workspace["snapshot"] == snapshot
            assert client.get(image_url).content == original_image
            recovered = workspace["case"]
            for field in ("case_id", "revision", "snapshot_id", "selected_option_ids", "buyer_context", "rounds"):
                assert recovered[field] == previous[field]
            assert recovered["requirements"] == expected
            if state == "qualified-context":
                assert recovered == previous
            elif state == "unfinished":
                assert recovered["phase"] == "ready_for_review"
                assert recovered["last_operation"] == previous["last_operation"]
            else:
                assert recovered["report"] is None
                code = "invalid_review" if state == "damaged-review" else "review_regeneration_required"
                assert recovered["last_operation"]["code"] == code
                result = client.post("/api/reviews", json=confirm_command(recovered))
                assert result.status_code == 422

    if state == "qualified-context":
        return
    requests = []

    async def review(inputs, images):
        requests.append(inputs)
        return await offline_review(inputs, images)

    with TestClient(create_app(database, **{**recovery_options, "review_generator": review})) as client:
        command = confirm_command(reopen_brief(client, recovered))
        reviewed = ready(client.post("/api/reviews", json=command))
        assert ready(client.post("/api/reviews", json=command)) == reviewed
        assert len(requests) == 1
        assert reviewed["requirements"] == expected
        assert reviewed["report"]["buyer_requirements"][0]["quote"] == quote
        draft = reviewed["report"]["supplier_draft"]
        assert "laser engraving" in draft
        assert "matte finish" not in draft and "glossy finish" not in draft

    with TestClient(create_app(database, **recovery_options)) as client:
        assert client.get("/api/case").json()["case"] == reviewed


@pytest.mark.parametrize("invented_preference", [False, True])
def test_old_report_is_kept_only_if_its_projection_follows_the_saved_decisions(tmp_path, invented_preference):
    database = tmp_path / "case.db"
    deadline = "The order must be delivered by 1 December."
    prompt = "Do you need a pre-production sample?"

    async def ask(inputs):
        return QuestionResult(
            reason="Check the need for a sample.",
            context_requirements=[ContextRequirement(
                statement=deadline, quote=deadline, topic="deadline", option_ids=inputs.selected_option_ids,
            )],
            questions=[QuestionDraft(
                prompt=prompt, why_ask="Sample approval may affect timing.", topic="sample", buyer_facet="other",
                option_ids=inputs.selected_option_ids[:1], source_ids=[], choices=[], requirement_ids=[], choice_effects=[],
            )],
        )

    prior_reports = []

    async def incompatible_review(inputs, images):
        if prior_reports:
            return await offline_review(inputs, images)
        values = minimal_content(inputs)
        if invented_preference:
            values["open_preferences"] = [{
                "preference_id": "extra", "topic": "deadline",
                "input_refs": [f"answer:{inputs.answers[0].question_id}"], "finding_ids": [],
            }]
        content = ReviewContent.model_validate(values)
        prior_reports.append((inputs, images, content))
        evidence = review_sent_evidence(inputs, images)
        return ReviewReady(report=assemble_review(inputs, content, sent_evidence=evidence),
                           content=content, sent_evidence=evidence)

    with TestClient(create_app(database, translator=stub, question_generator=ask,
                               review_generator=incompatible_review)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        asking = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=deadline)))
        rejected = finish_review(client, round_request(asking)).json()
        assert rejected["code"] == "invalid_review"
        failed = client.get("/api/case").json()["case"]
        assert failed["report"] is None
        reviewed = ready(client.post("/api/case/retry", json=retry_request(failed)))
    (inputs, images, content), = prior_reports
    old = _install_archived_report(database, reviewed, inputs, images, content, version=5)
    assert len(old["report"]["open_preferences"]) == (2 if invented_preference else 1)

    async def forbidden(*args):
        pytest.fail("Opening a saved case must not ask for questions or a review.")

    for _ in range(2):
        with TestClient(create_app(database, translator=stub, question_generator=forbidden,
                                   review_generator=forbidden)) as client:
            reopened = client.get("/api/case").json()["case"]
            assert reopened["requirements"] == old["requirements"]
            assert reopened["rounds"] == old["rounds"]
            if invented_preference:
                assert reopened["report"] is None
                assert reopened["last_operation"]["code"] == "review_regeneration_required"
            else:
                assert reopened == old

    if invented_preference:
        with TestClient(create_app(database, translator=stub, question_generator=forbidden,
                                   review_generator=offline_review)) as client:
            rebuilt = ready(client.post("/api/reviews", json=confirm_command(reopen_brief(client, reopened))))
            sample, = rebuilt["report"]["open_preferences"]
            assert sample["text"] == "Your preference for samples is still open."
            assert sample["option_ids"] == rebuilt["selected_option_ids"][:1]
            assert "1 December" in rebuilt["report"]["supplier_draft"]


@pytest.mark.parametrize(("facet", "damaged", "expected_code"), [
    ("other", False, None),
    ("order_type", False, "review_regeneration_required"),
    ("order_type", True, "invalid_review"),
])
def test_v6_recovery_verifies_original_labels_before_requesting_regeneration(
    tmp_path, monkeypatch, facet, damaged, expected_code,
):
    database = tmp_path / "case.db"
    prompt = "Do you need a pre-production sample?"
    stock = "Ready-made stock only."

    async def ask(inputs):
        prior = next((row.requirement_id for row in inputs.requirements or [] if row.topic == "sample"), None)
        return QuestionResult(
            reason="Check the sample separately from the buyer's other decisions.",
            context_requirements=None if prior else [ContextRequirement(
                statement=stock, quote=stock, topic="customization", buyer_facet="order_type", option_ids=inputs.selected_option_ids,
            )],
            questions=[QuestionDraft(
                prompt="Do you need to approve a sample before ordering?" if prior else prompt,
                why_ask="Sample approval may affect timing.", topic="sample", buyer_facet="other" if prior else facet,
                option_ids=inputs.selected_option_ids[:1], source_ids=[], choices=[],
                requirement_ids=[prior] if prior else [], choice_effects=[],
            )],
        )

    calls = []

    async def review(inputs, images):
        values = canonical_content(inputs)
        row, = [row for row in inputs.requirements if row.status == "unresolved"]
        values["findings"] = [finding_selection(
            row.option_ids, topic="sample", finding_id="sample-choice", action_type="decide_preference",
            supplier_action=None, decision_id=row.requirement_id,
        )]
        content = CanonicalReviewContent.model_validate(values)
        evidence = review_sent_evidence(inputs, images)
        calls.append((inputs, images, content))
        return ReviewReady(report=assemble_review(inputs, content, sent_evidence=evidence),
                           content=content, sent_evidence=evidence)

    app_options = dict(translator=stub, question_generator=ask, review_generator=review)
    with monkeypatch.context() as patch:
        patch.setattr(requirements, "_validate_topic_facet", lambda topic, facet: None)
        with TestClient(create_app(database, **app_options)) as client:
            snapshot = _upload(client, RAW).json()["snapshot"]
            asking = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=stock)))
            reviewed = ready(finish_review(client, round_request(asking)))
    (inputs, images, content), = calls
    old = _install_archived_report(database, reviewed, inputs, images, content, version=6)
    subject = "stock and customization" if facet == "order_type" else "samples"
    assert old["report"]["open_preferences"][0]["text"] == f"Your preference for {subject} is still open."
    if damaged:
        def change_label(record):
            record["view"]["report"]["open_preferences"][0]["text"] = "The stored report was edited directly."

        _replace_saved_case(database, change_label)

    async def forbidden(*args):
        pytest.fail("Opening a saved case must not ask for questions or a review.")

    restore_options = {**app_options, "question_generator": forbidden, "review_generator": forbidden}
    for _ in range(2):
        with TestClient(create_app(database, **restore_options)) as client:
            reopened = client.get("/api/case").json()["case"]
            for field in ("case_id", "revision", "buyer_context", "rounds", "requirements", "selected_option_ids"):
                assert reopened[field] == old[field]
            if expected_code:
                assert reopened["last_operation"]["code"] == expected_code
                assert reopened["report"] is None
            else:
                assert reopened == old

    if expected_code == "review_regeneration_required":
        with TestClient(create_app(database, **{**restore_options, "review_generator": review})) as client:
            rebuilt = ready(client.post("/api/reviews", json=confirm_command(reopen_brief(client, reopened))))
            sample, = rebuilt["report"]["open_preferences"]
            assert sample["text"] == "Your preference for samples is still open."
            assert sample["finding_ids"] == ["sample-choice"]
            assert sample["questions"][0]["question_text"] == prompt
            assert len(calls) == 2
        with TestClient(create_app(database, **restore_options)) as client:
            assert client.get("/api/case").json()["case"] == rebuilt
        with TestClient(create_app(database, **{**app_options, "review_generator": forbidden})) as client:
            revised = ready(client.post("/api/questions/continue", json=round_request(rebuilt, texts=["Still deciding."])))
            follow_up, = revised["rounds"][-1]["questions"]
            assert follow_up["topic"] == "sample"
            assert follow_up["buyer_facet"] == "other"
            assert follow_up["requirement_ids"] == [inputs.requirements[-1].requirement_id]


def test_targeted_add_conflict_can_be_resolved_then_edited_or_waived(tmp_path):
    stock = "Ready-made stock only."
    custom = "Custom orders are acceptable."
    matte = "A matte finish is required."
    prompt = "Is the first option still restricted to stock?"

    async def ask(inputs):
        return QuestionResult(
            reason="Confirm the order type for the first selected option.",
            context_requirements=[
                ContextRequirement(statement=quote, quote=quote, topic=topic, option_ids=inputs.selected_option_ids)
                for quote, topic in [(stock, "customization"), (matte, "finish")]
            ],
            questions=[QuestionDraft(
                prompt=prompt, why_ask="The first option may have different order terms.",
                topic="customization", buyer_facet="order_type", option_ids=inputs.selected_option_ids[:1],
                source_ids=[], choices=[], requirement_ids=["context:1"], choice_effects=[],
            )],
        )

    with TestClient(create_app(tmp_path / "case.db", translator=stub, question_generator=ask,
                               review_generator=offline_review)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        initial = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=f"{stock} {matte}")))
        first, *others = initial["selected_option_ids"]
        stock_id, = initial["rounds"][0]["questions"][0]["requirement_ids"]

        def edit(case, answer, action, target):
            command = round_request(case, texts=[answer])
            command["submission"]["answers"][0]["requirement_effect"] = {"action": action, "requirement_id": target}
            return ready(finish_review(client, command))

        open_case = edit(initial, custom, "add", None)
        conflict, = [row for row in open_case["requirements"] if row.get("order_conflict")]
        assert conflict["option_ids"] == [first]
        assert [(entry["quote"], entry["question_text"]) for entry in conflict["provenance"]] == [(stock, None), (custom, prompt)]
        command = {
            **reference(open_case), "operation_id": str(uuid4()), "submission": None,
            "order_clarification": {
                "requirement_id": conflict["requirement_id"], "option_id": first, "choice": "custom_orders_acceptable",
            },
        }
        wrong_option = {**command, "order_clarification": {**command["order_clarification"], "option_id": others[0]}}
        assert finish_review(client, wrong_option).status_code == 422
        clarified = ready(finish_review(client, command))
        assert clarified["rounds"] == open_case["rounds"]
        assert clarified["report"]["open_preferences"] == []
        resolved, = [row for row in clarified["requirements"] if row["option_ids"] == [first]]
        assert resolved["status"] == "active"
        assert [(row["quote"], row["option_ids"]) for row in resolved["provenance"]] == [
            (stock, [first, *others]), (custom, [first]), ("Custom orders are acceptable", [first]),
        ]
        assert resolved["provenance"][-1]["input_ref"].startswith("clarification:")

        replaced = edit(clarified, custom, "replace", stock_id)
        assert not replaced.get("order_clarifications")
        assert replaced["report"]["open_preferences"] == []
        assert [(row["quote"], row["option_ids"]) for row in replaced["report"]["buyer_requirements"]] == [
            (stock, others), (matte, [first, *others]), (custom, [first]),
        ]
        waived = edit(replaced, "No order type is required for this option.", "waive", stock_id)
        assert not waived.get("order_clarifications")
        assert waived["report"]["open_preferences"] == []
        assert [(row["quote"], row["option_ids"]) for row in waived["report"]["buyer_requirements"]] == [
            (stock, others), (matte, [first, *others]),
        ]


@pytest.mark.parametrize(("had_review", "scoped"), [(False, False), (True, False), (True, True)])
def test_saved_order_conflict_replays_without_automatic_generation(tmp_path, monkeypatch, had_review, scoped):
    database = tmp_path / "case.db"
    stock = "Ready-made stock only."
    custom = "For Option A, custom orders are acceptable." if scoped else "Custom orders are acceptable."

    async def ask(inputs):
        return QuestionResult(
            reason="The buyer provided two order preferences.", questions=[],
            context_requirements=[
                ContextRequirement(
                    statement=quote, quote=quote, topic="customization", buyer_facet="order_type", option_ids=scope,
                )
                for quote, scope in [(stock, inputs.selected_option_ids), (custom, inputs.selected_option_ids[:1])]
            ],
        )

    options = dict(translator=stub, question_generator=ask, review_generator=offline_review)
    with monkeypatch.context() as patch:
        patch.setattr(requirements, "_order_conflicts", lambda case_id, rows, **kwargs: {})
        patch.setattr(requirements, "scoped_order_target", lambda *args, **kwargs: None)
        with TestClient(create_app(database, **options)) as client:
            snapshot = _upload(client, RAW).json()["snapshot"]
            previous = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=f"{stock} {custom}")))
            assert (previous["requirements"][1]["supplier_target"] is None) == scoped
            if had_review:
                previous = ready(finish_review(client, {**reference(previous), "operation_id": str(uuid4()), "submission": None}))
                assert ("requires a custom order" in previous["report"]["supplier_draft"]) == (not scoped)
                assert "ready-made stock" in previous["report"]["supplier_draft"]

    _replace_saved_case(database, lambda record: record.pop("order_conflict_version"))

    async def forbidden(*args):
        pytest.fail("Opening a saved case must not ask for questions or a review.")

    restore_options = {**options, "question_generator": forbidden, "review_generator": forbidden}
    for _ in range(2):
        with TestClient(create_app(database, **restore_options)) as client:
            reopened = client.get("/api/case").json()["case"]
            for field in ("case_id", "revision", "buyer_context", "rounds", "selected_option_ids"):
                assert reopened[field] == previous[field]
            conflict, = [row for row in reopened["requirements"] if row.get("order_conflict")]
            assert [entry["quote"] for entry in conflict["provenance"]] == [stock, custom]
            assert reopened["report"] is None
            if had_review:
                assert reopened["last_operation"]["code"] == "review_regeneration_required"
            else:
                assert reopened["phase"] == "ready_for_review"

    with TestClient(create_app(database, **options)) as client:
        if had_review:
            generated = ready(client.post("/api/reviews", json=confirm_command(reopen_brief(client, reopened))))
        else:
            generated = ready(finish_review(client, {**reference(reopened), "operation_id": str(uuid4()), "submission": None}))
        assert generated["report"]["open_preferences"][0]["option_ids"] == reopened["selected_option_ids"][:1]
        assert "requires a custom order" not in generated["report"]["supplier_draft"]
        assert "[Options B, C]" in generated["report"]["supplier_draft"]
    with TestClient(create_app(database, **restore_options)) as client:
        assert client.get("/api/case").json()["case"] == generated


def test_keep_reactivates_the_original_scoped_answer_after_uncertainty(tmp_path):
    database = tmp_path / "case.db"
    prior = "Ready-made stock only."
    answer = "Option A: custom orders are acceptable."
    prompts = (
        "What order arrangement will work for this option?",
        "Has that order preference changed?",
        "Do you want to keep the earlier order arrangement?",
    )

    async def ask(inputs):
        first = inputs.selected_option_ids[:1]
        scope = [row.requirement_id for row in inputs.requirements or [] if row.option_ids == first]
        initial = inputs.round_number == 1
        return QuestionResult(
            reason="Confirm the order terms for the first option.",
            context_requirements=[ContextRequirement(
                statement=prior, quote=prior, option_ids=inputs.selected_option_ids, topic="customization",
            )] if initial else None,
            questions=[QuestionDraft(
                prompt=prompts[inputs.round_number - 1], why_ask="The answer determines which order terms we should confirm.",
                topic="customization", buyer_facet="order_type", option_ids=first, source_ids=[], choices=[],
                requirement_ids=["context:1"] if initial else scope, choice_effects=[],
            )],
        )

    def submit(client, case, text, *, action=None):
        command = round_request(case, texts=[text])
        if action:
            target, = case["rounds"][-1]["questions"][0]["requirement_ids"]
            command["submission"]["answers"][0]["requirement_effect"] = {"action": action, "requirement_id": target}
        endpoint = "/api/reviews" if action == "keep" else "/api/questions/continue"
        return ready(finish_review(client, command) if endpoint == "/api/reviews" else client.post(endpoint, json=command))

    options = dict(translator=stub, question_generator=ask, review_generator=offline_review)
    with TestClient(create_app(database, **options)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        start = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=prior)))
        first = start["selected_option_ids"][:1]
        round_two = submit(client, start, answer, action="replace")
        chosen, = [row for row in round_two["requirements"] if row["option_ids"] == first]
        assert chosen["supplier_target"]["value"] == "custom_orders_acceptable"
        round_three = submit(client, round_two, "Still deciding.")
        uncertain, = [row for row in round_three["requirements"] if row["option_ids"] == first]
        assert uncertain["requirement_id"] == chosen["requirement_id"]
        assert uncertain["status"] == "unresolved" and uncertain["supplier_target"] is None
        target, = round_three["rounds"][-1]["questions"][0]["requirement_ids"]
        for action, text in [
            ("keep", "Option B: keep the earlier requirement."),
            ("waive", "No order requirement for Option A except Option B."),
            ("replace", "Custom orders are acceptable except B."),
        ]:
            command = round_request(round_three, texts=[text])
            command["submission"]["answers"][0]["requirement_effect"] = {"action": action, "requirement_id": target}
            rejected = finish_review(client, command)
            assert rejected.status_code == 422 and rejected.json()["code"] == "invalid_input"
            assert client.get("/api/case").json()["case"] == round_three
        final = submit(client, round_three, "Keep the earlier requirement.", action="keep")
        reactivated, = [row for row in final["requirements"] if row["option_ids"] == first]
        assert reactivated["requirement_id"] == chosen["requirement_id"]
        assert reactivated["status"] == "active" and reactivated["supplier_target"] == chosen["supplier_target"]
        assert [source["quote"] for source in reactivated["provenance"]] == [
            prior, answer, "Still deciding.", "Keep the earlier requirement.",
        ]
        row, = [row for row in final["report"]["buyer_requirements"] if row["option_ids"] == first]
        assert row["quote"] == answer and row["question_text"] == prompts[0]
        assert "requires a custom order" in final["report"]["supplier_draft"]
        assert "ready-made stock" in final["report"]["supplier_draft"]

    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == final


@pytest.mark.parametrize(("scope_index", "effect", "damaged"), [
    (0, "add", False),
    (0, "add", True),
    (1, "replace", False),
])
def test_version_one_case_recovers_scoped_answers_after_verifying_the_old_report(tmp_path, monkeypatch, scope_index, effect, damaged):
    database = tmp_path / "case.db"
    stock = "Ready-made stock only."
    answer = "For Option A, custom orders are acceptable."

    async def ask(inputs):
        return QuestionResult(
            reason="Confirm the order terms for this option.",
            context_requirements=[ContextRequirement(
                statement=stock, quote=stock, option_ids=inputs.selected_option_ids, topic="customization",
            )],
            questions=[QuestionDraft(
                prompt="What order arrangement would you accept?", why_ask="The order terms can change the purchase.",
                topic="customization", buyer_facet="order_type", option_ids=[inputs.selected_option_ids[scope_index]],
                source_ids=[], choices=[], requirement_ids=["context:1"], choice_effects=[],
            )],
        )

    original_infer = requirements._with_supplier_target

    def legacy_infer(row, **kwargs):
        return original_infer(row, **{**kwargs, "scoped_answers": False})

    options = dict(translator=stub, question_generator=ask, review_generator=offline_review)
    with monkeypatch.context() as patch:
        patch.setattr(case_workflow, "CURRENT_ORDER_CONFLICT_VERSION", 1)
        patch.setattr(requirements, "_with_supplier_target", legacy_infer)
        patch.setattr(requirements, "order_statement_matches_scope", lambda *args, **kwargs: True)
        patch.setattr(requirements, "written_order_scope", lambda *args, **kwargs: None)
        with TestClient(create_app(database, **options)) as client:
            snapshot = _upload(client, RAW).json()["snapshot"]
            case = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context=stock)))
            command = round_request(case, texts=[answer])
            target, = case["rounds"][0]["questions"][0]["requirement_ids"]
            command["submission"]["answers"][0]["requirement_effect"] = {
                "action": effect, "requirement_id": target if effect == "replace" else None,
            }
            old = ready(finish_review(client, command))
            assert not any(row.get("order_conflict") for row in old["requirements"])
            assert any(row["quote"] == answer for row in old["report"]["buyer_requirements"])

    if damaged:
        _replace_saved_case(database, lambda record: record["saved_review"].update(input_digest="0" * 64))

    async def forbidden(*args):
        pytest.fail("Opening a saved case must not ask for questions or a review.")

    restore_options = {**options, "question_generator": forbidden, "review_generator": forbidden}
    for _ in range(2):
        with TestClient(create_app(database, **restore_options)) as client:
            reopened = client.get("/api/case").json()["case"]
            assert reopened["rounds"] == old["rounds"]
            assert reference(reopened) == reference(old)
            assert reopened["report"] is None
            code = "invalid_review" if damaged else "review_regeneration_required"
            assert reopened["last_operation"]["code"] == code

    with TestClient(create_app(database, **{**restore_options, "review_generator": offline_review})) as client:
        rebuilt = ready(client.post("/api/reviews", json=confirm_command(reopen_brief(client, reopened))))
        assert "requires a custom order" not in rebuilt["report"]["supplier_draft"]
        if effect == "add":
            conflict, = [row for row in rebuilt["requirements"] if row.get("order_conflict")]
            assert conflict["option_ids"] == rebuilt["selected_option_ids"][:1]
            assert [item["quote"] for item in conflict["provenance"]] == [stock, answer]
        else:
            stock_options = {option for row in rebuilt["report"]["buyer_requirements"] if row["quote"] == stock
                             for option in row["option_ids"]}
            assert stock_options == set(rebuilt["selected_option_ids"])
            assert rebuilt["report"]["open_preferences"] == []
    with TestClient(create_app(database, **restore_options)) as client:
        assert client.get("/api/case").json()["case"] == rebuilt
