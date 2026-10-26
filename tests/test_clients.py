"""Model client tests. Everything runs offline against httpx.MockTransport."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from labwatch.baseline import RuleBasedClient
from labwatch.clients import (ChatToolsClient, ModelClientError, MuseSparkClient, OpenWeightsClient,
                              ReplayClient, make_client)
from labwatch.pipeline import run_scenario, score
from labwatch.tools import TOOL_SCHEMAS

KEY = "sk-test-secret"


def reply(content=None, tool_calls=None, status=200):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(status, json={"choices": [{"index": 0, "message": msg}]})


def api_call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class Fake:
    """An endpoint that answers each request with the next scripted response."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def body(self, i=-1):
        return json.loads(self.requests[i].content)


def client(fake, **kw):
    return ChatToolsClient("https://fake.test/v1/", KEY, "test-model", backoff=0,
                           transport=httpx.MockTransport(fake), **kw)


MESSAGES = [
    {"role": "system", "content": "You are LabWatch."},
    {"role": "user", "content": "Incident INC-0001 opened."},
    {"role": "assistant", "content": "Check the layout.",
     "tool_calls": [{"id": "c1", "name": "get_layout", "arguments": {"rooms": ["412"]}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "get_layout", "content": {"evidence_id": "E1", "rooms": {}}},
    {"role": "assistant", "content": {"status": "diagnosed"}},
    {"role": "user", "content": "Your report failed validation."},
]


class TranslationTests(unittest.TestCase):
    def test_request_translation(self):
        fake = Fake(reply('{"status": "inconclusive"}'))
        client(fake).next(MESSAGES, TOOL_SCHEMAS)
        req, body = fake.requests[0], fake.body()
        self.assertEqual(str(req.url), "https://fake.test/v1/chat/completions")
        self.assertEqual(req.headers["authorization"], f"Bearer {KEY}")
        self.assertEqual(body["model"], "test-model")
        system, user, assistant, tool, report, repair = body["messages"]
        self.assertEqual(system, MESSAGES[0])
        self.assertEqual(user, MESSAGES[1])
        self.assertEqual(assistant["tool_calls"], [{"id": "c1", "type": "function", "function": {
            "name": "get_layout", "arguments": '{"rooms": ["412"]}'}}])
        self.assertEqual(assistant["content"], "Check the layout.")
        self.assertEqual(tool, {"role": "tool", "tool_call_id": "c1",
                                "content": '{"evidence_id": "E1", "rooms": {}}'})
        self.assertEqual(report, {"role": "assistant", "content": '{"status": "diagnosed"}'})
        self.assertEqual(repair, MESSAGES[5])

    def test_tool_schema_translation(self):
        fake = Fake(reply("{}"))
        client(fake).next(MESSAGES[:2], TOOL_SCHEMAS)
        tools = fake.body()["tools"]
        self.assertEqual(len(tools), len(TOOL_SCHEMAS))
        self.assertEqual(tools[0], {"type": "function", "function": TOOL_SCHEMAS[0]})

    def test_tool_call_response(self):
        fake = Fake(reply("Check AHU-4 first.", [api_call("call_9", "query_metrics", {"asset": "AHU-4"}),
                                                 {"id": "call_10", "type": "function",
                                                  "function": {"name": "get_layout", "arguments": "{bad"}}]))
        resp = client(fake).next(MESSAGES[:2], TOOL_SCHEMAS)
        self.assertEqual(resp, {"thought": "Check AHU-4 first.", "tool_calls": [
            {"id": "call_9", "name": "query_metrics", "arguments": {"asset": "AHU-4"}},
            {"id": "call_10", "name": "get_layout", "arguments": "{bad"}]})   # agent.py reports bad JSON

    def test_json_final(self):
        resp = client(Fake(reply('{"status": "diagnosed", "confidence": "high"}'))).next(MESSAGES[:2], [])
        self.assertEqual(resp, {"final": {"status": "diagnosed", "confidence": "high"}})

    def test_fenced_json_final(self):
        text = 'Here is my report:\n```json\n{"status": "inconclusive"}\n```\n'
        resp = client(Fake(reply(text))).next(MESSAGES[:2], [])
        self.assertEqual(resp, {"final": {"status": "inconclusive"}})

    def test_non_json_final_is_returned_raw(self):
        resp = client(Fake(reply("I think AHU-4 failed."))).next(MESSAGES[:2], [])
        self.assertEqual(resp, {"final": "I think AHU-4 failed."})


class HttpTests(unittest.TestCase):
    def test_429_then_success_is_retried(self):
        fake = Fake(httpx.Response(429, json={"error": {"message": "slow down"}}), reply("{}"))
        with self.assertLogs("labwatch", "WARNING"):
            self.assertEqual(client(fake).next(MESSAGES[:2], []), {"final": {}})
        self.assertEqual(len(fake.requests), 2)

    def test_400_is_not_retried(self):
        fake = Fake(httpx.Response(400, json={"error": {"message": "bad tools field"}}))
        with self.assertRaises(ModelClientError) as ctx:
            client(fake).next(MESSAGES[:2], [])
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("bad tools field", str(ctx.exception))
        self.assertEqual(len(fake.requests), 1)

    def test_5xx_retries_run_out(self):
        fake = Fake(*[httpx.Response(503, text="overloaded")] * 3)
        with self.assertRaises(ModelClientError) as ctx, self.assertLogs("labwatch", "WARNING"):
            client(fake).next(MESSAGES[:2], [])
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(len(fake.requests), 3)

    def test_timeout_is_retried_then_fails(self):
        fake = Fake(*[httpx.ReadTimeout("timed out")] * 3)
        with self.assertRaises(ModelClientError) as ctx, self.assertLogs("labwatch", "WARNING"):
            client(fake, timeout=5).next(MESSAGES[:2], [])
        self.assertIn("timed out after 5s", str(ctx.exception))
        self.assertEqual(len(fake.requests), 3)

    def test_api_key_never_shown(self):
        fake = Fake(httpx.Response(401, json={"error": {"message": "invalid key"}}))
        c = client(fake)
        self.assertNotIn(KEY, repr(c))
        with self.assertRaises(ModelClientError) as ctx, self.assertLogs("labwatch", "WARNING") as logs:
            client(Fake(*[httpx.Response(500)] * 3)).next(MESSAGES[:2], [])
        self.assertNotIn(KEY, str(ctx.exception) + "".join(logs.output))
        with self.assertRaises(ModelClientError) as ctx:
            c.next(MESSAGES[:2], [])
        self.assertNotIn(KEY, str(ctx.exception))


class Scripted:
    def __init__(self, *responses):
        self.responses = list(responses)

    def next(self, messages, tools):
        return self.responses.pop(0)


class ReplayTests(unittest.TestCase):
    def test_record_then_replay(self):
        responses = [{"tool_calls": [{"id": "c1", "name": "get_layout", "arguments": {"rooms": ["412"]}}]},
                     {"final": {"status": "inconclusive"}}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            recorder = ReplayClient(path, inner=Scripted(*responses))
            got = [recorder.next([{"role": "user", "content": "go"}], []) for _ in responses]
            self.assertEqual(got, responses)
            self.assertEqual(len(path.read_text().splitlines()), 2)
            player = ReplayClient(path)
            self.assertEqual([player.next([], []) for _ in responses], responses)
            with self.assertRaises(ModelClientError):
                player.next([], [])

    def test_missing_replay_file(self):
        with self.assertRaises(ModelClientError):
            make_client("replay:/nonexistent/session.jsonl", None, None)


class SelectionTests(unittest.TestCase):
    def test_baseline(self):
        self.assertIsInstance(make_client("baseline", {}, {"rooms": ["412"]}), RuleBasedClient)

    def test_unknown_client(self):
        with self.assertRaises(ModelClientError):
            make_client("gpt", None, None)

    def test_muse_without_key_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ModelClientError) as ctx:
            make_client("muse", None, None)
        self.assertIn("META_API_KEY", str(ctx.exception))

    def test_env_config(self):
        env = {"META_BASE_URL": "https://meta.test/v1", "META_API_KEY": KEY, "META_MODEL": "muse-spark",
               "OPEN_WEIGHTS_BASE_URL": "https://open.test/v1", "OPEN_WEIGHTS_MODEL": "glimmer"}
        with mock.patch.dict(os.environ, env, clear=True):
            muse, open_ = MuseSparkClient(), OpenWeightsClient()
        self.assertEqual((muse.model, str(muse._http.base_url)), ("muse-spark", "https://meta.test/v1/"))
        self.assertEqual(open_.model, "glimmer")
        self.assertNotIn("authorization", open_._http.headers)   # self-hosted key is optional


def ahu_endpoint():
    """A fake model that investigates ahu_failure the way we'd hope Muse Spark does."""
    def tool_results(body):
        return {m["tool_call_id"]: json.loads(m["content"]) for m in body["messages"] if m["role"] == "tool"}

    def handler(request):
        body = json.loads(request.content)
        results, step = tool_results(body), len(handler.seen)
        handler.seen.append(body)
        if step == 0:
            rooms = ["412", "414", "418"]
            assert "412" in body["messages"][1]["content"]
            return reply("Find what the flagged rooms share.", [api_call("a", "get_layout", {"rooms": rooms})])
        if step == 1:
            ahu = {r["air_handler"] for r in results["a"]["rooms"].values()}
            assert ahu == {"AHU-4"}, ahu
            return reply("All on AHU-4. If it failed, fan power is low and doors are quiet.",
                         [api_call("b", "query_metrics", {"asset": "AHU-4"}),
                          api_call("c", "get_door_events", {"rooms": ["412", "414", "418"]})])
        if step == 2:
            assert results["b"]["current"] < 2 and results["c"]["events"] == []
            return reply("AHU-4 is down. Look up the protocol.",
                         [api_call(f"p{i}", "get_protocol", {"situation": s}) for i, s in enumerate(
                             ["air_handler_failure", "isolation_pressure_lost", "protective_pressure_lost"])])
        report = {
            "status": "diagnosed", "confidence": "high", "affected_rooms": ["412", "414", "418"],
            "root_cause": {"category": "air_handler_failure", "asset": "AHU-4",
                           "summary": "AHU-4 fan power collapsed.", "evidence": [results["b"]["evidence_id"]]},
            "findings": [{"claim": "All flagged rooms are on AHU-4.", "evidence": [results["a"]["evidence_id"]]}],
            "ruled_out": [{"cause": "doors", "evidence": [results["c"]["evidence_id"]]}],
            "actions": [{"id": "INSPECT_AHU", "params": {"asset": "AHU-4"}},
                        {"id": "DOORS_CLOSED", "params": {"rooms": ["412", "414", "418"]}},
                        {"id": "N95_ENTRY", "params": {"rooms": ["412", "414"]}},
                        {"id": "PREPARE_BACKUP_ROOM", "params": {"backup_room": "420"}},
                        {"id": "LIMIT_ENTRY_PROTECTIVE", "params": {"rooms": ["418"]}}]}
        return reply("```json\n" + json.dumps(report) + "\n```")

    handler.seen = []
    return handler


class EndToEndTests(unittest.TestCase):
    def test_ahu_failure_investigation_over_http(self):
        endpoint = ahu_endpoint()
        run = run_scenario("ahu_failure", lambda layout, incident: ChatToolsClient(
            "https://fake.test/v1", KEY, "test-model", transport=httpx.MockTransport(endpoint)))
        result = run["result"]
        self.assertEqual(result.status, "accepted", result.errors)
        self.assertEqual(score(run), {"detected_correctly": True, "root_cause_correct": True, "actions_f1": 1.0})
        self.assertEqual(len(endpoint.seen), 4)
        self.assertEqual([t["evidence_id"] for t in result.trace if t["type"] == "tool"],
                         ["E1", "E2", "E3", "E4", "E5", "E6"])


if __name__ == "__main__":
    unittest.main()
