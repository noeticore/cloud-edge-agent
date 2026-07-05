"""Privacy masking benchmark — evaluate detection & sanitization against TAB dataset.

Evaluates the privacy detection + sanitization pipeline against the
Text Anonymization Benchmark (TAB) dataset from ECHR court cases.

TAB provides span-level entity mentions with:
  - entity_type: CODE, PERSON, LOC, ORG, DATETIME, DEM, MISC
  - identifier_type: DIRECT (must mask), QUASI (should mask), NO_MASK (safe)

Metrics:
  - Entity-level: Precision, Recall, F1 per entity type (micro/macro avg)
  - Span-level: exact span match (strict) and partial overlap (lenient)
  - Identifier-type breakdown: how well does the system detect DIRECT vs QUASI?
  - Privacy-level classification accuracy (S1/S2/S3 vs has_entities)
  - Detection layer analysis (regex vs keyword vs SLM hit rates)
  - Sanitization: replacement rate, placeholder consistency

Layer control:
  --layers 1   Regex only
  --layers 2   Regex + NER (Presidio) — default
  --layers 3   Regex + NER + SLM (full pipeline)

Usage:
  python benchmark/privacy_benchmark.py --max-samples 100
  python benchmark/privacy_benchmark.py --layers 1 --max-samples 100
  python benchmark/privacy_benchmark.py --layers 3 --max-samples 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.domain.privacy.privacy import PrivacyDetection, PrivacyLevel, SensitiveEntity
from app.services.privacy_engine import (
    RegexSanitizer,
    _keyword_detect,
    _ner_detect,
    _regex_detect,
    _slm_judge,
)
from app.domain.llm.llm_client import LLMClient

# ---------------------------------------------------------------------------
# Entity type mapping: TAB labels → our detector labels
# ---------------------------------------------------------------------------

# Actual TAB entity types (from annotations)
TAB_ENTITY_TYPES = [
    "CODE", "PERSON", "LOC", "ORG", "DATETIME", "DEM", "MISC", "QUANTITY",
]

# Map TAB entity types → prediction entity type (for FN counting alignment).
# Must align with OURS_TO_TAB so GT FNs and prediction TPs use the same type key.
TAB_TO_OURS: dict[str, str] = {
    "PERSON":    "PERSON",        # Presidio returns "PERSON"
    "CODE":      "ID_CARD",       # CODE = identifiers (case numbers, etc.)
    "LOC":       "LOCATION",      # Presidio returns "LOCATION"
    "ORG":       "ORGANIZATION",  # Presidio returns "ORGANIZATION"
    "DATETIME":  "DATE_TIME",     # Presidio returns "DATE_TIME"
    "DEM":       "MISC",          # demonym/nationality
    "MISC":      "MISC",
    "QUANTITY":  "MISC",
}

# Reverse: our/presidio entity types → which TAB types count as match
OURS_TO_TAB: dict[str, list[str]] = {
    # Regex types
    "PHONE":       ["CODE"],
    "ID_CARD":     ["CODE"],
    "EMAIL":       ["CODE"],
    "BANK_CARD":   ["CODE"],
    "IP_ADDRESS":  ["CODE"],
    # Keyword types
    "NAME":        ["PERSON", "ORG"],
    "ADDRESS":     ["LOC"],
    "FINANCIAL":   ["MISC"],
    "MEDICAL":     ["MISC"],
    # Presidio NER types
    "PERSON":       ["PERSON"],
    "LOCATION":     ["LOC", "ORG"],        # Presidio conflates locations & organizations
    "ORGANIZATION": ["ORG"],
    "DATE_TIME":    ["DATETIME"],
    "EMAIL_ADDRESS": ["CODE"],
    "PHONE_NUMBER":  ["CODE"],
    "URL":           ["MISC"],
    "CREDIT_CARD":   ["CODE"],
    "CRYPTO":        ["MISC"],
    "IBAN_CODE":     ["CODE"],
    "US_SSN":        ["CODE"],
    "US_DRIVER_LICENSE": ["CODE"],
    "AGE":           ["MISC"],
    "DATE":          ["DATETIME"],
    "TIME":          ["DATETIME"],
    "NRP":           ["DEM", "MISC"],      # Nationality/religious/political group
}

# Presidio type → our internal type (for aggregation)
PRESIDIO_TO_OURS: dict[str, str] = {
    "PERSON": "NAME",
    "LOCATION": "ADDRESS",
    "ORGANIZATION": "NAME",
    "DATE_TIME": "MISC",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "URL": "MISC",
    "CREDIT_CARD": "BANK_CARD",
    "CRYPTO": "FINANCIAL",
    "IBAN_CODE": "BANK_CARD",
    "US_SSN": "ID_CARD",
    "US_DRIVER_LICENSE": "ID_CARD",
    "AGE": "MISC",
    "DATE": "MISC",
    "TIME": "MISC",
    "NRP": "MISC",
}

OUR_ENTITY_TYPES = [
    "PHONE", "ID_CARD", "EMAIL", "BANK_CARD", "IP_ADDRESS",
    "NAME", "ADDRESS", "FINANCIAL", "MEDICAL", "MISC",
    # Presidio types (for per-type breakdown)
    "PERSON", "LOCATION", "ORGANIZATION", "DATE_TIME",
]


_NER_RESULT_CACHE: dict[str, list[SensitiveEntity]] = {}


async def _cached_ner_detect(text: str) -> list[SensitiveEntity]:
    """Reuse NER output between scoring and layer-breakdown passes."""
    if text not in _NER_RESULT_CACHE:
        _NER_RESULT_CACHE[text] = await _ner_detect(text)
    return _NER_RESULT_CACHE[text]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def filter_samples_by_identifier(
    samples: list[dict[str, Any]], identifier_filter: str
) -> list[dict[str, Any]]:
    """Filter normalized entities by TAB identifier type."""
    allowed = {
        "direct": {"DIRECT"},
        "direct+quasi": {"DIRECT", "QUASI"},
    }.get(identifier_filter)

    if allowed is None:
        return samples

    return [
        {
            **sample,
            "entities": [
                entity
                for entity in sample.get("entities", [])
                if entity.get("identifier_type") in allowed
            ],
        }
        for sample in samples
    ]


def load_dataset_from_file(filepath: Path) -> list[dict[str, Any]]:
    """Load a pre-downloaded TAB dataset from disk.

    Expected format (one JSON/JSONL per sample):
      {
        "id": str,
        "text": str,
        "applicant": str,
        "entities": [
          {"text": str, "type": str, "start": int, "end": int, "identifier_type": str}
        ]
      }
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Dataset file not found: {filepath}")

    if filepath.suffix == ".jsonl":
        samples: list[dict[str, Any]] = []
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    samples.append(json.loads(line))
        return samples

    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    raise ValueError(f"Unexpected JSON structure in {filepath}")


