import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.ai import generate_questions
from backend.contracts import ORDER_CONFLICT_CHOICES, ORDER_CONFLICT_PROMPT, QuestionResult
from backend.main import create_app
from backend.report import generate_review
from backend.tests.test_cases import confirm_command, finish_review, initial_request, ready, reference, round_request
from backend.tests.test_imports import RAW, _upload, stub
from backend.tests.test_purchase_decisions import scripted_client
from backend.tests.test_reports import canonical_content, response


STOCK = "Ready-made stock only."
CUSTOM = "Custom orders are acceptable."
CUSTOM_FOR_A = "For Option A, custom orders are acceptable."
STOCK_FOR_A = "Ready-made stock only for Option A."
CUSTOM_FOR_BC = "Custom orders are acceptable for Options B and C."
MATTE = "A matte finish is required."
ASK_ABOUT_A = "Are custom orders acceptable for Option A?"


def context_proposal(quote, scope, topic="customization", facet="order_type"):
    return {"quote": quote, "statement": quote, "option_ids": scope, "topic": topic, "buyer_facet": facet}


def scripted_questions(*, from_context, overlaps=True, declare_target=False, offered_answer=CUSTOM, punctuation="."):
    async def generate(inputs):
        def serve(request):
            first, *others = inputs.selected_option_ids
            stock = STOCK if overlaps else STOCK_FOR_A
            custom = (CUSTOM_FOR_A if overlaps else CUSTOM_FOR_BC).removesuffix(".") + punctuation
            context = [context_proposal(stock, inputs.selected_option_ids if overlaps else [first])]
            questions = []
            if from_context:
                context.append(context_proposal(custom, [first] if overlaps else others))
                if declare_target:
                    context[-1]["supplier_target"] = {"kind": "order_type", "value": "custom_orders_acceptable"}
            else:
                context.append(context_proposal(MATTE, inputs.selected_option_ids, "finish", "surface_finish"))
                questions.append({
                    "prompt": ASK_ABOUT_A, "why_ask": "The choice affects the order terms to confirm for Option A.",
                    "option_ids": [first], "source_ids": [], "topic": "customization", "buyer_facet": "order_type",
                    "requirement_ids": [], "choices": [offered_answer] if offered_answer else [],
                    "choice_effects": [{
                        "choice": offered_answer, "action": "add", "requirement_id": None, "statement": offered_answer,
                    }] if offered_answer else [],
                })
                if declare_target and offered_answer:
                    questions[0]["choice_effects"][0]["supplier_target"] = {
                        "kind": "order_type", "value": "custom_orders_acceptable",
                    }
            return response(request, {"reason": "Review the order terms.", "context_requirements": context, "questions": questions})

        async with scripted_client(serve) as sdk:
            return await generate_questions(inputs, client=sdk)

    return generate


def scripted_review(requests):
    async def generate(inputs, images):
        def serve(request):
            parts = json.loads(request.content)["input"][0]["content"]
            requests.append(json.loads(parts[0]["text"])["requirements"])
            return response(request, canonical_content(inputs, "customization"))

        async with scripted_client(serve) as sdk:
            return await generate_review(inputs, images, client=sdk)

    return generate


def publish(client, case, command=None):
    command = {**reference(case), "operation_id": str(uuid4()), "submission": None} if command is None else command
    return ready(finish_review(client, command))


def buy(client, context):
    archive = Path(os.environ.get("PURCHASE_TEST_ZIP", RAW))
    snapshot = _upload(client, archive).json()["snapshot"]
    start = initial_request(snapshot, buyer_context=context)
    start["selected_option_ids"].reverse()
    return ready(client.post("/api/questions", json=start))


