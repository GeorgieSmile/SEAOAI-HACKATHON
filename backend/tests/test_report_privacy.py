from copy import deepcopy

import pytest

from backend.contracts import ReviewInput
from backend.report import assemble_review
from backend.report_models import ReviewContent, ReviewFailed
from backend.tests.test_reports import minimal_content, parsed_listing, response, run_review


def build_case(listing, buyer_text, supplier_question, *, source="answer"):
    request = ReviewInput.model_validate({
        "case_id": "privacy-case", "revision": 1, "snapshot": listing.snapshot.model_dump(),
        "selected_option_ids": [listing.snapshot.options[0].option_id],
        "buyer_context": buyer_text if source == "context" else "",
        "answers": [{
            "question_id": "issued-1", "question_text": "What else should the review consider?",
            "answer_text": buyer_text if source == "answer" else None,
        }],
    })
    payload = minimal_content(request)
    payload["findings"] = [{
        "finding_id": "check-sample", "title": "Confirm sample availability",
        "option_ids": request.selected_option_ids, "kind": "clarification_needed", "action_type": "ask_supplier",
        "requirement_ids": [], "citations": [], "interpretation": "Sample availability is still open.",
        "impact": "A sample can help the buyer inspect the finish.", "priority_reason": "Check the sample before ordering.",
        "unknowns": [], "requested_evidence": "Ask whether a sample is available.",
        "evidence_limits": "One sample only shows the inspected piece.", "supplier_question": supplier_question,
    }]
    return request, payload


@pytest.mark.parametrize(("source", "buyer_text", "question"), [
    ("answer", "Reach cedar914@example.test before sampling.", "Can you send sample photos to cedar914@example.test?"),
    ("answer", "Reach 'cedar914@example.test' before sampling.", "Can you email cedar914@example.test?"),
    ("context", "Email: Cedar914＠example.test.", "Could you email CEDAR914@example.test?"),
    ("context", "I need a sample before ordering.", "Can you send details to samples@example.test?"),
    ("answer", "Call +1 (415) 555-0136 before sending the sample.", "Can you call +1-415-555-0136?"),
    ("context", "Please reach me on 081 234 5678.", "Can you send photos to 0812345678?"),
    ("answer", "Our internal margin target is 43.5% before shipping.", "Could we make 43.5% work?"),
    ("answer", "Internal note: Project Cedar-914 applies to this purchase.", "Can you use Cedar-914 on the sample?"),
    ("context", "Private note: use the Cedar Lotus Launch reference.", "Can you put Cedar Lotus Launch on the sample?"),
])
def test_draft_rejects_private_buyer_details_copied_without_the_surrounding_note(parsed_listing, source, buyer_text, question):
    request, payload = build_case(parsed_listing, buyer_text, question, source=source)
    with pytest.raises(ValueError, match="private business details") as error:
        assemble_review(request, ReviewContent.model_validate(payload), sent_source_ids=set())
    assert question not in str(error.value)


@pytest.mark.parametrize(("buyer_text", "question"), [
    ("Buyer margin: 43.5%. We require 720 ml and 316L steel.", "Can you confirm 720 ml and 316L steel?"),
    ("Private note: project Cedar-914. Use SKU 316-A.", "Can you confirm SKU 316-A and a 150 × 150 mm box?"),
    ("Reach cedar914@example.test before sampling.", "What business email should we use to request a sample?"),
    ("Call 081-234-5678 before sampling. SKU code 1290472158.", "Can you confirm SKU 1290472158?"),
])
def test_draft_allows_supplier_questions_about_product_specs(parsed_listing, buyer_text, question):
    request, payload = build_case(parsed_listing, buyer_text, question)
    report = assemble_review(request, ReviewContent.model_validate(payload), sent_source_ids=set())
    assert question in report.supplier_draft


def test_model_cannot_publish_a_report_that_keeps_leaking_contact_details(parsed_listing):
    request, payload = build_case(parsed_listing, "Reach cedar914@example.test before sampling.",
                                  "Please send the sample photos to cedar914@example.test.")
    attempts = []

    def provider(http_request):
        attempts.append(http_request)
        return response(http_request, deepcopy(payload))

    result = run_review(request, {}, provider)
    assert isinstance(result, ReviewFailed)
    assert result.code == "invalid_review"
    assert "cedar914@example.test" not in result.message
    assert len(attempts) == 2