def load_dataset_from_huggingface(
    dataset_name: str = "mattmdjaga/text-anonymization-benchmark-train",
    split: str = "train",
    max_samples: int = 0,
    annotator: str = "annotator1",
    identifier_filter: str = "all",
) -> list[dict[str, Any]]:
    """Load TAB dataset directly from HuggingFace.

    Args:
        dataset_name: HuggingFace dataset identifier.
        split: dataset split name.
        max_samples: maximum samples to load (0 = all).
        annotator: which annotator to use ('annotator1', 'majority', 'union').
        identifier_filter: filter by identifier_type ('all', 'direct', 'direct+quasi').

    Returns:
        list of normalized sample dicts.
    """
    from datasets import load_dataset

    # Reuse the download script's extraction logic
    from benchmark.download_tab_dataset import extract_entities_from_annotations

    dataset = load_dataset(dataset_name, split=split)
    if max_samples > 0:
        dataset = dataset.select(range(min(max_samples, len(dataset))))

    samples: list[dict[str, Any]] = []
    for idx, row in enumerate(dataset):
        row_dict = dict(row)
        text = str(row_dict.get("text", ""))
        doc_id = str(row_dict.get("doc_id", idx))
        meta = row_dict.get("meta", {})
        applicant = meta.get("applicant", "") if isinstance(meta, dict) else ""
        annotations = row_dict.get("annotations", {})

        entities = extract_entities_from_annotations(
            annotations, annotator, identifier_filter
        )

        samples.append({
            "id": doc_id,
            "text": text,
            "applicant": applicant,
            "entities": entities,
        })

    return samples


# ---------------------------------------------------------------------------
# Entity metrics computation
# ---------------------------------------------------------------------------


def _normalize_pred_type(entity_type: str) -> str:
    """Normalize a prediction entity type to a known category for metric counting."""
    if entity_type in OUR_ENTITY_TYPES:
        return entity_type
    return PRESIDIO_TO_OURS.get(entity_type, "MISC")


