"""Unit tests for the policy routing engine."""

from app.domain.privacy.policy import (
    CollaborateMode,
    ComplexityLevel,
    PrivacyLevel,
    RouteDecision,
    route,
)


class TestRouteMatrix:
    """Test the routing decision matrix."""

    def test_s1_low_complexity_routes_to_edge(self) -> None:
        result = route(PrivacyLevel.S1, ComplexityLevel.L1)
        assert result.decision == RouteDecision.EDGE
        assert result.mode == CollaborateMode.DIRECT_LOCAL

    def test_s1_high_complexity_routes_to_cloud(self) -> None:
        result = route(PrivacyLevel.S1, ComplexityLevel.L5)
        assert result.decision == RouteDecision.CLOUD
        assert result.mode == CollaborateMode.DIRECT_CLOUD

    def test_s2_low_complexity_routes_to_edge(self) -> None:
        result = route(PrivacyLevel.S2, ComplexityLevel.L2)
        assert result.decision == RouteDecision.EDGE

    def test_s2_high_complexity_routes_to_sanitized_cloud(self) -> None:
        result = route(PrivacyLevel.S2, ComplexityLevel.L4)
        assert result.decision == RouteDecision.SANITIZED_CLOUD
        assert result.mode == CollaborateMode.SANITIZE_CLOUD

    def test_s3_high_complexity_routes_to_sanitized_cloud(self) -> None:
        result = route(PrivacyLevel.S3, ComplexityLevel.L5)
        assert result.decision == RouteDecision.SANITIZED_CLOUD
        assert result.mode == CollaborateMode.SANITIZE_CLOUD

    def test_s3_low_complexity_routes_to_edge(self) -> None:
        result = route(PrivacyLevel.S3, ComplexityLevel.L1)
        assert result.decision == RouteDecision.EDGE
        assert result.mode == CollaborateMode.DIRECT_LOCAL
