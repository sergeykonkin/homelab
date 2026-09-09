import importlib.util
import pathlib
import sys
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


if __name__ == "__main__":
    unittest.main()
