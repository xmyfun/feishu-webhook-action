"""Unit tests for notify.py — standard library only, run with:

    python3 -m unittest discover -s tests -v
"""

import base64
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from urllib.error import URLError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify  # noqa: E402


def inputs(**overrides):
    """All inputs empty, so a test only sets what it cares about."""
    base = {key: "" for key in (
        "INPUT_WEBHOOK", "INPUT_WEBHOOKS", "INPUT_WEBHOOK_SECRET", "INPUT_MSG_TYPE",
        "INPUT_TITLE", "INPUT_CONTENT", "INPUT_CARD", "INPUT_TEMPLATE",
        "INPUT_STATUS", "INPUT_INCLUDE_CONTEXT", "INPUT_EXTRA_ELEMENTS",
        "INPUT_TIMEOUT", "INPUT_RETRIES", "INPUT_DRY_RUN", "INPUT_FAIL_ON_ERROR",
    )}
    base.update(overrides)
    return base


class SignTests(unittest.TestCase):
    def test_signature_matches_feishu_spec(self):
        # sign = base64(HMAC-SHA256(key = ts + "\n" + secret, msg = ""))
        ts = 1795487030
        secret = "testsecret123"
        expected = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
        body = {"msg_type": "text", "content": {"text": "hi"}}
        signed = notify.sign(body, secret, timestamp=ts)
        self.assertEqual(signed["timestamp"], str(ts))
        self.assertEqual(signed["sign"], expected)
        # the original body must not be mutated
        self.assertNotIn("sign", body)

    def test_golden_vector_is_stable(self):
        # Frozen vector: changing this means the wire format changed (needs v2).
        signed = notify.sign({"msg_type": "text"}, "s3cr3t", timestamp=1700000000)
        self.assertEqual(signed["sign"], "CNNNRL0wSY2j+cKcY98lQAfVvb21u/iW3phZyXRkmcY=")

    def test_no_secret_means_no_signature(self):
        self.assertEqual(notify.sign({"msg_type": "text"}, ""), {"msg_type": "text"})


class TargetTests(unittest.TestCase):
    def test_single_webhook_fallback(self):
        self.assertEqual(
            notify.parse_targets("", "https://hook/a", "s"),
            [("https://hook/a", "s")],
        )

    def test_webhooks_win_over_single(self):
        targets = notify.parse_targets("https://hook/a|s1\nhttps://hook/b", "https://hook/c", "g")
        self.assertEqual(targets, [("https://hook/a", "s1"), ("https://hook/b", "g")])

    def test_global_secret_applies_to_lines_without_secret(self):
        targets = notify.parse_targets("https://hook/a\nhttps://hook/b|s2", "", "global")
        self.assertEqual(targets, [("https://hook/a", "global"), ("https://hook/b", "s2")])

    def test_blank_lines_ignored(self):
        self.assertEqual(notify.parse_targets("\n  \n", "", ""), [])