def compute_entity_metrics(
    ground_truth: list[dict[str, Any]],
    predictions: list[SensitiveEntity],
) -> dict[str, Any]:
    """Compute precision/recall/F1 for entity detection using lenient matching.

    Metrics are reported by TAB ground-truth type (PERSON, CODE, LOC, etc.)
    since prediction types (Presidio, regex, keywords) use different taxonomies.
    This ensures TP and FN are counted in the same bucket.

    Args:
        ground_truth: list of GT entity dicts with 'text','type','start','end'.
        predictions: list of SensitiveEntity from our detector.

    Returns:
        dict with per-type metrics by TAB type + overall counts.
    """
    # Use TAB entity types as the canonical metric axis
    per_type: dict[str, dict[str, int]] = {
        etype: {"tp": 0, "fp": 0, "fn": 0} for etype in TAB_ENTITY_TYPES
    }

    matched_gt: set[int] = set()

    # For each prediction, find the best unmatched GT
    for pred in predictions:
        compatible_tab_types = OURS_TO_TAB.get(pred.entity_type, [])
        if not compatible_tab_types:
            # Prediction type doesn't match any TAB type — skip (no FP by TAB type)
            continue

        best_match: tuple[int, float] | None = None  # (gt_idx, overlap_chars)
        for gi, gt in enumerate(ground_truth):
            if gi in matched_gt:
                continue
            if gt["type"] not in compatible_tab_types:
                continue

            overlap_start = max(pred.start, gt["start"])
            overlap_end = min(pred.end, gt["end"])
            if overlap_start < overlap_end:
                overlap = float(overlap_end - overlap_start)
                if best_match is None or overlap > best_match[1]:
                    best_match = (gi, overlap)

        if best_match is not None:
            gt_type = ground_truth[best_match[0]]["type"]
            per_type[gt_type]["tp"] += 1
            matched_gt.add(best_match[0])
        else:
            # FP: map prediction type to TAB type for counting
            fallback_tab = compatible_tab_types[0]
            per_type[fallback_tab]["fp"] += 1

    # Unmatched GT entities → FN by their TAB type
    for gi, gt in enumerate(ground_truth):
        if gi not in matched_gt:
            per_type[gt["type"]]["fn"] += 1

    # Compute scores per TAB type
    results: dict[str, Any] = {"per_type": {}, "overall": {}}
    total_tp, total_fp, total_fn = 0, 0, 0

    for etype, counts in per_type.items():
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        total_tp += tp
        total_fp += fp
        total_fn += fn

        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 0.001)

        results["per_type"][etype] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }

    micro_p = total_tp / max(total_tp + total_fp, 1)
    micro_r = total_tp / max(total_tp + total_fn, 1)
    micro_f1 = 2 * micro_p * micro_r / max(micro_p + micro_r, 0.001)

    results["overall"] = {
        "tp": total_tp, "fp": total_fp, "fn": total_fn,
        "precision": round(micro_p, 4),
        "recall": round(micro_r, 4),
        "f1": round(micro_f1, 4),
    }
    results["matched_gt"] = len(matched_gt)
    results["total_gt"] = len(ground_truth)
    results["total_pred"] = len(predictions)

    # Per-prediction-type breakdown (for layer analysis)
    pred_type_counts: dict[str, dict[str, int]] = {}
    for pred in predictions:
        ptype = pred.entity_type
        if ptype not in pred_type_counts:
            pred_type_counts[ptype] = {"total": 0, "matched": 0}
        pred_type_counts[ptype]["total"] += 1
    for gi in matched_gt:
        # Find which prediction matched this GT (re-run lightweight)
        for pred in predictions:
            compatible = OURS_TO_TAB.get(pred.entity_type, [])
            gt = ground_truth[gi]
            if gt["type"] not in compatible:
                continue
            if max(pred.start, gt["start"]) < min(pred.end, gt["end"]):
                ptype = pred.entity_type
                if ptype not in pred_type_counts:
                    pred_type_counts[ptype] = {"total": 0, "matched": 0}
                pred_type_counts[ptype]["matched"] += 1
                break

    results["pred_type_breakdown"] = {
        ptype: {
            "total": counts["total"],
            "matched": counts["matched"],
            "match_rate": round(counts["matched"] / max(counts["total"], 1), 4),
        }
        for ptype, counts in sorted(pred_type_counts.items(), key=lambda x: -x[1]["total"])
    }

    return results


def compute_strict_span_metrics(
    ground_truth: list[dict[str, Any]],
    predictions: list[SensitiveEntity],
) -> dict[str, float]:
    """Compute exact span match ratio (strict).

    A prediction matches iff its (start, end) exactly equals a GT span
    AND the entity types are compatible.
    """
    gt_spans = {(g["start"], g["end"], g["type"]) for g in ground_truth}

    exact_matched = 0
    for pred in predictions:
        compatible = OURS_TO_TAB.get(pred.entity_type, [])
        pred_key = (pred.start, pred.end)
        for gt_start, gt_end, gt_type in gt_spans:
            if pred_key == (gt_start, gt_end) and gt_type in compatible:
                exact_matched += 1
                break

    return {
        "overall_exact_match": round(exact_matched / max(len(predictions), 1), 4),
        "gt_coverage": round(exact_matched / max(len(ground_truth), 1), 4),
        "exact_matched": exact_matched,
        "total_pred": len(predictions),
        "total_gt": len(ground_truth),
    }