@pytest.mark.parametrize("choice", ["ready_made_only", "custom_orders_acceptable"])
@pytest.mark.parametrize("offered", [True, False])
def test_conflicting_targeted_answer_has_one_buyer_choice_and_neutral_supplier_scope(tmp_path, choice, offered):
    database = tmp_path / "case.db"
    requests = []
    options = dict(
        translator=stub,
        question_generator=scripted_questions(
            from_context=False, declare_target=offered, offered_answer=CUSTOM_FOR_A if offered else None,
        ),
        review_generator=scripted_review(requests),
    )
    with TestClient(create_app(database, **options)) as client:
        issued = buy(client, f"{STOCK} {MATTE}")
        first, *others = issued["selected_option_ids"]
        assert issued["rounds"][0]["questions"][0]["option_ids"] == [first]
        command = round_request(issued, texts=[CUSTOM_FOR_A])
        if not offered:
            command["submission"]["answers"][0]["requirement_effect"] = {"action": "add", "requirement_id": None}
        open_case = publish(client, issued, command)

    report = open_case["report"]
    conflict, = [row for row in open_case["requirements"] if row.get("order_conflict")]
    assert conflict["option_ids"] == [first]
    assert [(source["quote"], source["question_text"]) for source in conflict["provenance"]] == [
        (STOCK, None), (CUSTOM_FOR_A, ASK_ABOUT_A),
    ]
    preference, = report["open_preferences"]
    assert preference["preference_id"] == f"requirement:{conflict['requirement_id']}"
    assert preference["text"] == ORDER_CONFLICT_PROMPT
    assert preference["option_ids"] == [first] and preference["finding_ids"] == []
    assert [row["option_ids"] for row in requests[0] if row["topic"] == "customization"] == [others]
    assert "[Options B, C]" in report["supplier_draft"]
    assert "ready-made stock" in report["supplier_draft"] and "custom order" not in report["supplier_draft"]
    assert "matte finish" in report["supplier_draft"]

    command = {
        **reference(open_case), "operation_id": str(uuid4()), "submission": None,
        "order_clarification": {"requirement_id": conflict["requirement_id"], "option_id": first, "choice": choice},
    }
    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == open_case
        assert len(requests) == 1
        resolved = publish(client, open_case, command)
        assert ready(client.post("/api/reviews", json=confirm_command(resolved, command["operation_id"]))) == resolved
        assert len(requests) == 2

    assert resolved["revision"] == open_case["revision"] + 1
    assert resolved["rounds"] == open_case["rounds"]
    assert resolved["report"]["open_preferences"] == []
    chosen, = [row for row in resolved["report"]["buyer_requirements"] if row["option_ids"] == [first]]
    assert chosen["quote"] == ORDER_CONFLICT_CHOICES[choice]
    assert chosen["question_text"] == ORDER_CONFLICT_PROMPT and chosen["input_ref"].startswith("clarification:")
    draft_lines = resolved["report"]["supplier_draft"].splitlines()
    stock, = [line for line in draft_lines if "Please answer for ready-made stock." in line]
    if choice == "ready_made_only":
        assert "[Options A, B, C]" in stock
        assert not any("requires a custom order" in line for line in draft_lines)
    else:
        assert "[Options B, C]" in stock
        custom, = [line for line in draft_lines if "requires a custom order" in line]
        assert "[Option A]" in custom
    assert "matte finish" in resolved["report"]["supplier_draft"]

    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == resolved
        assert len(requests) == 2


@pytest.mark.parametrize("scope_count,quote,expected", [
    (1, CUSTOM_FOR_A, "custom"),
    (1, "For Option A, custom orders are acceptable if the lead time works.", "conditional"),
    (1, "For Option B, custom orders are acceptable.", "wrong_scope"),
    (3, CUSTOM_FOR_A, "wrong_scope"),
    (3, "Custom orders are acceptable for Option A if the lead time works.", "wrong_scope"),
])
def test_free_text_order_replacement_must_stay_inside_the_exact_issued_scope(tmp_path, scope_count, quote, expected):
    database = tmp_path / "case.db"
    requests = []

    async def questions(inputs):
        def serve(request):
            return response(request, {
                "reason": "Check whether the buyer wants to revise the existing order terms.",
                "context_requirements": [context_proposal(STOCK, inputs.selected_option_ids)],
                "questions": [{
                    "prompt": "What order terms should the review use for these options?",
                    "why_ask": "Your answer affects the order terms to confirm.",
                    "option_ids": inputs.selected_option_ids[:scope_count], "source_ids": [],
                    "topic": "customization", "buyer_facet": "order_type", "requirement_ids": ["context:1"],
                    "choices": [], "choice_effects": [],
                }],
            })

        async with scripted_client(serve) as sdk:
            return await generate_questions(inputs, client=sdk)

    options = dict(translator=stub, question_generator=questions, review_generator=scripted_review(requests))
    with TestClient(create_app(database, **options)) as client:
        issued = buy(client, STOCK)
        first = issued["selected_option_ids"][0]
        requirement_id, = issued["rounds"][0]["questions"][0]["requirement_ids"]
        command = round_request(issued, texts=[quote])
        command["submission"]["answers"][0]["requirement_effect"] = {
            "action": "replace", "requirement_id": requirement_id,
        }
        result = finish_review(client, command)
        if expected == "wrong_scope":
            assert result.status_code == 422 and result.json()["code"] == "invalid_input"
            assert client.get("/api/case").json()["case"] == issued
            assert requests == []
            return
        reviewed = ready(result)

    a, = [row for row in reviewed["requirements"] if row["option_ids"] == [first]]
    assert a["statement"] == a["provenance"][-1]["quote"] == quote
    assert not a.get("order_conflict")
    assert reviewed["rounds"][0]["answers"][0]["answer_text"] == quote
    report = reviewed["report"]
    b_c_draft, = [line for line in report["supplier_draft"].splitlines() if "[Options B, C]" in line]
    assert "ready-made stock" in b_c_draft and "custom order" not in b_c_draft
    if expected == "custom":
        assert a["supplier_target"]["value"] == "custom_orders_acceptable"
        assert any(row["quote"] == quote and row["option_ids"] == [first] for row in report["buyer_requirements"])
        a_draft, = [line for line in report["supplier_draft"].splitlines() if "[Option A]" in line]
        assert "requires a custom order" in a_draft
    else:
        assert a["supplier_target"] is None
        assert "[Option A]" not in report["supplier_draft"]

    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == reviewed
        assert len(requests) == 1


