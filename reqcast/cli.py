"""CLI entry point: PDF / Markdown / plain text / .docx -> extracted nodes -> .reqifz."""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

from .extract import Config, apply_manual_figures, apply_redactions, extract
from .reqif import ReqIfWriter

_PDF_ONLY_DEFAULTS = {"slices_per_sheet": 1, "slice_direction": "horizontal", "first_page": None, "last_page": None}
_SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".docx"}


def _load_config(path: pathlib.Path | None) -> Config:
    if path is None:
        raise SystemExit(
            "A --config JSON file is required (at minimum: {\"id_pattern\": \"...\"}). "
            "See README.md for the pattern reference and an example."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in dataclasses.fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise SystemExit(f"Unknown config key(s): {sorted(unknown)}")
    return Config(**data)


def _run_pdf(args, cfg: Config):
    import pymupdf

    from .layout import iter_logical_pages

    doc = pymupdf.open(args.source)
    if args.first_page or args.last_page:
        first = (args.first_page or 1) - 1
        last = args.last_page or len(doc)
        doc.select(range(first, last))

    pages = iter_logical_pages(doc, slices_per_sheet=args.slices_per_sheet,
                               slice_direction=args.slice_direction)
    roots, report = extract(pages, cfg)
    failed_manual_figures = apply_manual_figures(roots, pages, cfg.manual_figures)
    return roots, report, failed_manual_figures


def _run_txt(args, cfg: Config):
    from .text_formats import extract_txt

    roots, report = extract_txt(args.source.read_text(encoding="utf-8"), cfg)
    return roots, report, []


def _run_markdown(args, cfg: Config):
    from .text_formats import extract_markdown

    roots, report = extract_markdown(args.source.read_text(encoding="utf-8"), args.source.parent, cfg)
    return roots, report, []


def _run_docx(args, cfg: Config):
    from .docx_extract import extract_docx

    roots, report = extract_docx(args.source, cfg)
    return roots, report, []


_RUNNERS = {
    ".pdf": _run_pdf,
    ".txt": _run_txt,
    ".md": _run_markdown,
    ".markdown": _run_markdown,
    ".docx": _run_docx,
}


def _check_format_specific_args(args, suffix: str) -> None:
    if suffix == ".pdf":
        return
    set_flags = [f for f, default in _PDF_ONLY_DEFAULTS.items() if getattr(args, f) != default]
    if set_flags:
        raise SystemExit(f"{', '.join('--' + f.replace('_', '-') for f in set_flags)} only apply to PDF input")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path,
                        help="Source document: .pdf, .md/.markdown, .txt, or .docx")
    parser.add_argument("out", type=pathlib.Path,
                        help="Output path: a .reqifz package by default, or a plain .reqif file with --reqif-only")
    parser.add_argument("--config", type=pathlib.Path, help="JSON file of Config fields")
    parser.add_argument("--title", default=None, help="Specification title (default: source filename stem)")
    parser.add_argument("--embed-images", action="store_true",
                        help="Embed each figure inline as a base64 data URI "
                             "(<xhtml:object data=\"data:image/png;base64,...\">), per the ReqIF spec's own "
                             "allowance for embedded binary data. Default: link each figure as a real file "
                             "under media/ (bundled inside the .reqifz, or written alongside a plain .reqif "
                             "with --reqif-only) - smaller XML, easier to diff, but a two-part output.")
    parser.add_argument("--reqif-only", action="store_true",
                        help="Write a plain, unzipped .reqif file to `out` instead of a .reqifz package. "
                             "Without --embed-images, linked figures are still written to a media/ directory "
                             "next to `out` so the file's own references resolve; with --embed-images, `out` "
                             "is fully self-contained.")
    parser.add_argument("--slices-per-sheet", type=int, default=1,
                        help="[PDF only] Independent logical pages per physical sheet (e.g. 2 for a booklet export)")
    parser.add_argument("--slice-direction", choices=["horizontal", "vertical"], default="horizontal",
                        help="[PDF only]")
    parser.add_argument("--first-page", type=int, default=None, help="[PDF only] 1-based, inclusive")
    parser.add_argument("--last-page", type=int, default=None, help="[PDF only] 1-based, inclusive")
    parser.add_argument("--report", type=pathlib.Path, default=None,
                        help="Write a JSON extraction report here (for the manual review/polishing step)")
    parser.add_argument("--polish", action="store_true",
                        help="Second pass: ask Claude to reflow mechanically-extracted requirement text into "
                             "natural paragraphs (formatting only - see reqcast/polish.py for the exact "
                             "rules). Mainly useful for PDF input, where a source line-wrap can still slip "
                             "through as a paragraph break; Markdown/plain-text/.docx input already joins "
                             "paragraphs correctly at extraction time, so --polish is usually a no-op there. "
                             "Requests are batched (TOON-encoded, see --polish-batch-tokens), not one API "
                             "call per requirement. Requires the `anthropic` package and API credentials.")
    parser.add_argument("--polish-model", default="claude-opus-5",
                        help="Model for --polish (default: %(default)s)")
    parser.add_argument("--polish-temperature", type=float, default=0.0,
                        help="Sampling temperature for --polish (default: %(default)s). This pass only "
                             "reflows and repairs text, never invents it - a non-zero temperature raises the "
                             "risk of the model filling gaps or varying wording it has no business touching.")
    parser.add_argument("--polish-batch-tokens", type=int, default=None,
                        help="Approximate token budget per batched --polish request, overriding "
                             "config.json's polish_batch_tokens if both are given (default: "
                             "reqcast.polish.DEFAULT_POLISH_BATCH_TOKENS). Larger batches mean fewer API "
                             "calls and more TOON compression, but risk shallower model attention on "
                             "requirements that land mid-batch (the 'lost in the middle' effect) - see "
                             "apply_polish's docstring in reqcast/polish.py.")
    parser.add_argument("--polish-injection-check", choices=["strict", "off"], default=None,
                        help="'strict' (default) screens every --polish request/reply for prompt-injection "
                             "phrasing before sending, failing closed on a match (see reqcast/polish.py). "
                             "'off' disables both screens - for a document you already trust that happens to "
                             "legitimately share this vocabulary (a spec that itself defines an AI/LLM "
                             "system's behavior or prompt format), where the heuristic would otherwise block "
                             "most of it from ever being polished. Overrides config.json's "
                             "polish_injection_check if both are given.")
    args = parser.parse_args(argv)

    suffix = args.source.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise SystemExit(f"Unsupported input type {suffix!r} - expected one of {sorted(_SUPPORTED_SUFFIXES)}")
    _check_format_specific_args(args, suffix)

    cfg = _load_config(args.config)
    if suffix != ".pdf" and cfg.manual_figures:
        raise SystemExit("`manual_figures` in config.json is PDF-only (keyed to a page/slice/bbox) - "
                          "remove it for non-PDF input")

    roots, report, failed_manual_figures = _RUNNERS[suffix](args, cfg)

    polish_report = None
    if args.polish:
        from .polish import DEFAULT_POLISH_BATCH_TOKENS, apply_polish
        batch_tokens = args.polish_batch_tokens or cfg.polish_batch_tokens or DEFAULT_POLISH_BATCH_TOKENS
        injection_check_mode = args.polish_injection_check or cfg.polish_injection_check
        polish_report = apply_polish(
            roots, args.polish_model,
            batch_tokens=batch_tokens,
            temperature=args.polish_temperature,
            injection_check=(injection_check_mode != "off"),
        )

    apply_redactions(roots, cfg.redact)

    title = args.title or args.source.stem
    writer = ReqIfWriter(title=title, embed_images=args.embed_images)
    xml = writer.render(roots)
    if args.reqif_only:
        writer.write_reqif(xml, args.out)
    else:
        writer.package(xml, args.out)

    summary = {
        "source_format": suffix.lstrip("."),
        "logical_pages": report.logical_pages,
        "requirements": report.requirements,
        "headings": report.headings,
        "figures_found": report.figures_found,
        "figures_cropped": report.figures_cropped,
        "figures_skipped": report.figures_skipped,
        "discovered_attribute_labels": dict(report.discovered_attribute_labels),
        "boilerplate_lines_dropped": report.boilerplate_lines_dropped,
        "manual_figures_applied": len(cfg.manual_figures) - len(failed_manual_figures),
        "manual_figures_failed": failed_manual_figures,
        "output": str(args.out),
    }
    if polish_report is not None:
        summary["polish"] = {
            "attempted": polish_report.attempted,
            "changed": polish_report.changed,
            "unchanged": polish_report.unchanged,
            "rejected_length": polish_report.rejected_length,
            "flagged_injection": polish_report.flagged_injection,
            "rejected_injection": polish_report.rejected_injection,
            "failed": polish_report.failed,
            "errors": polish_report.errors,
            "flagged": polish_report.flagged,
        }
    print(json.dumps(summary, indent=2))
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
