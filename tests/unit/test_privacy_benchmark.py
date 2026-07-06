"""Tests for privacy benchmark layer isolation."""

from unittest.mock import AsyncMock, patch

import pytest

from benchmark.privacy_benchmark import (
    analyze_detection_layer_breakdown,
    filter_samples_by_identifier,
)


@pytest.mark.asyncio
async def test_layer_one_does_not_run_ner() -> None:
    samples = [{"id": "sample-1", "text": "plain text", "entities": []}]

    with patch(
        "benchmark.privacy_benchmark._cached_ner_detect",
        new=AsyncMock(return_value=[]),
    ) as ner_detect:
        result = await analyze_detection_layer_breakdown(samples, num_layers=1)

    ner_detect.assert_not_awaited()
    assert result["ner_pct"] == 0.0

def test_filter_samples_by_direct_identifier() -> None:
    samples = [
        {
            "id": "sample-1",
            "text": "example",
            "entities": [
                {"text": "Alice", "identifier_type": "DIRECT"},
                {"text": "2026", "identifier_type": "QUASI"},
                {"text": "Paris", "identifier_type": "NO_MASK"},
            ],
        }
    ]

    filtered = filter_samples_by_identifier(samples, "direct")

    assert [e["text"] for e in filtered[0]["entities"]] == ["Alice"]
