"""河道风险领域规则的行为测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from river_recreation_risk.assessment import assess_entrance, evidence_calendar
from river_recreation_risk.history import review_decision
from river_recreation_risk.ledger import OperationsLedger
from river_recreation_risk.policies import select_policy
from river_recreation_risk.scheduling import evaluate_trip
from river_recreation_risk.service import RiverRiskService
from river_recreation_risk.topology import RiverIndex

DATA = Path(__file__).parents[1] / "data"
AS_OF = "2026-08-15T12:00"
TARGET = "2026-08-16T09:00"


def base_data(**overrides):
    data = {
        "segments": [
            {"segment_id": "R1", "entrances": ["E1"], "flow_direction": "downstream"},
        ],
        "samples": [],
        "weather": [],
        "activities": [],
        "notices": [],
        "policies": [{
            "policy_id": "p",
            "version": "test-v1",
            "effective_from": "2026-01-01T00:00",
            "threshold": 0.5,
            "freshness_hours": 72,
            "min_valid_samples": 1,
            "required_indicators": ["risk_value"],
            "max_receipt_hours": 24,
            "rain_alert_mm": 20,
            "rain_window_hours": 24,
        }],
        "decision_context": {"as_of": AS_OF, "target_at": TARGET},
    }
    data.update(overrides)
    return data


def valid_sample(**overrides):
    sample = {
        "sample_id": "s1",
        "segment_id": "R1",
        "collected_at": "2026-08-15T07:00",
        "received_at": "2026-08-15T09:00",
        "available_at": "2026-08-15T10:00",
        "coverage": "full",
        "quality": "valid",
        "risk_value": 0.2,
    }
    sample.update(overrides)
    return sample


def assess(data, entrance_id="E1", **kwargs):
    service = RiverRiskService(data)
    return service.assess_all(
        kwargs.pop("as_of", AS_OF),
        kwargs.pop("target_at", TARGET),
    )[entrance_id]


class EvidenceClassificationTest(unittest.TestCase):
    def test_explicit_below_threshold_supports_open(self):
        result = assess(base_data(samples=[valid_sample()]))
        self.assertEqual(result["evidence_state"], "explicit_below_threshold")
        self.assertEqual(result["recommended_action"], "continue_open")
        self.assertEqual(result["current_status"], "open")

    def test_no_sample_is_distinct_from_below_threshold(self):
        result = assess(base_data(samples=[]))
        self.assertEqual(result["evidence_state"], "no_sample")
        self.assertNotEqual(result["current_status"], "open")
        codes = {g["code"] for g in result["gaps"]}
        self.assertIn("no_sample", codes)

    def test_threshold_is_strict_below(self):
        result = assess(base_data(samples=[valid_sample(risk_value=0.5)]))
        self.assertEqual(result["evidence_state"], "explicit_above_threshold")
        self.assertEqual(result["recommended_action"], "hold")

    def test_pending_partial_and_preservation_are_not_full_samples(self):
        pending = assess(base_data(samples=[valid_sample(
            sample_id="pending", received_at=None, available_at=None,
            quality="pending", risk_value=None,
        )]))
        self.assertEqual(pending["evidence_state"], "only_unusable_or_pending_samples")
        self.assertIn("pending_result", {g["code"] for g in pending["gaps"]})

        partial_policy = [{
            **base_data()["policies"][0],
            "required_indicators": ["risk_value", "e_coli"],
        }]
        partial = assess(base_data(
            policies=partial_policy,
            samples=[valid_sample(indicator_coverage="partial", measured_indicators=["risk_value"])],
        ))
        self.assertNotEqual(partial["recommended_action"], "continue_open")
        self.assertIn("partial_indicators", {g["code"] for g in partial["gaps"]})

        spoiled = assess(base_data(samples=[valid_sample(
            preservation_status="exceeded", quality="invalid",
        )]))
        self.assertIn("preservation_failed", {g["code"] for g in spoiled["gaps"]})
        self.assertEqual(spoiled["evidence_state"], "only_unusable_or_pending_samples")

    def test_stale_sample_does_not_support_target_opening(self):
        old = valid_sample(
            collected_at="2026-08-10T07:00",
            received_at="2026-08-10T09:00",
            available_at="2026-08-10T10:00",
        )
        result = assess(base_data(samples=[old]))
        self.assertNotEqual(result["recommended_action"], "continue_open")
        self.assertIn("stale_sample", {g["code"] for g in result["gaps"]})


class SpatialAndWeatherTest(unittest.TestCase):
    def test_partial_coverage_does_not_apply_to_upstream_uncovered_entrance(self):
        data = base_data(
            segments=[{"segment_id": "R1", "entrances": ["E0", "E1", "E2"], "flow_direction": "downstream"}],
            samples=[valid_sample(coverage="partial", covered_entrances=["E1"], risk_value=0.9)],
        )
        e1 = assess(data, "E1")
        e0 = assess(data, "E0")
        e2 = assess(data, "E2")
        self.assertEqual(e1["recommended_action"], "hold")
        # E0 位于采样点上游，不能由下游高值反推。
        self.assertEqual(e0["evidence_state"], "no_sample")
        self.assertIn("spatial_coverage_missing", {g["code"] for g in e0["gaps"]})
        # E2 在有顺序/方向资料时可收到顺流污染提示。
        self.assertEqual(e2["recommended_action"], "hold")

    def test_full_segment_coverage_applies_to_all_segment_entrances_only(self):
        data = base_data(
            segments=[
                {"segment_id": "R1", "entrances": ["E1", "E2"], "flow_direction": "downstream"},
                {"segment_id": "R2", "entrances": ["E3"], "flow_direction": "downstream"},
            ],
            samples=[valid_sample(segment_id="R1", coverage="full", risk_value=0.9)],
        )
        self.assertEqual(assess(data, "E1")["recommended_action"], "hold")
        self.assertEqual(assess(data, "E2")["recommended_action"], "hold")
        self.assertEqual(assess(data, "E3")["evidence_state"], "no_sample")

    def test_adverse_result_propagates_downstream_not_upstream(self):
        index = RiverIndex.from_records(
            segments=[{"segment_id": "R1", "entrances": ["E1", "E2"], "flow_direction": "downstream"}],
            entrances=[
                {"entrance_id": "E1", "segment_id": "R1", "position_km": 0},
                {"entrance_id": "E2", "segment_id": "R1", "position_km": 4},
            ],
        )
        sample = valid_sample(entrance_id="E1", coverage="station", risk_value=0.9)
        policy = base_data()["policies"][0]
        e2 = assess_entrance(
            entrance_id="E2", index=index, samples=[sample], as_of=AS_OF,
            target_at=TARGET, policies=[policy],
        )
        self.assertEqual(e2["recommended_action"], "hold")
        self.assertTrue(any(
            s["relation"]["relation"] == "adverse_downstream_flow"
            for s in e2["supporting_samples"]
        ))

    def test_rain_without_post_rain_sample_blocks_open_and_calendar_keeps_no_sample_separate(self):
        weather = [{"observed_at": "2026-08-15T06:00", "rain_mm": 35, "water_level": 1.8}]
        before_rain = valid_sample()  # collected 07? Need before rain; override below.
        before_rain = valid_sample(collected_at="2026-08-14T07:00",
                                   received_at="2026-08-14T09:00",
                                   available_at="2026-08-14T10:00")
        data = base_data(weather=weather, samples=[before_rain])
        result = assess(data)
        self.assertFalse(result["post_rain_check"]["satisfied"])
        self.assertIn("missing_post_rain_sample", {g["code"] for g in result["gaps"]})
        self.assertNotEqual(result["recommended_action"], "continue_open")

        service = RiverRiskService(data)
        calendar = service.calendar("E1", "2026-08-15", "2026-08-16", AS_OF)
        states = {day["date"]: day["state"] for day in calendar}
        self.assertEqual(states["2026-08-15"], "no_sample")


    def test_active_high_risk_zone_overrides_low_point_sample(self):
        data = base_data(
            samples=[valid_sample()],
            risk_zones=[{"zone_id": "zone-1", "segment_id": "R1", "risk_level": "high",
                         "effective_from": "2026-08-15T00:00"}],
        )
        result = assess(data)
        self.assertEqual(result["recommended_action"], "hold")
        self.assertIn("risk_zone_restriction", {g["code"] for g in result["gaps"]})


    def test_missed_sampling_plan_is_not_counted_as_sample(self):
        data = base_data(
            sampling_plans=[{
                "plan_id": "plan-missed", "segment_id": "R1",
                "planned_for": "2026-08-15T08:00", "status": "planned",
            }],
            samples=[],
        )
        result = assess(data)
        self.assertIn("planned_sample_missing", {g["code"] for g in result["gaps"]})
        self.assertEqual(result["sampling_plans"][0]["timing_state"], "planned_without_actual_sample")


class ObservationsTest(unittest.TestCase):
    def test_feedback_and_volunteer_cannot_replace_lab_evidence(self):
        data = base_data(
            samples=[],
            visitor_feedback=[{"feedback_id": "f1", "entrance_id": "E1", "contact_at": AS_OF}],
            volunteer_observations=[{"observation_id": "v1", "entrance_id": "E1", "observed_at": AS_OF}],
        )
        result = assess(data)
        self.assertEqual(result["evidence_state"], "no_sample")
        self.assertFalse(result["visitor_feedback"][0]["can_replace_lab_test"])
        self.assertFalse(result["volunteer_observations"][0]["can_replace_lab_test"])
        self.assertIn("visitor_feedback_not_evidence", {g["code"] for g in result["gaps"]})


class PolicyVersionTest(unittest.TestCase):
    def test_selects_policy_effective_at_historical_time(self):
        policies = [
            {"version": "old", "effective_from": "2026-01-01T00:00", "effective_to": "2026-08-15T00:00", "threshold": 0.8},
            {"version": "new", "effective_from": "2026-08-15T00:00", "threshold": 0.5},
        ]
        old = select_policy(policies, "2026-08-14T15:00")
        new = select_policy(policies, "2026-08-15T12:00")
        self.assertEqual(old["version"], "old")
        self.assertEqual(old["threshold"], 0.8)
        self.assertEqual(new["version"], "new")
        self.assertNotEqual(old["fingerprint"], new["fingerprint"])


class SchedulingTest(unittest.TestCase):
    resources = [{
        "resource_type": "boat", "resource_id": "b1",
        "available_windows": [{"start": "2026-08-16T07:00", "end": "2026-08-16T12:00"}],
    }, {
        "resource_type": "staff", "resource_id": "p1",
        "available_windows": [{"start": "2026-08-16T07:00", "end": "2026-08-16T12:00"}],
    }]

    def test_confirmed_activity_conflict_is_visible_and_blocks_confirmation(self):
        trip = {"trip_id": "t1", "entrance_ids": ["E3"], "start": "2026-08-16T08:00",
                "end": "2026-08-16T10:00", "boat_id": "b1", "staff_ids": ["p1"]}
        activities = [{"activity_id": "a2", "entrance_id": "E3", "starts_at": "2026-08-16T09:00", "status": "confirmed"}]
        result = evaluate_trip(trip, self.resources, activities)
        self.assertFalse(result["can_confirm"])
        self.assertEqual(result["activity_conflicts"][0]["activity_id"], "a2")
        self.assertGreater(result["activity_conflicts"][0]["overlap_minutes"], 0)
        self.assertTrue(result["risk_impact"]["blocks_upgrade_to_open"])

    def test_clean_slot_can_confirm(self):
        trip = {"trip_id": "t1", "entrance_ids": ["E3"], "start": "2026-08-16T07:00",
                "end": "2026-08-16T08:00", "boat_id": "b1", "staff_ids": ["p1"]}
        result = evaluate_trip(trip, self.resources, [])
        self.assertTrue(result["resource_feasible"])
        self.assertTrue(result["can_confirm"])


class LedgerAndLabResultTest(unittest.TestCase):
    def test_sent_notice_and_completed_activity_are_immutable(self):
        ledger = OperationsLedger(
            notices=[{"notice_id": "n1", "entrance_id": "E1", "status": "sent", "version": "v1"}],
            activities=[{"activity_id": "a1", "entrance_id": "E1", "status": "sold"}],
        )
        with self.assertRaises(Exception):
            ledger.update_notice_draft("n1", {"version": "changed"})
        revision = ledger.issue_revision("n1", {"notice_id": "n2", "version": "v2"})
        self.assertEqual(revision["replaces_notice_id"], "n1")

        ledger.transition_activity("a1", "completed")
        with self.assertRaises(Exception):
            ledger.transition_activity("a1", "cancelled")

    def test_new_lab_result_changes_unconfirmed_only(self):
        data = base_data(
            samples=[valid_sample(quality="pending", risk_value=None, received_at=None, available_at=None)],
            arrangements=[
                {"arrangement_id": "arr-draft", "entrance_id": "E1", "status": "unconfirmed",
                 "recommended_action": "hold"},
                {"arrangement_id": "arr-confirmed", "entrance_id": "E1", "status": "confirmed",
                 "recommended_action": "hold"},
            ],
        )
        service = RiverRiskService(data)
        outcome = service.receive_lab_result({
            "sample_id": "s1",
            "received_at": "2026-08-15T12:30",
            "available_at": "2026-08-15T13:00",
            "quality": "valid",
            "risk_value": 0.1,
        })
        refresh = outcome["arrangement_refresh"]
        self.assertIn("arr-draft", refresh["updated_unconfirmed"])
        self.assertIn("arr-confirmed", refresh["confirmed_requiring_handling"])
        self.assertEqual(service.ledger.arrangements["arr-draft"]["recommended_action"], "continue_open")
        self.assertEqual(service.ledger.arrangements["arr-confirmed"]["recommended_action"], "hold")
        self.assertTrue(service.ledger.arrangements["arr-confirmed"]["risk_change_proposals"])


class RetrospectiveTest(unittest.TestCase):
    def test_later_hazard_makes_permissive_decision_false_safe(self):
        decision = {
            "decision_id": "d1", "entrance_id": "E1", "segment_id": "R1",
            "selected_action": "continue_open", "current_status": "open",
            "decided_at": TARGET, "basis": {"target_at": TARGET},
        }
        review = review_decision(decision, [{
            "observation_id": "o1", "entrance_id": "E1",
            "observed_at": "2026-08-16T12:00", "risk_value": 0.9,
        }])
        self.assertEqual(review["verdict"], "false_safe_or_missed_hazard")

    def test_missing_later_observation_is_unverifiable_not_correct(self):
        decision = {"decision_id": "d1", "entrance_id": "E1", "selected_action": "hold", "basis": {}}
        review = review_decision(decision, [])
        self.assertEqual(review["verdict"], "unverifiable")


class WorkspaceDataTest(unittest.TestCase):
    def test_synthetic_workspace_dashboard_shows_conflict_and_tracking(self):
        service = RiverRiskService(DATA)
        dashboard = service.dashboard()
        ids = {e["entrance_id"]: e for e in dashboard["entrances"]}
        self.assertEqual(ids["E-01"]["recommended_action"], "hold")
        self.assertEqual(ids["E-03"]["evidence_state"], "explicit_below_threshold")
        self.assertTrue(ids["E-03"]["gaps"])
        conflict_activities = {
            c["activity_id"]
            for trip in dashboard["trip_evaluations"]
            for c in trip["activity_conflicts"]
        }
        self.assertIn("a-002", conflict_activities)
        notice = dashboard["notices"][0]
        self.assertEqual(notice["current_delivery_state"], "delivered")
        self.assertIn("threshold-v1", notice["historical_basis_at_issue"]["policy_version"])


if __name__ == "__main__":
    unittest.main()
