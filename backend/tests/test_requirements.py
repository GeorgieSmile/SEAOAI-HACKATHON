from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend import requirements
from backend.case_models import SavedReview
from backend.case_workflow import _review_digest
from backend.contracts import ContextRequirement, QuestionDraft, QuestionResult
from backend.main import create_app
from backend.report import assemble_review, review_sent_evidence
from backend.report_models import ReviewContent, ReviewReady
from backend.tests.offline_server import offline_review
from backend.tests.test_cases import initial_request, ready, reference, round_request
from backend.tests.test_imports import RAW, _image_url, _upload, stub
from backend.tests.test_reports import finding_selection, minimal_content
from backend.tests.test_review_workflow import _replace_saved_case, retry_request


def _install_v5_report(database, case, inputs, images, content):
    evidence = review_sent_evidence(inputs, images)
    report = assemble_review(inputs, content, sent_evidence=evidence).model_dump(mode="json")
    digest = _review_digest(inputs, case["last_operation"]["operation_id"], images, evidence, version=5)
    saved = SavedReview(version=5, input_digest=digest, content=content, sent_evidence=evidence)

    def replace(record):
        record["view"]["report"] = report
        record["saved_review"] = saved.model_dump(mode="json")

    _replace_saved_case(database, replace)
    return {**case, "report": report}


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

    async def old_review(inputs, images):
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
        evidence = review_sent_evidence(inputs, images)
        exact = ReviewContent.model_validate(content)
        return ReviewReady(report=assemble_review(inputs, exact, sent_evidence=evidence), content=exact, sent_evidence=evidence)

    with TestClient(create_app(database, translator=stub, question_generator=ask, review_generator=old_review)) as client:
        snapshot = _upload(client).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot)))
        old = ready(client.post("/api/reviews", json=round_request(case, texts=[first_quote, second_quote])))
    _replace_saved_case(database, lambda record: record.pop("requirement_version"))

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
            regenerated = ready(client.post("/api/case/retry", json=retry_request(recovered)))
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
            previous = ready(client.post(endpoint, json=round_request(case, texts=[quote])))
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
            regenerated = ready(client.post("/api/case/retry", json=retry_request(recovered)))
        else:
            command = {**reference(recovered), "operation_id": str(uuid4()), "submission": None}
            regenerated = ready(client.post("/api/reviews", json=command))
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
            previous = ready(client.post(endpoint, json=issue))
            assert previous["requirements"][0]["supplier_target"] is None
            image_url = _image_url(snapshot, snapshot["options"][2])
            image = client.get(image_url)
            assert image.status_code == 200
            original_image = image.content

    if state != "unfinished":
        (inputs, images), = prior_inputs
        content = ReviewContent.model_validate(minimal_content(inputs, topic="finish"))
        previous = _install_v5_report(database, previous, inputs, images, content)
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
                assert client.post("/api/reviews", json=issue).json()["code"] == code

    if state == "qualified-context":
        return
    requests = []

    async def review(inputs, images):
        requests.append(inputs)
        return await offline_review(inputs, images)

    with TestClient(create_app(database, **{**recovery_options, "review_generator": review})) as client:
        if state == "unfinished":
            endpoint = "/api/reviews"
            command = {**reference(recovered), "operation_id": str(uuid4()), "submission": None}
        else:
            endpoint = "/api/case/retry"
            command = retry_request(recovered)
        reviewed = ready(client.post(endpoint, json=command))
        assert ready(client.post(endpoint, json=command)) == reviewed
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
        rejected = client.post("/api/reviews", json=round_request(asking)).json()
        assert rejected["code"] == "invalid_review"
        failed = client.get("/api/case").json()["case"]
        assert failed["report"] is None
        reviewed = ready(client.post("/api/case/retry", json=retry_request(failed)))
    (inputs, images, content), = prior_reports
    old = _install_v5_report(database, reviewed, inputs, images, content)
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
            rebuilt = ready(client.post("/api/case/retry", json=retry_request(reopened)))
            sample, = rebuilt["report"]["open_preferences"]
            assert sample["text"] == "Your preference for samples is still open."
            assert sample["option_ids"] == rebuilt["selected_option_ids"][:1]
            assert "1 December" in rebuilt["report"]["supplier_draft"]