def compute_identifier_type_breakdown(
    ground_truth: list[dict[str, Any]],
    predictions: list[SensitiveEntity],
) -> dict[str, Any]:
    """Break down detection performance by identifier_type (DIRECT/QUASI/NO_MASK).

    Args:
        ground_truth: GT entities with 'identifier_type' field.
        predictions: detected entities.

    Returns:
        dict with per-identifier-type recall and counts.
    """
    gt_by_id_type: dict[str, list[dict]] = {"DIRECT": [], "QUASI": [], "NO_MASK": []}
    for gt in ground_truth:
        id_type = gt.get("identifier_type", "DIRECT")
        if id_type not in gt_by_id_type:
            id_type = "DIRECT"
        gt_by_id_type[id_type].append(gt)

    result: dict[str, Any] = {}
    for id_type, gt_list in gt_by_id_type.items():
        if not gt_list:
            result[id_type] = {"count": 0, "matched": 0, "recall": None}
            continue

        matched = 0
        for gt in gt_list:
            for pred in predictions:
                pred_compat = OURS_TO_TAB.get(pred.entity_type, [])
                if gt["type"] not in pred_compat:
                    continue
                overlap_start = max(pred.start, gt["start"])
                overlap_end = min(pred.end, gt["end"])
                if overlap_start < overlap_end:
                    matched += 1
                    break

        result[id_type] = {
            "count": len(gt_list),
            "matched": matched,
            "recall": round(matched / max(len(gt_list), 1), 4),
        }

    return result


# ---------------------------------------------------------------------------
# Sanitization evaluation
# ---------------------------------------------------------------------------


async def evaluate_sanitization(
    text: str,
    entities: list[SensitiveEntity],
) -> dict[str, Any]:
    """Evaluate sanitization quality."""
    sanitizer = RegexSanitizer(mapping_store=None)

    if not entities:
        return {
            "entities_replaced": 0,
            "sanitized_length": len(text),
            "original_length": len(text),
            "length_change_pct": 0.0,
            "placeholder_count": 0,
        }

    result = await sanitizer.sanitize(text, entities, session_id="benchmark")
    placeholder_count = len(set(result.mapping.keys()))

    return {
        "entities_replaced": result.entities_replaced,
        "sanitized_length": len(result.sanitized_text),
        "original_length": len(text),
        "length_change_pct": round(
            100 * (len(result.sanitized_text) - len(text)) / max(len(text), 1), 2
        ),
        "placeholder_count": placeholder_count,
    }


# ---------------------------------------------------------------------------
# Privacy level classification accuracy
# ---------------------------------------------------------------------------


