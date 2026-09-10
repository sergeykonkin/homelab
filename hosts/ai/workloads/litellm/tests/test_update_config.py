import contextlib
import hashlib
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "update_config.py"
SPEC = importlib.util.spec_from_file_location("update_config", MODULE_PATH)
update_config = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = update_config
SPEC.loader.exec_module(update_config)


def model(mid: str, modality: str) -> dict:
    return {"id": mid, "architecture": {"modality": modality}, "pricing": {}}


class IsTextModelTests(unittest.TestCase):
    def test_plain_text_to_text(self):
        self.assertTrue(update_config.is_text_model("text->text"))

    def test_text_and_image_input(self):
        self.assertTrue(update_config.is_text_model("text+image->text"))
        self.assertTrue(update_config.is_text_model("image+text->text"))

    def test_image_output_excluded(self):
        self.assertFalse(update_config.is_text_model("text->text+image"))
        self.assertFalse(update_config.is_text_model("text+image->text+image"))

    def test_non_text_input_excluded(self):
        # audio-only input cannot accept text, so it is excluded; a model that
        # also accepts text alongside audio still qualifies.
        self.assertFalse(update_config.is_text_model("audio->text"))
        self.assertTrue(update_config.is_text_model("audio+text->text"))

    def test_empty_or_malformed(self):
        self.assertFalse(update_config.is_text_model(""))
        self.assertFalse(update_config.is_text_model("text"))
        self.assertFalse(update_config.is_text_model("->"))
        self.assertFalse(update_config.is_text_model("text->"))


class FetchModelsFilterTests(unittest.TestCase):
    def _models_data(self, models):
        return {"data": models}

    def test_filters_by_modality(self):
        models = [
            model("nebius/keep-1", "text->text"),
            model("nebius/keep-2", "text+image->text"),
            model("nebius/skip-img-out", "text->text+image"),
            model("nebius/skip-audio", "audio->text"),
        ]
        # Exercise the same predicate fetch_models applies, without the network.
        kept = [
            m for m in models
            if update_config.is_text_model(m.get("architecture", {}).get("modality", ""))
        ]
        self.assertEqual([m["id"] for m in kept], ["nebius/keep-1", "nebius/keep-2"])


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        update_config.CONFIG_DIR = self.tmp.name
        update_config.CONFIG_PATH = os.path.join(self.tmp.name, "config.yaml")
        update_config.HASH_PATH = os.path.join(self.tmp.name, "config.yaml.sha256sum")
        self.addCleanup(setattr, update_config, "fetch_models", update_config.fetch_models)
        os.environ["NEBIUS_API_KEY"] = "test-key"
        self.addCleanup(os.environ.pop, "NEBIUS_API_KEY", None)

    def config_text(self):
        return pathlib.Path(update_config.CONFIG_PATH).read_text()

    def hash_text(self):
        return pathlib.Path(update_config.HASH_PATH).read_text()

    def run_ok(self, models):
        update_config.fetch_models = lambda _key: list(models)
        with contextlib.redirect_stdout(io.StringIO()):
            update_config.main()

    def run_fail(self, models):
        update_config.fetch_models = lambda _key: list(models)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as ctx:
                update_config.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_writes_config_and_hash_on_first_run(self):
        self.run_ok([model("org/model-a", "text->text")])
        self.assertIn("model-a", self.config_text())
        digest = hashlib.sha256(self.config_text().encode()).hexdigest()
        self.assertEqual(self.hash_text(), digest)

    def test_no_changes_when_config_and_hash_match(self):
        models = [model("org/model-a", "text->text")]
        self.run_ok(models)
        update_config.fetch_models = lambda _key: list(models)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            update_config.main()
        self.assertIn("No changes", out.getvalue())

    def test_empty_catalog_preserves_existing_config(self):
        self.run_ok([model("org/model-a", "text->text")])
        content, digest = self.config_text(), self.hash_text()
        self.run_fail([])
        self.assertEqual(self.config_text(), content)
        self.assertEqual(self.hash_text(), digest)

    def test_empty_catalog_on_empty_volume_writes_nothing(self):
        self.run_fail([])
        self.assertFalse(os.path.exists(update_config.CONFIG_PATH))
        self.assertFalse(os.path.exists(update_config.HASH_PATH))

    def test_regenerates_deleted_config_despite_matching_hash(self):
        models = [model("org/model-a", "text->text")]
        self.run_ok(models)
        content = self.config_text()
        os.unlink(update_config.CONFIG_PATH)
        self.run_ok(models)
        self.assertEqual(self.config_text(), content)

    def test_repairs_corrupted_config_despite_matching_hash(self):
        models = [model("org/model-a", "text->text")]
        self.run_ok(models)
        content = self.config_text()
        pathlib.Path(update_config.CONFIG_PATH).write_text("garbage")
        self.run_ok(models)
        self.assertEqual(self.config_text(), content)

    def test_regenerates_missing_hash_alongside_matching_config(self):
        models = [model("org/model-a", "text->text")]
        self.run_ok(models)
        os.unlink(update_config.HASH_PATH)
        self.run_ok(models)
        digest = hashlib.sha256(self.config_text().encode()).hexdigest()
        self.assertEqual(self.hash_text(), digest)


if __name__ == "__main__":
    unittest.main()
