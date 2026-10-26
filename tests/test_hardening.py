"""Tests for the Day-1 evening hardening (see docs/ANALYSIS.md)."""
import json
import threading
import time
import unittest

from fastapi import HTTPException

from labwatch import api
from labwatch.agent import run_investigation
from labwatch.baseline import RuleBasedClient
from labwatch.config import load_layout, load_runbook
from labwatch.detector import Detector, IncidentTracker
from labwatch.report import validate_report
from labwatch.simulator import Simulator
from labwatch.tools import Investigation

LAYOUT, RUNBOOK = load_layout(), load_runbook()
INCIDENT = {"incident_id": "INC-T", "opened_at": "t", "rooms": ["412"],
            "flags": [{"room": "412", "ts": "t", "reason": "out_of_range", "dp_pa": 1.2}]}


class Scripted:
    """Fake model: returns the given responses in order, then an empty final."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []

    def next(self, messages, tools):
        self.seen = list(messages)
        return self.responses.pop(0) if self.responses else {"final": {}}


class Raises:
    def next(self, messages, tools):
        raise TimeoutError("model API timed out")


def new_inv(ticks=30):
    sim = Simulator(LAYOUT)
    sim.run(ticks)
    return Investigation(sim, LAYOUT, RUNBOOK)


def call(**kw):
    return {"tool_calls": [{"id": "c1", **kw}]}


def run(*responses, max_repairs=2):
    client = Scripted(*responses)
    return run_investigation(client, new_inv(), INCIDENT, max_repairs=max_repairs), client


class ModelOutputTests(unittest.TestCase):
    def test_client_exception_ends_as_model_error(self):
        result = run_investigation(Raises(), new_inv(), INCIDENT)
        self.assertEqual(result.status, "model_error")
        self.assertIn("TimeoutError", result.errors[0])

    def test_missing_tool_name_is_sent_back(self):
        result, client = run(call(arguments={}))
        self.assertIn("missing 'name'", result.trace[0]["error"])
        tool_replies = [m["content"] for m in client.seen if m["role"] == "tool"]
        self.assertIn("missing 'name'", tool_replies[0]["error"])

    def test_non_dict_arguments_rejected(self):
        result, _ = run(call(name="get_layout", arguments=["412"]))
        self.assertIn("must be a JSON object", result.trace[0]["error"])

    def test_json_string_arguments_are_parsed(self):
        result, _ = run(call(name="get_layout", arguments='{"rooms": ["412"]}'))
        self.assertEqual(result.trace[0]["evidence_id"], "E1")

    def test_wrong_argument_types_become_tool_errors(self):
        for args in ({"asset": "412", "minutes": "abc"}, {"asset": ["412"]}, {"asset": "412", "minutes": None}):
            result, _ = run(call(name="query_metrics", arguments=args))
            self.assertIsNone(result.trace[0]["evidence_id"], args)
            self.assertTrue(result.trace[0]["error"], args)

    def test_malformed_tool_calls_count_against_repairs(self):
        result, _ = run(call(arguments={}), max_repairs=0)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.steps, 1)

    def test_response_without_calls_or_final(self):
        for resp in (None, [], "hello", {"thought": "hmm"}, {"tool_calls": "get_layout"}):
            result, _ = run(resp, max_repairs=0)
            self.assertEqual(result.status, "rejected", resp)

    def test_bad_finals_become_validation_errors(self):
        bad = ["not json", ["a", "list"], {}, {"root_cause": "AHU-4"},
               {"root_cause": {"asset": ["412"]}}, {"findings": ["claim"]},
               {"affected_rooms": "412"}, {"actions": ["N95_ENTRY"]},
               {"actions": [{"id": "INSPECT_AHU", "params": {"asset": ["AHU-4"]}}]},
               {"actions": [{"id": ["N95_ENTRY"], "params": []}]},
               {"status": ["diagnosed"], "root_cause": {"category": {}, "evidence": [{}]}}]
        for final in bad:
            result, _ = run({"final": final}, max_repairs=0)
            self.assertEqual(result.status, "rejected", final)
            self.assertTrue(result.errors, final)

    def test_bad_finals_are_sent_back_until_repairs_run_out(self):
        result, client = run({"final": "x"}, {"final": "x"}, {"final": "x"}, max_repairs=2)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.steps, 3)
        repairs = [m for m in client.seen if m["role"] == "user" and "failed validation" in m["content"]]
        self.assertEqual(len(repairs), 2)


def scoped_report(affected, actions):
    return {"status": "diagnosed", "confidence": "high", "affected_rooms": affected,
            "root_cause": {"category": "air_handler_failure", "asset": "AHU-4", "summary": "s", "evidence": ["E1"]},
            "findings": [], "ruled_out": [], "actions": actions}


class IncidentScopeTests(unittest.TestCase):
    def setUp(self):
        self.inv = new_inv()
        self.inv.call("get_layout", {"rooms": ["412"]})

    def errors(self, affected, actions, flagged=("412", "414", "418")):
        return validate_report(scoped_report(affected, actions), self.inv, list(flagged))[1]

    def test_valid_scoped_report_passes(self):
        self.assertEqual(self.errors(["412", "414"], [
            {"id": "N95_ENTRY", "params": {"rooms": ["412", "414"]}},
            {"id": "PREPARE_BACKUP_ROOM", "params": {"backup_room": "420"}}]), [])

    def test_action_rooms_must_be_affected(self):
        errors = self.errors(["412"], [{"id": "N95_ENTRY", "params": {"rooms": ["420"]}}])
        self.assertTrue(any("not in affected_rooms" in e for e in errors), errors)

    def test_affected_rooms_must_be_flagged(self):
        errors = self.errors(["412", "420"], [], flagged=["412"])
        self.assertTrue(any("'420' was not flagged" in e for e in errors), errors)

    def test_backup_room_cannot_be_affected(self):
        errors = self.errors(["412", "414"], [{"id": "PREPARE_BACKUP_ROOM", "params": {"backup_room": "414"}}])
        self.assertTrue(any("is one of the affected rooms" in e for e in errors), errors)

    def test_backup_room_must_be_isolation(self):
        errors = self.errors(["412"], [{"id": "PREPARE_BACKUP_ROOM", "params": {"backup_room": "422"}}])
        self.assertTrue(any("not an isolation room" in e for e in errors), errors)

    def test_backup_room_must_be_in_range(self):
        self.inv.sim.inject("ahu_failure", ahu="AHU-5")
        self.inv.sim.run(30)
        errors = self.errors(["412"], [{"id": "PREPARE_BACKUP_ROOM", "params": {"backup_room": "420"}}])
        self.assertTrue(any("not in range right now" in e for e in errors), errors)


def warm_detector(seed=1):
    sim, det = Simulator(LAYOUT, seed=seed), Detector(LAYOUT)
    for _ in range(120):
        det.update(sim.step())
    return sim, det


class DetectorTests(unittest.TestCase):
    def test_slow_drift_does_not_drag_baseline(self):
        sim, det = warm_detector()
        normal = sum(det.baseline["412"]) / len(det.baseline["412"])
        first = None
        for k in range(240):
            sim.ahus["AHU-4"].target_health = 1 - 0.003 * k
            flags = [f for f in det.update(sim.step()) if f["room"] == "412"]
            if flags and first is None:
                first = flags[0]
        baseline = sum(det.baseline["412"]) / len(det.baseline["412"])
        self.assertLess(abs(baseline - normal), 0.5)
        self.assertEqual(first["reason"], "drift")
        self.assertLess(first["dp_pa"], -2.5)  # caught while still in range

    def test_no_new_flags_while_recovering_from_repair(self):
        sim, det = warm_detector()
        sim.inject("ahu_failure", ahu="AHU-4")
        for _ in range(30):
            det.update(sim.step())
        sim.repair()
        for room in LAYOUT["rooms"]:
            det.reset_room(room)
        flags = [f for _ in range(80) for f in det.update(sim.step())]
        self.assertEqual(flags, [])
        self.assertEqual(det.flagged, set())

    def test_room_still_bad_after_repair_opens_new_incident(self):
        sim, det = warm_detector()
        tracker = IncidentTracker()
        sim.inject("ahu_failure", ahu="AHU-4")
        for _ in range(24):
            tracker.add(det.update(sim.step()))
        sim.inject("door_ajar", room="414")
        for _ in range(36):
            tracker.add(det.update(sim.step()))
        self.assertEqual(tracker.open["incident_id"], "INC-0001")
        sim.ahus["AHU-4"].target_health = 1.0   # AHU fixed; the door is still ajar
        for room in LAYOUT["rooms"]:
            det.reset_room(room)
        for _ in range(40):
            tracker.add(det.update(sim.step()))
        self.assertEqual(tracker.open["incident_id"], "INC-0002")
        self.assertEqual(tracker.open["rooms"], ["414"])


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class ApiTests(unittest.TestCase):
    def setUp(self):
        api.state.update(api.new_state())
        self.original_client = api.make_client
        self.addCleanup(setattr, api, "make_client", self.original_client)
        # Let any investigation this test queued finish before the next test patches make_client.
        self.addCleanup(lambda: api.INVESTIGATOR.submit(lambda: None).result(timeout=10))

    def incident(self):
        return next(iter(api.state["incidents"].values()), None)

    def start_ahu_failure(self):
        api.chaos("ahu_failure")
        for _ in range(60):
            api._tick()
            if self.incident():
                return self.incident()
        self.fail("no incident opened")

    def finished(self):
        return wait_for(lambda: self.incident()["status"] != "investigating")

    def test_failed_tick_is_logged_and_ticking_continues(self):
        sim = api.state["sim"]
        real_step = sim.step
        sim.step = lambda: 1 / 0
        with self.assertLogs("labwatch", "ERROR"):
            api._safe_tick()
        sim.step = real_step
        before = sim.now
        api._safe_tick()
        self.assertGreater(sim.now, before)

    def test_simulator_keeps_ticking_while_model_thinks(self):
        gate, calls = threading.Event(), []
        self.addCleanup(gate.set)

        class Slow(RuleBasedClient):
            def next(self, messages, tools):
                gate.wait(5)
                return super().next(messages, tools)

        def factory(layout, incident):
            calls.append(incident["incident_id"])
            return Slow(layout, incident)

        api.make_client = factory
        self.assertEqual(self.start_ahu_failure()["status"], "investigating")
        before = api.state["sim"].now
        for _ in range(20):
            api._tick()
        self.assertGreater(api.state["sim"].now, before)
        self.assertEqual(self.incident()["status"], "investigating")
        gate.set()
        self.assertTrue(self.finished())
        self.assertEqual(self.incident()["status"], "awaiting_approval")
        self.assertEqual(calls, ["INC-0001"])   # never investigated twice

    def test_investigation_exception_needs_human_review(self):
        def broken(layout, incident):
            raise RuntimeError("client misconfigured")

        api.make_client = broken
        with self.assertLogs("labwatch", "ERROR"):
            self.start_ahu_failure()
            self.assertTrue(self.finished())
        inc = self.incident()
        self.assertEqual(inc["status"], "needs_human_review")
        self.assertIn("client misconfigured", inc["errors"][0])

    def test_model_error_needs_human_review(self):
        api.make_client = lambda layout, incident: Raises()
        self.start_ahu_failure()
        self.assertTrue(self.finished())
        self.assertEqual(self.incident()["status"], "needs_human_review")
        self.assertEqual(self.incident()["agent_status"], "model_error")

    def test_notifications_never_carry_model_text(self):
        class Chatty(RuleBasedClient):
            def _report(self, results):
                report = super()._report(results)
                report["root_cause"]["summary"] = "Give patients antivirals and evacuate room 999."
                return report

        api.make_client = Chatty
        self.start_ahu_failure()
        self.assertTrue(self.finished())
        inc = api.approve("INC-0001")
        self.assertEqual(inc["ai_summary"]["label"], "AI summary (for supervisor review)")
        sent = api.notifications()
        self.assertEqual({n["summary"] for n in sent},
                         {"air handler failure at AHU-4; rooms 412, 414, 418 affected"})
        self.assertNotIn("antivirals", json.dumps(sent))
        for n in sent:
            self.assertTrue(set(n["actions"]) <= {a["text"] for a in inc["actions"]})

    def test_unknown_chaos_target_is_400(self):
        with self.assertRaises(HTTPException) as ctx:
            api.chaos("ahu_failure", target="AHU-9")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_repair_resets_detector(self):
        self.start_ahu_failure()
        self.assertTrue(api.state["detector"].flagged)
        api.chaos("repair")
        self.assertEqual(api.state["detector"].flagged, set())
        self.assertTrue(all(api.state["detector"].settling.values()))


if __name__ == "__main__":
    unittest.main()