def evaluate_privacy_level_accuracy(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate privacy-level classification accuracy.

    A sample "has entities" if GT annotations exist → should be S2+.
    No GT entities → should be S1.

    Returns:
        dict with classification metrics.
    """
    correct = 0
    total = len(records)
    s1_correct, s1_total = 0, 0
    s2_correct, s2_total = 0, 0
    false_safe = 0   # has entities but classified S1 (dangerous)
    false_alarm = 0  # no entities but classified S2/S3

    for rec in records:
        has_entities = rec["gt_entity_count"] > 0
        predicted_level = rec["detection"]["level"]

        if has_entities:
            s2_total += 1
            if predicted_level in ("S2", "S3"):
                correct += 1
                s2_correct += 1
            else:
                false_safe += 1
        else:
            s1_total += 1
            if predicted_level == "S1":
                correct += 1
                s1_correct += 1
            else:
                false_alarm += 1

    return {
        "accuracy": round(correct / max(total, 1), 4),
        "total": total,
        "correct": correct,
        "s1_accuracy": round(s1_correct / max(s1_total, 1), 4) if s1_total else None,
        "s2plus_recall": round(s2_correct / max(s2_total, 1), 4) if s2_total else None,
        "false_safe_rate": round(false_safe / max(total, 1), 4),
        "false_alarm_rate": round(false_alarm / max(total, 1), 4),
        "false_safe_count": false_safe,
        "false_alarm_count": false_alarm,
    }


# ---------------------------------------------------------------------------
# Detection layer breakdown
# ---------------------------------------------------------------------------


async def analyze_detection_layer_breakdown(
    samples: list[dict[str, Any]],
    num_layers: int = 2,
    slm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """Analyze which detection layers fire and their hit rates."""
    results: dict[str, Any] = {
        "regex_only": 0,
        "ner_only": 0,
        "regex_and_ner": 0,
        "no_hit": 0,
        "total": len(samples),
        "num_layers": num_layers,
        "per_layer": {
            "regex": {"hits": 0, "total_entities_found": 0},
            "ner": {"hits": 0, "total_entities_found": 0},
        },
    }

    if num_layers >= 3:
        results["per_layer"]["slm"] = {"hits": 0, "s1_count": 0, "s2_count": 0, "s3_count": 0}
        results["slm_triggered"] = 0  # SLM only fires when regex+NER both miss

    for sample in samples:
        text = sample["text"]
        regex_entities = _regex_detect(text)
        ner_entities = await _cached_ner_detect(text) if num_layers >= 2 else []

        has_regex = bool(regex_entities)
        has_ner = bool(ner_entities)

        if has_regex and has_ner:
            results["regex_and_ner"] += 1
        elif has_regex:
            results["regex_only"] += 1
        elif has_ner:
            results["ner_only"] += 1
        else:
            results["no_hit"] += 1

        results["per_layer"]["regex"]["hits"] += 1 if has_regex else 0
        results["per_layer"]["regex"]["total_entities_found"] += len(regex_entities)
        results["per_layer"]["ner"]["hits"] += 1 if has_ner else 0
        results["per_layer"]["ner"]["total_entities_found"] += len(ner_entities)

        # SLM layer: only triggered when regex+NER both miss
        if num_layers >= 3 and not has_regex and not has_ner and slm_client is not None:
            results["slm_triggered"] += 1
            slm_result = await _slm_judge(text, slm_client)
            results["per_layer"]["slm"]["hits"] += 1
            if slm_result.level == PrivacyLevel.S2:
                results["per_layer"]["slm"]["s2_count"] += 1
            elif slm_result.level == PrivacyLevel.S3:
                results["per_layer"]["slm"]["s3_count"] += 1
            else:
                results["per_layer"]["slm"]["s1_count"] += 1

    total = max(len(samples), 1)
    results["regex_pct"] = round(100 * results["per_layer"]["regex"]["hits"] / total, 1)
    results["ner_pct"] = round(100 * results["per_layer"]["ner"]["hits"] / total, 1)
    results["no_hit_pct"] = round(100 * results["no_hit"] / total, 1)

    if num_layers >= 3 and "slm" in results["per_layer"]:
        results["slm_pct"] = round(100 * results["slm_triggered"] / total, 1)

    return results


# ---------------------------------------------------------------------------
# Layer-controlled detection
# ---------------------------------------------------------------------------


async def detect_with_layers(
    text: str,
    num_layers: int = 2,
    slm_client: LLMClient | None = None,
) -> PrivacyDetection:
    """Run privacy detection with explicit layer control.

    Unlike ThreeLayerPrivacyDetector which short-circuits (regex hit → return
    immediately), this collects entities from ALL active layers for benchmarking.

    Args:
        text: input text to analyze.
        num_layers: 1=regex only, 2=regex+keywords, 3=regex+keywords+SLM.
        slm_client: SLM judge client (required if num_layers >= 3).

    Returns:
        PrivacyDetection with combined entities and level.
    """
    all_entities: list[SensitiveEntity] = []

    # Layer 1: Regex (always active)
    if num_layers >= 1:
        regex_entities = _regex_detect(text)
        all_entities.extend(regex_entities)

    # Layer 2: NER (Presidio)
    if num_layers >= 2:
        ner_entities = await _cached_ner_detect(text)
        all_entities.extend(ner_entities)

    # Layer 3: SLM judge
    slm_level: PrivacyLevel | None = None
    slm_confidence: float = 0.0
    slm_reason: str = ""

    if num_layers >= 3 and slm_client is not None:
        slm_result = await _slm_judge(text, slm_client)
        slm_level = slm_result.level
        slm_confidence = slm_result.confidence
        slm_reason = slm_result.reason

    # Deduplicate entities by position (keep first occurrence)
    seen: set[tuple[int, int]] = set()
    deduped: list[SensitiveEntity] = []
    for e in all_entities:
        key = (e.start, e.end)
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    # Determine privacy level
    if all_entities:
        level = PrivacyLevel.S2
        _high_conf_types = {
            "PHONE", "ID_CARD", "EMAIL", "BANK_CARD", "IP_ADDRESS",
            "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD",
            "US_SSN", "US_DRIVER_LICENSE", "IBAN_CODE",
        }
        confidence = 0.95 if any(
            e.entity_type in _high_conf_types for e in all_entities
        ) else 0.7
        reason = f"Detected {len(deduped)} entities across layers 1-{num_layers}"
    elif num_layers >= 3 and slm_client is not None and slm_level is not None:
        level = slm_level
        confidence = slm_confidence
        reason = f"SLM judge: {slm_reason}"
    else:
        level = PrivacyLevel.S1
        confidence = 0.5
        reason = (
            f"No PII detected (layers 1-{num_layers})"
            if num_layers < 3
            else "No PII detected (layers 1-3, SLM available)"
        )

    return PrivacyDetection(
        level=level,
        confidence=confidence,
        entities=deduped,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------


async def run_benchmark(
    samples: list[dict[str, Any]],
    num_layers: int = 2,
) -> dict[str, Any]:
    """Run the full privacy benchmark.

    Args:
        samples: list of normalized sample dicts with 'text' and 'entities' keys.
        num_layers: detection layers to use (1, 2, or 3).

    Returns:
        dict with all benchmark metrics.
    """
    _NER_RESULT_CACHE.clear()

    slm_client: LLMClient | None = None
    if num_layers >= 3:
        from app.core.config.settings import get_settings
        from app.infrastructure.llm.client_factory import create_edge_llm_client

        settings = get_settings()
        slm_client = create_edge_llm_client(settings.edge_llm)
        print("SLM judge enabled (edge model)")

    records: list[dict[str, Any]] = []
    start_time = time.perf_counter()

    for idx, sample in enumerate(samples):
        text = sample["text"]
        gt_entities: list[dict[str, Any]] = sample.get("entities", [])

        # Run privacy detection with the specified layers
        detection = await detect_with_layers(text, num_layers, slm_client)
        pred_entities = detection.entities

        # Compute metrics
        entity_metrics = compute_entity_metrics(gt_entities, pred_entities)
        span_metrics = compute_strict_span_metrics(gt_entities, pred_entities)
        id_type_breakdown = compute_identifier_type_breakdown(gt_entities, pred_entities)
        sanitization = await evaluate_sanitization(text, pred_entities)

        records.append({
            "sample_id": sample["id"],
            "text_length": len(text),
            "gt_entity_count": len(gt_entities),
            "pred_entity_count": len(pred_entities),
            "detection": {
                "level": detection.level.value,
                "confidence": round(detection.confidence, 4),
                "reason": detection.reason,
            },
            "entity_metrics": entity_metrics,
            "span_metrics": span_metrics,
            "identifier_type_breakdown": id_type_breakdown,
            "sanitization": sanitization,
        })

        if (idx + 1) % 20 == 0:
            print(f"  Processed {idx + 1}/{len(samples)} samples...")

    elapsed = time.perf_counter() - start_time

    aggregate = _aggregate_metrics(records)
    privacy_level_metrics = evaluate_privacy_level_accuracy(records)
    layer_breakdown = await analyze_detection_layer_breakdown(
        samples, num_layers, slm_client
    )

    return {
        "metadata": {
            "benchmark": "privacy-masking-evaluation",
            "dataset": "Text Anonymization Benchmark (TAB)",
            "num_samples": len(samples),
            "num_samples_with_entities": sum(
                1 for r in records if r["gt_entity_count"] > 0
            ),
            "total_gt_entities": sum(r["gt_entity_count"] for r in records),
            "num_layers": num_layers,
            "elapsed_seconds": round(elapsed, 2),
            "avg_ms_per_sample": round(1000 * elapsed / max(len(samples), 1), 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "aggregate": aggregate,
        "privacy_level_accuracy": privacy_level_metrics,
        "layer_breakdown": layer_breakdown,
        "per_sample": records,
    }


def _aggregate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-sample metrics into overall scores."""
    total_tp, total_fp, total_fn = 0, 0, 0
    per_type_agg: dict[str, dict[str, int]] = {
        etype: {"tp": 0, "fp": 0, "fn": 0} for etype in TAB_ENTITY_TYPES
    }
    total_gt_entities = 0
    total_pred_entities = 0
    exact_match_scores: list[float] = []
    gt_coverage_scores: list[float] = []

    # Identifier type aggregation
    id_type_agg: dict[str, dict[str, int]] = {
        "DIRECT": {"count": 0, "matched": 0},
        "QUASI": {"count": 0, "matched": 0},
        "NO_MASK": {"count": 0, "matched": 0},
    }

    for rec in records:
        total_gt_entities += rec["gt_entity_count"]
        total_pred_entities += rec["pred_entity_count"]

        em = rec["entity_metrics"]
        overall = em["overall"]
        total_tp += overall["tp"]
        total_fp += overall["fp"]
        total_fn += overall["fn"]

        for etype in TAB_ENTITY_TYPES:
            pt = em["per_type"].get(etype, {"tp": 0, "fp": 0, "fn": 0})
            per_type_agg[etype]["tp"] += pt["tp"]
            per_type_agg[etype]["fp"] += pt["fp"]
            per_type_agg[etype]["fn"] += pt["fn"]

        sm = rec.get("span_metrics", {})
        exact_match_scores.append(sm.get("overall_exact_match", 0.0))
        gt_coverage_scores.append(sm.get("gt_coverage", 0.0))

        for id_type in ("DIRECT", "QUASI", "NO_MASK"):
            id_data = rec.get("identifier_type_breakdown", {}).get(id_type, {})
            if id_data:
                id_type_agg[id_type]["count"] += id_data.get("count", 0)
                id_type_agg[id_type]["matched"] += id_data.get("matched", 0)

    # Micro-averaged entity metrics
    micro_p = total_tp / max(total_tp + total_fp, 1)
    micro_r = total_tp / max(total_tp + total_fn, 1)
    micro_f1 = 2 * micro_p * micro_r / max(micro_p + micro_r, 0.001)

    # Per-type scores
    per_type_scores: dict[str, dict[str, Any]] = {}
    for etype, counts in per_type_agg.items():
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        p = tp / max(tp + fp, 1)
        r = tp / max(tp + fn, 1)
        f1 = 2 * p * r / max(p + r, 0.001)
        per_type_scores[etype] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }

    # Identifier-type recall
    id_type_scores: dict[str, Any] = {}
    for id_type, agg in id_type_agg.items():
        if agg["count"] > 0:
            id_type_scores[id_type] = {
                "count": agg["count"],
                "matched": agg["matched"],
                "recall": round(agg["matched"] / max(agg["count"], 1), 4),
            }
        else:
            id_type_scores[id_type] = {"count": 0, "matched": 0, "recall": None}

    # Sanitization aggregate
    avg_entities_replaced = sum(
        r["sanitization"]["entities_replaced"] for r in records
    ) / max(len(records), 1)
    avg_length_change = sum(
        r["sanitization"]["length_change_pct"] for r in records
    ) / max(len(records), 1)

    return {
        "entity_detection": {
            "micro_precision": round(micro_p, 4),
            "micro_recall": round(micro_r, 4),
            "micro_f1": round(micro_f1, 4),
            "total_gt_entities": total_gt_entities,
            "total_pred_entities": total_pred_entities,
            "per_type": per_type_scores,
        },
        "span_accuracy": {
            "mean_exact_match": round(
                sum(exact_match_scores) / max(len(exact_match_scores), 1), 4
            ),
            "mean_gt_coverage": round(
                sum(gt_coverage_scores) / max(len(gt_coverage_scores), 1), 4
            ),
        },
        "identifier_type_recall": id_type_scores,
        "sanitization": {
            "avg_entities_replaced_per_sample": round(avg_entities_replaced, 2),
            "avg_length_change_pct": round(avg_length_change, 2),
        },
        "detection_rate_pct": round(
            100
            * sum(1 for r in records if r["pred_entity_count"] > 0)
            / max(len(records), 1),
            1,
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Privacy masking benchmark against TAB dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --max-samples 100                       # Quick test (layers 1+2)
  %(prog)s --layers 1 --max-samples 100            # Regex only
  %(prog)s --layers 3 --max-samples 200            # Full pipeline (L1+L2+SLM)
  %(prog)s --data datasets/tab/tab_dataset.json    # Use downloaded data
  %(prog)s --annotator majority --max-samples 100  # Majority-vote GT
  %(prog)s --output results/my_eval.json           # Custom output
        """,
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Path to pre-downloaded TAB dataset (JSON/JSONL). "
             "If omitted, downloads from HuggingFace.",
    )
    parser.add_argument(
        "--dataset",
        default="mattmdjaga/text-anonymization-benchmark-train",
        help="HuggingFace dataset name",
    )
    parser.add_argument(
        "--split",
        default="train",
        help="Dataset split (default: train)",
    )
    parser.add_argument(
        "--annotator",
        default="annotator1",
        help="Annotator to use as GT: 'annotator1', 'majority', 'union' (default: annotator1)",
    )
    parser.add_argument(
        "--identifier-filter",
        choices=["all", "direct", "direct+quasi"],
        default="all",
        help="Filter GT entities by identifier_type (default: all)",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=0,
        help="Maximum samples to benchmark (0 = all).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: benchmark/results/privacy_benchmark_<ts>.json)",
    )
    parser.add_argument(
        "--layers",
        type=int,
        choices=[1, 2, 3],
        default=2,
        help="Detection layers to activate: 1=regex only, 2=regex+NER/Presidio (default), "
             "3=regex+NER+SLM",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress detail output",
    )
    return parser.parse_args()


def main() -> None:
    """Run the privacy benchmark."""
    args = parse_args()

    # Load dataset
    if args.data:
        print(f"Loading dataset from file: {args.data}")
        samples = load_dataset_from_file(args.data)
    else:
        print("Loading dataset from HuggingFace...")
        samples = load_dataset_from_huggingface(
            dataset_name=args.dataset,
            split=args.split,
            max_samples=args.max_samples,
            annotator=args.annotator,
            identifier_filter=args.identifier_filter,
        )

    samples = filter_samples_by_identifier(samples, args.identifier_filter)

    if args.max_samples > 0 and len(samples) > args.max_samples:
        samples = samples[: args.max_samples]

    print(f"Loaded {len(samples)} samples.")

    # Validate loaded data
    gt_count = sum(len(s["entities"]) for s in samples)
    samples_with_gt = sum(1 for s in samples if s["entities"])
    print(f"  Ground truth entities: {gt_count}")
    print(f"  Samples with entities: {samples_with_gt}/{len(samples)}")
    print()

    # Output path
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        results_dir = Path(__file__).resolve().parent / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        output_path = results_dir / f"privacy_benchmark_{ts}.json"

    # Run benchmark
    layer_labels = {1: "L1 (Regex)", 2: "L1+L2 (Regex+NER/Presidio)", 3: "L1+L2+L3 (Regex+NER+SLM)"}
    print("Running privacy benchmark...")
    if not args.quiet:
        print(f"  Annotator:  {args.annotator}")
        print(f"  Identifier filter: {args.identifier_filter}")
        print(f"  Layers:     {layer_labels[args.layers]}")
        print()

    results = asyncio.run(
        run_benchmark(samples=samples, num_layers=args.layers)
    )

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to: {output_path}")

    _print_summary(results)


def _print_summary(results: dict[str, Any]) -> None:
    """Print a human-readable benchmark summary."""
    agg = results["aggregate"]
    ed = agg["entity_detection"]
    pla = results["privacy_level_accuracy"]
    lb = results["layer_breakdown"]
    meta = results["metadata"]

    W = 62
    print("\n" + "=" * W)
    print("  PRIVACY MASKING BENCHMARK — RESULTS SUMMARY")
    print("=" * W)
    print(f"  Samples:           {meta['num_samples']}")
    print(f"  With GT entities:  {meta['num_samples_with_entities']}")
    print(f"  Total GT entities: {meta['total_gt_entities']}")
    print(f"  Layers active:     {meta['num_layers']}")
    print(f"  Elapsed:           {meta['elapsed_seconds']:.1f}s "
          f"({meta['avg_ms_per_sample']:.1f} ms/sample)")
    print()

    print("  --- Entity Detection (micro-avg) ---")
    print(f"  Precision:  {ed['micro_precision']:.2%}")
    print(f"  Recall:     {ed['micro_recall']:.2%}")
    print(f"  F1 Score:   {ed['micro_f1']:.2%}")
    print(f"  GT / Pred:  {ed['total_gt_entities']} / {ed['total_pred_entities']}")
    print()

    print("  --- Per-Type F1 Scores (by TAB ground-truth type) ---")
    for etype, scores in sorted(ed["per_type"].items(), key=lambda x: -x[1]["f1"]):
        bar = "█" * min(int(scores["f1"] * 20), 20)
        print(f"  {etype:<20} F1={scores['f1']:.2%}  {bar} (s={scores['support']})")
    print()

    print("  --- Span Accuracy ---")
    span = agg["span_accuracy"]
    print(f"  Exact match ratio:  {span['mean_exact_match']:.2%}")
    print(f"  GT coverage ratio:  {span['mean_gt_coverage']:.2%}")
    print()

    print("  --- Identifier-Type Recall ---")
    for id_type in ("DIRECT", "QUASI", "NO_MASK"):
        data = agg["identifier_type_recall"].get(id_type, {})
        if data.get("recall") is not None:
            print(f"  {id_type:<12} {data['recall']:.2%}  "
                  f"(matched {data['matched']}/{data['count']})")
    print()

    print("  --- Privacy Level Classification ---")
    if pla["accuracy"] is not None:
        print(f"  Accuracy:          {pla['accuracy']:.2%}")
    print(f"  False-safe:         {pla['false_safe_count']} "
          f"({pla['false_safe_rate']:.2%}) — missed PII (BAD)")
    print(f"  False-alarm:        {pla['false_alarm_count']} "
          f"({pla['false_alarm_rate']:.2%}) — over-cautious")
    print()

    print("  --- Detection Layer Breakdown ---")
    print(f"  Regex hits:   {lb['regex_pct']:.1f}% of samples")
    print(f"  NER hits:     {lb['ner_pct']:.1f}% of samples")
    if lb.get("slm_pct") is not None:
        print(f"  SLM triggered:{lb['slm_pct']:.1f}% of samples")
        slm_data = lb["per_layer"].get("slm", {})
        if slm_data:
            print(f"    → S1: {slm_data.get('s1_count', 0)}, "
                  f"S2: {slm_data.get('s2_count', 0)}, "
                  f"S3: {slm_data.get('s3_count', 0)}")
    print(f"  No hits:      {lb['no_hit_pct']:.1f}% of samples")
    print()

    print("  --- Sanitization ---")
    san = agg["sanitization"]
    print(f"  Avg replacements/sample: {san['avg_entities_replaced_per_sample']:.1f}")
    print(f"  Avg length change:       {san['avg_length_change_pct']:.1f}%")
    print()
    print(f"  Detection rate: {agg['detection_rate_pct']:.1f}% of samples")
    print("=" * W)


if __name__ == "__main__":
    main()