def test_model_cannot_issue_a_narrower_order_choice_on_a_broad_question(tmp_path):
    attempts = []

    async def questions(inputs):
        def serve(request):
            attempts.append(json.loads(request.content)["instructions"])
            scope = inputs.selected_option_ids if len(attempts) == 1 else inputs.selected_option_ids[:1]
            return response(request, {
                "reason": "Clarify the order terms.", "context_requirements": [],
                "questions": [{
                    "prompt": "What order terms are acceptable?",
                    "why_ask": "This changes which order terms the seller should confirm.",
                    "option_ids": scope, "source_ids": [], "topic": "customization", "buyer_facet": "order_type",
                    "requirement_ids": [], "choices": [CUSTOM_FOR_A],
                    "choice_effects": [{
                        "choice": CUSTOM_FOR_A, "action": "add", "requirement_id": None, "statement": CUSTOM_FOR_A,
                    }],
                }],
            })

        async with scripted_client(serve) as sdk:
            return await generate_questions(inputs, client=sdk)

    with TestClient(create_app(tmp_path / "case.db", translator=stub, question_generator=questions)) as client:
        issued = buy(client, "")

    assert len(attempts) == 2
    assert "named options must match the scope" in attempts[1]
    question, = issued["rounds"][0]["questions"]
    assert question["option_ids"] == issued["selected_option_ids"][:1]
    assert question["choices"] == [CUSTOM_FOR_A]


@pytest.mark.parametrize("overlaps,declare_target,punctuation", [
    (True, False, "."), (True, True, "."), (False, True, "."), (True, False, "!"),
])
def test_stock_and_custom_context_with_zero_questions_shows_a_choice_only_for_overlap(
    tmp_path, overlaps, declare_target, punctuation,
):
    database = tmp_path / "case.db"
    requests = []
    with TestClient(create_app(
        database, translator=stub,
        question_generator=scripted_questions(
            from_context=True, overlaps=overlaps, declare_target=declare_target, punctuation=punctuation,
        ),
        review_generator=scripted_review(requests),
    )) as client:
        scoped_custom = CUSTOM_FOR_A.removesuffix(".") + punctuation
        context = f"{STOCK} {scoped_custom}" if overlaps else f"{STOCK_FOR_A} {CUSTOM_FOR_BC}"
        case = buy(client, context)
        first, *others = case["selected_option_ids"]
        assert case["phase"] == "ready_for_review" and case["rounds"] == []
        reviewed = publish(client, case)
        report = reviewed["report"]
        if overlaps:
            conflict, = [row for row in reviewed["requirements"] if row.get("order_conflict")]
            assert conflict["option_ids"] == [first]
            assert [source["quote"] for source in conflict["provenance"]] == [STOCK, scoped_custom]
            assert [row["text"] for row in report["open_preferences"]] == [ORDER_CONFLICT_PROMPT]
            assert [row["option_ids"] for row in requests[0]] == [others]
            corrected = publish(client, reviewed, {
                **reference(reviewed), "operation_id": str(uuid4()), "submission": None,
                "order_clarification": {"requirement_id": conflict["requirement_id"], "option_id": first,
                                        "choice": "custom_orders_acceptable"},
            })
            assert corrected["rounds"] == []
            report = corrected["report"]
            assert "[Options B, C]" in report["supplier_draft"] and "Please answer for ready-made stock." in report["supplier_draft"]
        else:
            assert not any(row.get("order_conflict") for row in reviewed["requirements"])
            assert [row["option_ids"] for row in requests[0]] == [[first], others]
            assert [row["quote"] for row in report["buyer_requirements"]] == [STOCK_FOR_A, CUSTOM_FOR_BC]
            assert "[Option A]" in report["supplier_draft"] and "Please answer for ready-made stock." in report["supplier_draft"]
        assert report["open_preferences"] == []
        assert "requires a custom order" in report["supplier_draft"]


