"""Verify roster previews never send student identities to an AI service."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def main():
    with tempfile.TemporaryDirectory(prefix="essay-roster-privacy-") as data_dir:
        os.environ["APP_ENV"] = "test"
        os.environ["ESSAY_DATA_DIR"] = data_dir
        os.environ["ARTICLE_JUDGE_API_KEY"] = "sk-synthetic-roster-privacy-test-key"
        from regression_test import (
            aiapp, login, make_student_workbook, post_file, reset_runtime,
        )
        from essay_platform.adapters import ai

        reset_runtime(data_dir)
        client = aiapp.app.test_client()
        admin = login(client, "admin")
        workbook = make_student_workbook([
            ["三年级", "1班", "PRIV01", "隐私测试甲", "SyntheticStudent-123!"],
            ["三年级", "1班", "PRIV02", "隐私测试乙", ""],
        ])
        with patch.object(ai, "call_llm", side_effect=AssertionError("Roster reached AI")) as llm:
            with patch("requests.sessions.Session.request", side_effect=AssertionError("Roster reached network")) as network:
                response = post_file(
                    client, "/api/admin/students/import/preview", "file",
                    workbook, "synthetic-roster.xlsx", admin,
                )
        assert response.status_code == 200, response.get_data(as_text=True)
        preview = response.get_json()
        assert preview["summary"]["new"] == 2
        assert preview["aiReview"]["status"] == "skipped"
        assert "本地" in preview["aiReview"]["summary"]
        assert llm.call_count == 0 and network.call_count == 0
        assert all("passwordHash" not in row and "password" not in row
                   for row in preview["rows"])
        with aiapp.db.connection() as conn:
            payload = conn.execute(
                "SELECT payload_json FROM import_previews WHERE token=?",
                (preview["token"],),
            ).fetchone()[0]
        assert "SyntheticStudent-123!" not in payload
        assert "sk-synthetic-roster-privacy-test-key" not in payload
        assert "SyntheticStudent-123!" not in response.get_data(as_text=True)
        aiapp.db.reset_state_for_tests()
    print("roster_privacy_test.py: OK (0 AI calls, 0 network calls)")


if __name__ == "__main__":
    main()
