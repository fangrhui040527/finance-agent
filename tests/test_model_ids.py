"""The probe's model-list parser: ids out, nothing else, in both reply shapes."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "model_ids.py"
spec = importlib.util.spec_from_file_location("model_ids", SCRIPT)
assert spec is not None and spec.loader is not None
model_ids = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_ids)


def test_an_openai_shaped_list_yields_sorted_unique_ids():
    text = '{"object":"list","data":[{"id":"llama-b","object":"model"},{"id":"llama-a"},{"id":"llama-b"}]}'
    assert model_ids.ids_in(text) == ["llama-a", "llama-b"]


def test_geminis_models_shape_is_read_by_name():
    text = '{"models":[{"name":"models/gemini-flash-latest"},{"name":"models/gemini-flash-lite-latest"}]}'
    assert model_ids.ids_in(text) == [
        "models/gemini-flash-latest",
        "models/gemini-flash-lite-latest",
    ]


def test_a_refusal_is_explained_from_the_vendors_message_not_the_body():
    text = '{"error":{"message":"Invalid API Key","type":"invalid_request_error"}}'
    assert model_ids.ids_in(text) == []
    assert model_ids.explain(text) == "(refused: Invalid API Key)"


def test_html_and_emptiness_are_named_not_dumped():
    assert model_ids.explain("<html>cloudflare</html>") == "(the reply is not JSON)"
    assert model_ids.explain("   ") == "(empty reply)"
    assert (
        model_ids.ids_in("[1, 2, 3]") == []
        and model_ids.explain("[1, 2, 3]") == "(no model ids in the reply)"
    )


def test_main_writes_one_id_per_line(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"data":[{"id":"z"},{"id":"a"}]}'))
    assert model_ids.main() == 0
    assert capsys.readouterr().out == "a\nz\n"
