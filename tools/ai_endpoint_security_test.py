"""Isolated outbound AI endpoint security tests with mocked transports."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TEMP_DATA = tempfile.TemporaryDirectory(prefix="essay-ai-endpoint-")
os.environ["APP_ENV"] = "test"
os.environ["ESSAY_DATA_DIR"] = _TEMP_DATA.name
os.environ["ESSAY_DEMO_ADMIN_PASSWORD"] = "EndpointAdmin123!"
os.environ["ESSAY_DEMO_TEACHER_PASSWORD"] = "EndpointTeacher123!"
os.environ["ESSAY_DEMO_STUDENT_PASSWORD"] = "EndpointStudent123!"
os.environ["ARTICLE_JUDGE_API_KEY"] = "sk-" + "x" * 24
os.environ["ECNU_PLUS_API_KEY"] = "sk-" + "y" * 24

import database as db
from essay_platform.adapters import ai, ai_ocr
from essay_platform.core.ai_endpoints import (
    AiEndpointError, validate_ocr_endpoint, validate_text_endpoint,
)
from essay_platform.persistence import datasets


class AiEndpointSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from essay_platform.application import create_app
        from essay_platform.bootstrap import initialize_runtime

        cls.app = create_app()
        cls.app.config.update(TESTING=True)
        initialize_runtime()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        db.reset_state_for_tests()
        _TEMP_DATA.cleanup()

    def setUp(self):
        from essay_platform.core.auth import secure_password_hash

        self.admin_password = "EndpointSecure123!"
        db.update_user_password(
            "admin", secure_password_hash(self.admin_password), must_change=False)
        cfg = datasets.get_config()
        cfg["base_url"] = "https://api.deepseek.com"
        datasets.save_config(cfg)
        response = self.client.post("/api/login", json={
            "username": "admin", "password": self.admin_password,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.admin = response.get_json()["token"]

    def test_validator_accepts_only_exact_https_endpoints(self):
        self.assertEqual(validate_text_endpoint("https://api.deepseek.com"),
                         "https://api.deepseek.com")
        self.assertEqual(validate_text_endpoint("https://api.deepseek.com:443/v1"),
                         "https://api.deepseek.com/v1")
        self.assertEqual(validate_ocr_endpoint("https://chat.ecnu.edu.cn/open/api/v1"),
                         "https://chat.ecnu.edu.cn/open/api/v1")
        invalid = (
            "http://api.deepseek.com", "https://api.deepseek.com.evil.test",
            "https://api.deepseek.com@evil.test", "https://user@api.deepseek.com",
            "https://api.deepseek.com:444", "https://api.deepseek.com:0443",
            "https://api.deepseek.com/v2", "https://api.deepseek.com/v1/",
            "https://api.deepseek.com/%76%31", "https://api.deepseek.com/v1?x=1",
            "https://api.deepseek.com/v1#fragment", "https://api.deepseek.com\\@evil.test",
            "https://api.deepseek.com/v1\n", " https://api.deepseek.com",
        )
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(AiEndpointError):
                validate_text_endpoint(endpoint)

    def test_allowed_routes_work_through_config_api(self):
        headers = {"X-Token": self.admin}
        saved = self.client.post("/api/config", json={
            "base_url": "https://api.deepseek.com/v1", "model": "test-model",
        }, headers=headers)
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        self.assertEqual(datasets.get_config()["base_url"], "https://api.deepseek.com/v1")

        response = Mock(status_code=200)
        response.json.return_value = {"model": "test-model"}
        with patch.object(ai.requests, "post", return_value=response) as send:
            tested = self.client.post("/api/config/test", json={}, headers=headers)
        self.assertEqual(tested.status_code, 200, tested.get_data(as_text=True))
        self.assertEqual(send.call_args.args[0],
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertIs(send.call_args.kwargs["allow_redirects"], False)

    def test_config_save_and_test_reject_invalid_input_without_transport(self):
        headers = {"X-Token": self.admin}
        with patch.object(ai.requests, "post") as send:
            for path, payload in (
                ("/api/config", {"base_url": "http://api.deepseek.com"}),
                ("/api/config/test", {"base_url": "https://api.deepseek.com.evil.test"}),
            ):
                response = self.client.post(path, json=payload, headers=headers)
                self.assertEqual(response.status_code, 400)
                self.assertIn("不在允许范围", response.get_json()["error"])
            send.assert_not_called()

    def test_persisted_invalid_config_is_blocked_before_transport(self):
        cfg = datasets.get_config()
        cfg["base_url"] = "https://api.deepseek.com.evil.test"
        datasets.save_config(cfg)
        with patch.object(ai.requests, "post") as send:
            with self.assertRaises(ai.LLMRequestError):
                ai.call_llm([{"role": "user", "content": "test"}])
            rejected_test = self.client.post(
                "/api/config/test", json={}, headers={"X-Token": self.admin})
            self.assertEqual(rejected_test.status_code, 400)
            self.assertIn("不在允许范围", rejected_test.get_json()["error"])
            send.assert_not_called()

    def test_allowed_text_routes_post_and_redirect_is_not_followed(self):
        response = Mock(status_code=200)
        response.json.return_value = {"model": "deepseek-v4-flash"}
        with patch.object(ai.requests, "post", return_value=response) as send:
            result = ai.test_connection("https://api.deepseek.com/v1", "secret", "model")
            self.assertTrue(result["ok"])
            self.assertEqual(send.call_args.args[0],
                             "https://api.deepseek.com/v1/chat/completions")
            self.assertIs(send.call_args.kwargs["allow_redirects"], False)

        redirected = Mock(status_code=307, headers={"Location": "https://evil.test"})
        with patch.object(ai.requests, "post", return_value=redirected) as send:
            with self.assertRaises(ai.LLMRequestError):
                ai.call_llm([{"role": "user", "content": "test"}])
            self.assertIs(send.call_args.kwargs["allow_redirects"], False)

        with patch.object(ai.requests, "post", return_value=redirected) as send:
            result = ai.test_connection("https://api.deepseek.com", "secret", "model")
            self.assertEqual(result["reason"], "redirect")
            self.assertIs(send.call_args.kwargs["allow_redirects"], False)

    def test_ocr_uses_fixed_endpoint_and_rejects_redirect(self):
        with patch.object(ai_ocr, "ECNU_BASE_URL", "http://127.0.0.1"), \
                patch.object(ai_ocr.requests, "post") as send:
            with self.assertRaises(ai_ocr.OcrError):
                ai_ocr.call_ocr_vision("data:image/png;base64,AA==")
            send.assert_not_called()

        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": "recognized"}}],
        }
        with patch.object(ai_ocr, "_record_ocr_usage"), \
                patch.object(ai_ocr.requests, "post", return_value=response) as send:
            text = ai_ocr.call_ocr_vision("data:image/png;base64,AA==")
            self.assertEqual(text, "recognized")
            self.assertEqual(send.call_args.args[0],
                             "https://chat.ecnu.edu.cn/open/api/v1/chat/completions")
            self.assertIs(send.call_args.kwargs["allow_redirects"], False)

        redirected = Mock(status_code=302, headers={"Location": "https://evil.test"})
        with patch.object(ai_ocr, "_record_ocr_usage"), \
                patch.object(ai_ocr.requests, "post", return_value=redirected) as send:
            with self.assertRaises(ai_ocr.OcrError):
                ai_ocr.call_ocr_vision("data:image/png;base64,AA==")
            self.assertIs(send.call_args.kwargs["allow_redirects"], False)


if __name__ == "__main__":
    unittest.main()
