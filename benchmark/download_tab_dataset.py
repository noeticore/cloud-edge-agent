"""Download the Text Anonymization Benchmark (TAB) dataset from HuggingFace.

TAB is a corpus of 1,268 English-language court cases from the European Court
of Human Rights (ECHR), annotated for text anonymization evaluation.
Each sample includes span-level entity mentions from multiple annotators.

Dataset schema (HuggingFace):
  - text: raw court case document
  - annotations: dict of annotator_name → {entity_mentions: [...]}
  - meta: {applicant, articles, countries, ...}
  - task: annotation task description

Each entity mention:
  - start_offset / end_offset: character positions
  - entity_type: CODE, PERSON, LOC, ORG, DATETIME, DEM, MISC
  - identifier_type: DIRECT (must mask), QUASI (should mask), NO_MASK (safe)
  - span_text: the actual text span
  - confidential_status: NOT_CONFIDENTIAL / CONFIDENTIAL

Reference:
  https://huggingface.co/datasets/mattmdjaga/text-anonymization-benchmark-train

Usage:
  python benchmark/download_tab_dataset.py
  python benchmark/download_tab_dataset.py --annotator majority
  python benchmark/download_tab_dataset.py --split test --max-samples 100
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "datasets" / "tab"
DEFAULT_DATASET_NAME = "mattmdjaga/text-anonymization-benchmark-train"
DEFAULT_SPLIT = "train"
DEFAULT_ANNOTATOR = "annotator1"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Download the Text Anonymization Benchmark (TAB) dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              # download train split to datasets/tab/
  %(prog)s --split test                 # download test split
  %(prog)s --annotator majority         # use majority vote across annotators
  %(prog)s --output ./tab_data          # custom output directory
  %(prog)s --format jsonl               # export as JSONL instead of JSON
        """,
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET_NAME,
        help=f"HuggingFace dataset name (default: {DEFAULT_DATASET_NAME})",
    )
    parser.add_argument(
        "--split",
        default=DEFAULT_SPLIT,
        help=f"Dataset split to download (default: {DEFAULT_SPLIT})",
    )
    parser.add_argument(
        "--annotator",
        default=DEFAULT_ANNOTATOR,
        help=(
            "Which annotator to use as ground truth. "
            "Options: 'annotator1' (default), 'annotator2', ..., "
            "'majority' (entities agreed by >=2 annotators), "
            "'union' (all entities from all annotators)"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--format",
        choices=["json", "jsonl"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=0,
        help="Maximum samples to download (0 = all, for quick testing)",
    )
    parser.add_argument(
        "--identifier-type",
        choices=["all", "direct", "direct+quasi"],
        default="all",
        help="Filter entities by identifier_type (default: all)",
    )
    return parser.parse_args()


def download_dataset(dataset_name: str, split: str) -> list[dict]:
    """Download dataset from HuggingFace Hub."""
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: 'datasets' library not installed. Install with: pip install datasets")
        sys.exit(1)

    print(f"Downloading dataset: {dataset_name} (split={split}) ...")
    dataset = load_dataset(dataset_name, split=split)
    print(f"Downloaded {len(dataset)} samples.")
    return [dict(row) for row in dataset]  # type: ignore[arg-type]


def extract_entities_from_annotations(
    annotations: dict,
    annotator_mode: str,
    identifier_filter: str,
) -> list[dict]:
    """Extract entity mentions from TAB annotations.

    Args:
        annotations: dict of annotator_name -> {"entity_mentions": [...]} | None.
        annotator_mode: which annotator(s) to use.
        identifier_filter: filter by identifier_type.

    Returns:
        list of entity dicts with 'text', 'type', 'start', 'end', 'identifier_type'.
    """
    if not annotations:
        return []

    # Collect mentions from all non-None annotators
    all_mentions: dict[str, list[dict]] = {}
    for name, data in annotations.items():
        if data is None or not isinstance(data, dict):
            continue
        mentions = data.get("entity_mentions", [])
        if mentions:
            all_mentions[name] = mentions

    if not all_mentions:
        return []

    if annotator_mode == "majority":
        return _majority_vote_entities(all_mentions, identifier_filter)
    elif annotator_mode == "union":
        return _union_entities(all_mentions, identifier_filter)
    else:
        # Single annotator mode (e.g. 'annotator1')
        mentions = all_mentions.get(annotator_mode, [])
        if not mentions:
            # Fallback to first available annotator
            first = next(iter(all_mentions.keys()))
            mentions = all_mentions[first]
        return _normalize_mentions(mentions, identifier_filter)


def _normalize_mentions(
    mentions: list[dict],
    identifier_filter: str,
) -> list[dict]:
    """Convert raw TAB mention dicts to normalized format."""
    entities: list[dict] = []
    for m in mentions:
        identifier_type = m.get("identifier_type", "DIRECT")

        # Apply identifier_type filter
        if identifier_filter == "direct" and identifier_type != "DIRECT":
            continue
        if identifier_filter == "direct+quasi" and identifier_type not in ("DIRECT", "QUASI"):
            continue

        entities.append({
            "text": m["span_text"],
            "type": m["entity_type"],
            "start": m["start_offset"],
            "end": m["end_offset"],
            "identifier_type": identifier_type,
        })

    # Sort by start position
    entities.sort(key=lambda e: (e["start"], -e["end"]))
    return entities


def _union_entities(
    all_mentions: dict[str, list[dict]],
    identifier_filter: str,
) -> list[dict]:
    """Combine entities from all annotators, removing exact duplicates."""
    seen: set[tuple[int, int, str]] = set()
    all_entities: list[dict] = []

    for mentions in all_mentions.values():
        normalized = _normalize_mentions(mentions, identifier_filter)
        for ent in normalized:
            key = (ent["start"], ent["end"], ent["type"])
            if key not in seen:
                seen.add(key)
                all_entities.append(ent)

    all_entities.sort(key=lambda e: (e["start"], -e["end"]))
    return all_entities


def _majority_vote_entities(
    all_mentions: dict[str, list[dict]],
    identifier_filter: str,
) -> list[dict]:
    """Keep entities that at least 2 annotators agree on.

    Two annotators agree if their entity spans overlap (IoU >= 0.5)
    and have the same entity_type.
    """
    # Flatten all mentions with annotator tag
    tagged: list[tuple[str, dict]] = []
    for annotator, mentions in all_mentions.items():
        for m in mentions:
            tagged.append((annotator, m))

    if len(all_mentions) < 2:
        # Only one annotator — return all their entities
        mentions = next(iter(all_mentions.values()))
        return _normalize_mentions(mentions, identifier_filter)

    # Group mentions into clusters by overlap
    clusters: list[list[tuple[str, dict]]] = []

    for annotator, mention in tagged:
        m_start = mention["start_offset"]
        m_end = mention["end_offset"]
        m_type = mention["entity_type"]

        matched = False
        for cluster in clusters:
            for _, existing in cluster:
                e_start = existing["start_offset"]
                e_end = existing["end_offset"]
                e_type = existing["entity_type"]

                # Compute IoU
                overlap_start = max(m_start, e_start)
                overlap_end = min(m_end, e_end)
                if overlap_start >= overlap_end:
                    continue
                overlap = overlap_end - overlap_start
                union = (m_end - m_start) + (e_end - e_start) - overlap
                iou = overlap / max(union, 1)

                if iou >= 0.5 and m_type == e_type:
                    cluster.append((annotator, mention))
                    matched = True
                    break
            if matched:
                break

        if not matched:
            clusters.append([(annotator, mention)])

    # Keep clusters with >= 2 unique annotators
    entities: list[dict] = []
    for cluster in clusters:
        unique_annotators = set(a for a, _ in cluster)
        if len(unique_annotators) >= 2:
            # Use the first mention's span as representative
            _, best = cluster[0]
            identifier_type = best.get("identifier_type", "DIRECT")

            if identifier_filter == "direct" and identifier_type != "DIRECT":
                continue
            if identifier_filter == "direct+quasi" and identifier_type not in ("DIRECT", "QUASI"):
                continue

            entities.append({
                "text": best["span_text"],
                "type": best["entity_type"],
                "start": best["start_offset"],
                "end": best["end_offset"],
                "identifier_type": identifier_type,
            })

    entities.sort(key=lambda e: (e["start"], -e["end"]))
    return entities


def normalize_sample(
    row: dict,
    idx: int,
    annotator_mode: str,
    identifier_filter: str,
) -> dict:
    """Normalize a TAB dataset row into a uniform schema.

    Output schema:
      {
        "id": str,
        "text": str,
        "applicant": str,
        "entities": [
          {"text": str, "type": str, "start": int, "end": int, "identifier_type": str}
        ]
      }
    """
    text = str(row.get("text", ""))
    doc_id = str(row.get("doc_id", idx))
    meta = row.get("meta", {})
    applicant = meta.get("applicant", "") if isinstance(meta, dict) else ""
    annotations = row.get("annotations", {})

    entities = extract_entities_from_annotations(
        annotations, annotator_mode, identifier_filter
    )

    return {
        "id": doc_id,
        "text": text,
        "applicant": applicant,
        "entities": entities,
    }


def save_dataset(
    samples: list[dict],
    output_dir: Path,
    fmt: str = "json",
) -> Path:
    """Save dataset to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "jsonl":
        output_path = output_dir / "tab_dataset.jsonl"
        with open(output_path, "w", encoding="utf-8") as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    else:
        output_path = output_dir / "tab_dataset.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=2)

    return output_path


def print_statistics(samples: list[dict]) -> None:
    """Print dataset statistics."""
    total = len(samples)
    total_entities = sum(len(s["entities"]) for s in samples)
    texts_with_entities = sum(1 for s in samples if s["entities"])

    # Count entity types and identifier types
    entity_type_counts: Counter = Counter()
    identifier_type_counts: Counter = Counter()
    for s in samples:
        for e in s["entities"]:
            entity_type_counts[e["type"]] += 1
            identifier_type_counts[e.get("identifier_type", "?")] += 1

    print(f"\n===== Dataset Statistics =====")
    print(f"Total samples:            {total}")
    print(f"Samples with entities:    {texts_with_entities}")
    print(f"Samples without entities: {total - texts_with_entities}")
    print(f"Total entities:           {total_entities}")
    print(f"Avg entities/sample:      {total_entities / max(total, 1):.2f}")
    print(f"\nEntity type distribution:")
    for etype, count in entity_type_counts.most_common():
        pct = 100 * count / max(total_entities, 1)
        print(f"  {etype:<20} {count:>6} ({pct:5.1f}%)")
    print(f"\nIdentifier type distribution:")
    for itype, count in identifier_type_counts.most_common():
        pct = 100 * count / max(total_entities, 1)
        print(f"  {itype:<20} {count:>6} ({pct:5.1f}%)")
    print("==============================\n")


def main() -> None:
    """Download TAB dataset and save to disk."""
    args = parse_args()

    rows = download_dataset(args.dataset, args.split)

    if args.max_samples > 0 and len(rows) > args.max_samples:
        rows = rows[: args.max_samples]
        print(f"Truncated to {args.max_samples} samples.")

    samples = [
        normalize_sample(row, idx, args.annotator, args.identifier_type)
        for idx, row in enumerate(rows)
    ]

    output_path = save_dataset(samples, args.output, args.format)
    print(f"Dataset saved to: {output_path}")

    print_statistics(samples)


if __name__ == "__main__":
    main()