class TemplateTests(unittest.TestCase):
    def test_status_mapping(self):
        for status, expected in (
            ("success", "green"), ("failure", "red"),
            ("cancelled", "grey"), ("skipped", "grey"),
        ):
            self.assertEqual(notify.resolve_template("", status), expected)

    def test_unknown_status_defaults_blue(self):
        self.assertEqual(notify.resolve_template("", ""), "blue")

    def test_template_whitelist(self):
        for colour in notify.TEMPLATES:
            self.assertEqual(notify.resolve_template(colour, "failure"), colour)

    def test_invalid_template_falls_back_to_blue(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(notify.resolve_template("purpel", "failure"), "blue")
        self.assertIn("::warning::", buffer.getvalue())


class BodyTests(unittest.TestCase):
    def build(self, **overrides):
        with mock.patch.dict(os.environ, inputs(**overrides), clear=True):
            return notify.build_body()

    def test_interactive_order(self):
        extra = json.dumps([{"tag": "hr"}])
        body = self.build(
            INPUT_TITLE="CI failed", INPUT_CONTENT="**boom**",
            GITHUB_REPOSITORY="o/r", GITHUB_REF_NAME="dev", GITHUB_SHA="abcdef1234",
            INPUT_EXTRA_ELEMENTS=extra,
        )
        self.assertEqual(body["msg_type"], "interactive")
        self.assertEqual(body["card"]["header"]["title"]["content"], "CI failed")
        self.assertEqual(body["card"]["header"]["template"], "blue")
        elements = body["card"]["elements"]
        self.assertEqual(elements[0]["text"]["content"], "**boom**")
        self.assertIn("Repository", elements[1]["text"]["content"])
        self.assertEqual(elements[-1], {"tag": "hr"})

    def test_include_context_false(self):
        body = self.build(
            INPUT_TITLE="t", INPUT_CONTENT="c", INPUT_INCLUDE_CONTEXT="false",
            GITHUB_REPOSITORY="o/r",
        )
        self.assertEqual(len(body["card"]["elements"]), 1)

    def test_text_mode_ignores_title(self):
        body = self.build(INPUT_MSG_TYPE="text", INPUT_TITLE="ignored", INPUT_CONTENT="hi")
        self.assertEqual(body, {"msg_type": "text", "content": {"text": "hi"}})

    def test_card_passthrough(self):
        card = {"header": {"template": "green"}, "elements": []}
        body = self.build(INPUT_CARD=json.dumps(card), INPUT_INCLUDE_CONTEXT="false")
        self.assertEqual(body, {"msg_type": "interactive", "card": card})

    def test_card_with_title_is_rejected(self):
        with self.assertRaises(notify.ActionError):
            self.build(INPUT_CARD='{"header":{}}', INPUT_TITLE="t")

    def test_card_with_template_is_rejected(self):
        with self.assertRaises(notify.ActionError):
            self.build(INPUT_CARD='{"header":{}}', INPUT_TEMPLATE="green")

    def test_card_invalid_json(self):
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(notify.ActionError):
                self.build(INPUT_CARD="{")

    def test_msg_type_post_unsupported(self):
        with self.assertRaises(notify.ActionError):
            self.build(INPUT_MSG_TYPE="post", INPUT_CONTENT="x")

    def test_msg_type_invalid(self):
        with self.assertRaises(notify.ActionError):
            self.build(INPUT_MSG_TYPE="markdown", INPUT_CONTENT="x")

    def test_empty_interactive_is_rejected(self):
        with self.assertRaises(notify.ActionError):
            self.build()

    def test_title_truncated(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            body = self.build(INPUT_TITLE="x" * 60, INPUT_INCLUDE_CONTEXT="false")
        self.assertEqual(len(body["card"]["header"]["title"]["content"]), notify.TITLE_LIMIT)
        self.assertIn("::warning::", buffer.getvalue())

    def test_oversized_body_shrinks(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            body = self.build(INPUT_TITLE="t", INPUT_CONTENT="x" * (notify.MAX_BODY_BYTES + 500))
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.assertLessEqual(len(raw), notify.MAX_BODY_BYTES)
        self.assertIn("::warning::", buffer.getvalue())

    def test_bad_extra_elements_warns_and_ignores(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            body = self.build(INPUT_TITLE="t", INPUT_EXTRA_ELEMENTS="{}")
        self.assertEqual(body["card"]["elements"], [])
        self.assertIn("::warning::", buffer.getvalue())


class ResponseTests(unittest.TestCase):
    def test_code_zero_is_success(self):
        self.assertTrue(notify.parse_response('{"code":0,"msg":"success"}')[0])

    def test_nonzero_code_is_failure(self):
        ok, detail = notify.parse_response('{"code":19021,"msg":"sign match fail"}')
        self.assertFalse(ok)
        self.assertIn("19021", detail)

    def test_non_json_response(self):
        ok, detail = notify.parse_response("<html>oops</html>")
        self.assertFalse(ok)
        self.assertIn("non-JSON", detail)


class SendTests(unittest.TestCase):
    def test_retry_on_network_error_then_success(self):
        responses = [URLError("boom"), mock.MagicMock()]

        def fake_urlopen(req, timeout):
            outcome = responses.pop(0)
            if isinstance(outcome, URLError):
                raise outcome
            handle = mock.MagicMock()
            handle.read.return_value = b'{"code":0}'
            handle.status = 200
            handle.__enter__ = lambda self: handle
            handle.__exit__ = lambda self, *a: False
            return handle

        with mock.patch.object(notify.urllib.request, "urlopen", side_effect=fake_urlopen):
            with mock.patch.object(notify.time, "sleep", return_value=None):
                ok, _ = notify.send("https://hook", {"msg_type": "text"}, retries=1)
        self.assertTrue(ok)

    def test_dry_run_does_not_send(self):
        with mock.patch.object(notify.urllib.request, "urlopen", side_effect=AssertionError("must not send")):
            with redirect_stdout(io.StringIO()):
                ok, detail = notify.send("https://hook", {"msg_type": "text"}, dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(detail, "dry_run")


class OutputTests(unittest.TestCase):
    def test_outputs_written(self):
        with tempfile.NamedTemporaryFile("r+", delete=False) as handle:
            path = handle.name
        try:
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": path}, clear=True):
                notify.write_outputs("success", 2, 0)
            with open(path, encoding="utf-8") as handle_read:
                content = handle_read.read()
            self.assertIn("result=success", content)
            self.assertIn("sent=2", content)
            self.assertIn("failed=0", content)
        finally:
            os.unlink(path)

    def test_body_output_uses_heredoc(self):
        with tempfile.NamedTemporaryFile("r+", delete=False) as handle:
            path = handle.name
        try:
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": path}, clear=True):
                notify.write_outputs("success", 0, 0, '{"msg_type":"text"}')
            with open(path, encoding="utf-8") as handle_read:
                content = handle_read.read()
            self.assertIn("body<<__FEISHU_BODY_EOF__", content)
            self.assertIn("__FEISHU_BODY_EOF__", content)
        finally:
            os.unlink(path)


class RunTests(unittest.TestCase):
    def run_with(self, env_map):
        with tempfile.NamedTemporaryFile("r+", delete=False) as handle:
            path = handle.name
        try:
            with mock.patch.dict(os.environ, dict(env_map, GITHUB_OUTPUT=path), clear=True):
                with redirect_stdout(io.StringIO()):
                    code = notify.main()
            with open(path, encoding="utf-8") as handle_read:
                return code, handle_read.read()
        finally:
            os.unlink(path)

    def test_no_webhook_skips(self):
        code, out = self.run_with(inputs(INPUT_TITLE="t"))
        self.assertEqual(code, 0)
        self.assertIn("result=skipped", out)

    def test_dry_run_outputs_body(self):
        code, out = self.run_with(inputs(
            INPUT_WEBHOOK="https://hook/x", INPUT_TITLE="t",
            INPUT_DRY_RUN="true", INPUT_INCLUDE_CONTEXT="false",
        ))
        self.assertEqual(code, 0)
        self.assertIn("result=success", out)
        self.assertIn("sent=0", out)
        self.assertIn("body<<__FEISHU_BODY_EOF__", out)

    def test_invalid_input_fails(self):
        code, out = self.run_with(inputs(
            INPUT_WEBHOOK="https://hook/x", INPUT_MSG_TYPE="post", INPUT_CONTENT="x",
        ))
        self.assertEqual(code, 1)
        self.assertIn("result=failure", out)


if __name__ == "__main__":
    unittest.main()
