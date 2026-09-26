"""Offline tests for contexts («уклон»): app/themes.py, app/theme_store.py and
the /api/themes routes, plus the context reaching every generator."""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config, exercise_sets, theme_store, themes  # noqa: E402
from app.server import app as fastapi_app  # noqa: E402
from tests.test_api import FakeAnalyzer, FakeGenerator  # noqa: E402

DAY = dt.date(2026, 9, 26)


class ThemeCatalogueTests(unittest.TestCase):
    def test_every_builtin_theme_but_mixed_has_prompts_with_unique_ids(self) -> None:
        ids = []
        for theme in themes.BUILTIN_THEMES:
            prompts = themes.builtin_prompts(theme.key)
            self.assertTrue(prompts, theme.key)
            if theme.key != themes.MIXED:
                ids += [p["id"] for p in prompts]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(themes.builtin_prompts(themes.MIXED)), len(ids))
        self.assertEqual(themes.DEFAULT_THEME, "it_backend")

    def test_prompt_of_day_changes_daily_and_offset_moves_it(self) -> None:
        prompts = themes.builtin_prompts("travel")
        today = themes.prompt_of_day(prompts, DAY)
        self.assertNotEqual(today, themes.prompt_of_day(prompts, DAY + dt.timedelta(days=1)))
        for offset in range(20):
            day = DAY + dt.timedelta(days=offset)
            self.assertNotEqual(
                themes.prompt_of_day(prompts, day), themes.prompt_of_day(prompts, day, offset=1)
            )

    def test_model_context(self) -> None:
        self.assertIn("backend", themes.model_context(None))
        self.assertIn("airports", themes.model_context({"key": "travel", "label": "x"}))
        own = themes.model_context({"key": "own_1", "label": "Ремонт машины"})
        self.assertIn("Ремонт машины", own)

    def test_split_prompt_id(self) -> None:
        self.assertEqual(themes.split_prompt_id("news:3"), ("news", 3))
        for bad in ("news", "news:", ":3", "news:x"):
            with self.assertRaises(ValueError):
                themes.split_prompt_id(bad)


class ThemeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)

    def test_default_is_it_backend_until_something_is_used(self) -> None:
        self.assertEqual(theme_store.last_used(), {"key": "it_backend", "label": "IT / бэкенд"})
        theme_store.remember(theme_store.resolve(label="  про  котов "))
        self.assertEqual(theme_store.last_used(), {"key": None, "label": "про котов"})
        self.assertEqual(theme_store.resolve(), {"key": None, "label": "про котов"})

    def test_own_themes_are_numbered_and_deduplicated(self) -> None:
        first = theme_store.add_custom("Ремонт машины")
        self.assertEqual(first, {"key": "own_1", "label": "Ремонт машины"})
        self.assertEqual(theme_store.add_custom("ремонт  МАШИНЫ"), first)
        self.assertEqual(theme_store.add_custom("Путешествия")["key"], "travel")
        self.assertEqual(theme_store.add_custom("Кулинария")["key"], "own_2")
        with self.assertRaises(theme_store.ThemeError):
            theme_store.add_custom("   ")
        self.assertEqual(theme_store.resolve("own_2")["label"], "Кулинария")
        with self.assertRaises(theme_store.ThemeError):
            theme_store.resolve("own_9")

    def test_deleting_the_last_used_theme_falls_back_to_the_default(self) -> None:
        own = theme_store.add_custom("Ремонт машины")
        theme_store.remember(own)
        self.assertTrue(theme_store.delete_custom("own_1"))
        self.assertFalse(theme_store.delete_custom("own_1"))
        self.assertEqual(theme_store.last_used()["key"], "it_backend")

    def test_day_prompt_follows_the_last_theme_or_falls_back_to_mixed(self) -> None:
        self.assertTrue(theme_store.prompt_of_day(DAY)["id"].startswith("it_backend:"))
        theme_store.remember({"key": "health", "label": "Здоровье"})
        prompt = theme_store.prompt_of_day(DAY)
        self.assertTrue(prompt["id"].startswith("health:"))
        self.assertEqual(prompt["theme"]["key"], "health")

        own = theme_store.add_custom("Ремонт машины")
        theme_store.remember(own)  # no prompts written yet
        self.assertEqual(theme_store.prompt_of_day(DAY)["theme"]["key"], themes.MIXED)

        theme_store.store_prompts(
            "own_1", [{"question": "Q1?", "hint": "Х1"}, {"question": "Q2?", "hint": "Х2"}],
            model="m", cost_usd=0.001,
        )
        self.assertEqual(theme_store.prompt_of_day(DAY)["theme"], own)
        resolved = theme_store.resolve_prompt("own_1:1")
        self.assertEqual((resolved["question"], resolved["theme"]), ("Q2?", own))
        self.assertIsNone(theme_store.resolve_prompt("own_1:2"))
        self.assertTrue(theme_store.overview()["custom"][0]["has_prompts"])


class ThemeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.generator = FakeGenerator()
        self.analyzer = FakeAnalyzer()
        fastapi_app.dependency_overrides[api.get_generator_factory] = lambda: (
            lambda: self.generator
        )
        fastapi_app.dependency_overrides[api.get_analyzer_factory] = lambda: (
            lambda: self.analyzer
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def test_own_theme_prompts_are_written_on_a_click_and_kept(self) -> None:
        created = self.client.post("/api/themes", json={"label": "Ремонт машины"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["last"], {"key": "own_1", "label": "Ремонт машины"})

        before = self.client.get("/api/themes/own_1/prompts").json()
        self.assertEqual((before["prompts"], before["can_generate"]), ([], True))
        after = self.client.post("/api/themes/own_1/prompts").json()
        self.assertEqual([p["id"] for p in after["prompts"]], ["own_1:0", "own_1:1"])
        self.assertEqual(self.generator.prompt_labels, ["Ремонт машины"])
        usage = self.client.get("/api/usage").json()["by_purpose"]
        self.assertEqual(usage["anthropic:theme_prompts"]["calls"], 1)

        builtin = self.client.post("/api/themes/travel/prompts")
        self.assertEqual(builtin.status_code, 400)
        self.assertEqual(self.client.get("/api/themes/nope/prompts").status_code, 404)

        self.assertEqual(self.client.delete("/api/themes/own_1").status_code, 200)
        self.assertEqual(self.client.delete("/api/themes/travel").status_code, 404)
        self.assertEqual(self.client.get("/api/themes").json()["custom"], [])

    def test_picker_choice_without_generation_becomes_the_default(self) -> None:
        body = self.client.put("/api/themes/last", json={"key": "restaurant"}).json()
        self.assertEqual(body["last"]["key"], "restaurant")
        today = self.client.get("/api/learner/today").json()
        live = next(s for s in today["steps"] if s["kind"] == "monologue")
        self.assertTrue(live["prompt"]["id"].startswith("restaurant:"))
        talk_prompt = self.client.get("/api/speech/talks").json()["prompt"]
        self.assertNotEqual(talk_prompt["id"], live["prompt"]["id"])
        unknown = self.client.put("/api/themes/last", json={"key": "own_7"})
        self.assertEqual(unknown.status_code, 404)

    def test_a_set_is_written_in_the_chosen_theme_and_keeps_it(self) -> None:
        with patch.object(config, "get_anthropic_api_key", return_value="k"):
            response = self.client.post(
                "/api/practice/sets",
                json={"topic": "past_simple", "theme": {"label": "про котов"}},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.generator.generated[0]["theme"], {"key": None, "label": "про котов"})
        sets = self.client.get("/api/practice/sets?topic=past_simple").json()["sets"]
        self.assertEqual(sets[0]["theme"], {"key": None, "label": "про котов"})
        # The next generation defaults to it.
        self.assertEqual(self.client.get("/api/themes").json()["last"]["label"], "про котов")

    def test_the_analysis_writes_drills_in_the_chosen_theme(self) -> None:
        from app import utils

        session = utils.create_session()
        session.transcript = "I go there yesterday."
        session.status = utils.STATUS_DONE
        utils.write_session_meta(session)
        session.transcript_path.write_text(session.transcript, encoding="utf-8")

        response = self.client.post(
            f"/api/sessions/{session.id}/analyze", json={"theme": {"key": "health"}}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.analyzer.last_kwargs["theme"], {"key": "health", "label": "Здоровье"})
        self.assertEqual(response.json()["analysis"]["theme"]["key"], "health")


class GenerationRequestTests(unittest.TestCase):
    def test_the_context_line_reaches_the_set_request(self) -> None:
        topic = {"key": "past_simple", "label": "Past Simple", "description": "d"}
        text = exercise_sets._generation_request(topic, [], [], {"key": "travel", "label": "x"})
        self.assertIn("Context: travelling", text)


if __name__ == "__main__":
    unittest.main()