def test_default_open_answer_cannot_reopen_another_options_settled_order_type(tmp_path):
    async def question_generator(inputs):
        return QuestionResult.model_validate({
            "reason": "Check whether the prior stock requirement still applies.",
            "context_requirements": [context_proposal(STOCK, inputs.selected_option_ids)],
            "questions": [{
                "prompt": ASK_ABOUT_A,
                "why_ask": "This determines the ordering terms for Option A.",
                "option_ids": inputs.selected_option_ids[:1], "source_ids": [],
                "topic": "customization", "buyer_facet": "order_type",
                "requirement_ids": ["context:1"], "choices": [], "choice_effects": [],
            }],
        })

    with TestClient(create_app(
        tmp_path / "case.db", translator=stub, question_generator=question_generator,
    )) as client:
        case = buy(client, STOCK)
        wrong_sku = round_request(case, texts=["For Option B, custom orders are acceptable."])
        assert client.post("/api/brief", json=wrong_sku).status_code == 422
        assert client.get("/api/case").json()["case"] == case
        assert [row["status"] for row in case["requirements"]] == ["active"]


@pytest.mark.parametrize("quote", [
    "For Option A, custom orders are acceptable unless the deadline changes.",
    "Custom orders are acceptable for Option A if the lead time works.",
    "For Option A, custom orders are acceptable except at rush rates.",
    CUSTOM_FOR_A,
])
def test_scoped_context_keeps_conditions_and_rejects_the_wrong_sku(tmp_path, quote):
    requests = []
    instructions = []
    conditional = quote != CUSTOM_FOR_A

    async def questions(inputs):
        def serve(request):
            instructions.append(json.loads(request.content)["instructions"])
            first_attempt = len(instructions) == 1
            first, second, _ = inputs.selected_option_ids
            option_id = second if first_attempt and not conditional else first
            proposal = context_proposal(quote, [option_id])
            if conditional:
                if first_attempt:
                    proposal["supplier_target"] = {"kind": "order_type", "value": "custom_orders_acceptable"}
                else:
                    proposal["status"] = "unresolved"
            return response(request, {"reason": "Review the order terms.", "context_requirements": [proposal], "questions": []})

        async with scripted_client(serve) as sdk:
            return await generate_questions(inputs, client=sdk)

    with TestClient(create_app(tmp_path / "case.db", translator=stub, question_generator=questions,
                               review_generator=scripted_review(requests))) as client:
        case = buy(client, quote)
        reviewed = publish(client, case)

    assert len(instructions) == 2
    row, = reviewed["requirements"]
    assert row["statement"] == quote and row["option_ids"] == case["selected_option_ids"][:1]
    assert row["provenance"][0]["quote"] == quote
    if conditional:
        assert "complete quoted buyer statement" in instructions[1]
        assert row["status"] == "unresolved" and row["supplier_target"] is None
        assert reviewed["report"]["supplier_draft"] == ""
    else:
        assert "named options must match the scope" in instructions[1]
        assert row["supplier_target"]["value"] == "custom_orders_acceptable"
        draft = reviewed["report"]["supplier_draft"]
        assert "[Option A]" in draft and "requires a custom order" in draft and "[Option B]" not in draft


def test_followup_questions_leave_the_existing_order_conflict_to_the_review(tmp_path):
    initial = scripted_questions(from_context=False)
    instructions = []

    async def questions(inputs):
        if inputs.round_number == 1:
            return await initial(inputs)

        def serve(request):
            instructions.append(json.loads(request.content)["instructions"])
            first_attempt = len(instructions) == 1
            return response(request, {
                "reason": "Clarify one remaining buyer concern.", "context_requirements": None,
                "questions": [{
                    "prompt": "Would you accept a custom order?" if first_attempt else "Do you need a sample?",
                    "why_ask": "This changes what the supplier should confirm.", "source_ids": [],
                    "option_ids": inputs.selected_option_ids[:1], "choices": [], "choice_effects": [], "requirement_ids": [],
                    "topic": "customization" if first_attempt else "sample", "buyer_facet": "order_type" if first_attempt else "other",
                }],
            })

        async with scripted_client(serve) as sdk:
            return await generate_questions(inputs, client=sdk)

    with TestClient(create_app(tmp_path / "case.db", translator=stub, question_generator=questions)) as client:
        issued = buy(client, f"{STOCK} {MATTE}")
        followup = ready(client.post("/api/questions/continue", json=round_request(issued, texts=[CUSTOM])))

    assert len(instructions) == 2
    assert "The buyer resolves existing order conflicts in the review." in instructions[1]
    assert followup["rounds"][1]["questions"][0]["prompt"] == "Do you need a sample?"
    assert [row["option_ids"] for row in followup["requirements"] if row.get("order_conflict")] == [
        issued["selected_option_ids"][:1],
    ]
