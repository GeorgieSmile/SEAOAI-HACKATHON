"""Render evidence gaps from the same snapshot and claims used by the report."""

from collections.abc import Mapping

from .contracts import Option, Source
from .report_models import Comparison, Coverage, CoverageSelection, ReviewContent, SentReviewEvidence


def _count(count: int, singular: str) -> str:
    return f"{count} {singular}" + ("" if count == 1 else "s")


def thumbnail_limitations(
    option: Option, alias: str, aspect: str, sources: Mapping[str, Source], sent: SentReviewEvidence,
    unreadable_in_report: bool,
) -> list[str]:
    """Explain a gap directly in the option's comparison cell."""
    subject = f"The saved thumbnail for Option {alias}"
    if option.image_source_id is None:
        problem = f"No thumbnail was linked to Option {alias}."
    elif sources[option.image_source_id].availability == "missing":
        problem = f"{subject} is missing."
    elif sources[option.image_source_id].availability == "unreadable":
        problem = f"{subject} could not be opened."
    elif option.image_source_id not in sent.attached_image_ids:
        problem = f"{subject} was not attached for review."
    elif unreadable_in_report:
        return [f"The {aspect} could not be read in the thumbnail for Option {alias}."]
    else:
        return []
    return [f"{problem} Its {aspect} could not be checked."]


def render_coverage(
    row: CoverageSelection, content: ReviewContent, comparisons: list[Comparison],
    aliases: Mapping[str, str], sources: Mapping[str, Source], sent: SentReviewEvidence,
    *, requirement_topics: set[str],
) -> Coverage:
    if row.group == "product_match":
        evidence_topics = [
            item.label for item in comparisons if item.shared_claim_ids or any(cell.claim_ids for cell in item.values)
        ]
        if evidence_topics:
            note = f"The comparison cites listing evidence for {', '.join(evidence_topics)}."
        else:
            note = "The comparison has no cited listing evidence for the selected options."
        if not requirement_topics:
            note += " No buyer requirement was linked for checking product fit."
        elif row.status != "reviewed":
            note += " Whether the delivered options meet the linked buyer requirements still needs confirmation."
    elif row.group == "intended_use":
        use_requirements = "temperature_use" in requirement_topics
        if use_requirements:
            note = "The review includes the buyer’s stated use or temperature conditions."
        else:
            note = "No buyer requirement describing use or temperature conditions was linked."
        if row.status != "reviewed" or not use_requirements:
            note += " Confirm the intended conditions and the product limits that apply to them."
        else:
            note += " Check the linked findings and their source evidence before confirming suitability."
    else:
        # Count only sources the displayed comparisons and findings actually cite.
        referenced = {cid for item in comparisons for cid in item.shared_claim_ids}
        referenced.update(cid for item in comparisons for cell in item.values for cid in cell.claim_ids)
        referenced.update(cid for item in content.findings for cid in item.claim_ids)
        cited = {c.source_id for c in content.claims if c.claim_id in referenced}
        text_count = sum(sources[sid].kind == "listing_text" for sid in cited)
        photo_count = sum(sources[sid].kind == "listing_image" for sid in cited)
        guidance_count = sum(sources[sid].kind == "official_guidance" for sid in cited)
        note = (f"Cited evidence includes {_count(text_count, 'listing text source')}, "
                f"{_count(photo_count, 'SKU thumbnail')}, and {_count(guidance_count, 'guidance source')}.")
        missing = [
            alias for oid, alias in aliases.items()
            if not any(oid in sources[sid].option_ids for sid in sent.attached_image_ids)
        ]
        if missing:
            scope = "Option" if len(missing) == 1 else "Options"
            note += f" No SKU thumbnail was attached for {scope} {', '.join(missing)}."
        elif not cited:
            note += " The report does not link supporting evidence to an assessment."
        else:
            note += " Listing material alone cannot verify the delivered item."
    return Coverage(**row.model_dump(), note=note)
