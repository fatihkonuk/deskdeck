from pathlib import Path

import pytest

from deskdeck import config, layout

EXAMPLE = Path(__file__).resolve().parent.parent / "config.toml.example"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_example_config_loads():
    pages = config.load_pages(EXAMPLE)
    assert len(pages) == 3
    assert all(len(page) == layout.BUTTON_COUNT for page in pages)
    assert config.load(EXAMPLE).token


def test_no_pages_falls_back_to_media_page(tmp_path):
    assert config.load_pages(write(tmp_path, "[device]\ntoken = 't'\n")) == config.DEFAULT_PAGES


def test_empty_cells_are_none(tmp_path):
    (page,) = config.load_pages(write(tmp_path, '[[pages]]\nbuttons = [{}, { app = "Safari" }]\n'))
    assert page[0] is None
    assert page[1] == config.ButtonSpec("app", "Safari")
    assert page[2:] == (None,) * (layout.BUTTON_COUNT - 2)


@pytest.mark.parametrize("text, message", [
    ('pages = ["x"]', "page 1: must be a table"),
    ("[pages]\nbuttons = []", "array of tables"),
    ("[[pages]]\nbuttons = [1]", "page 1, button 1: must be a table"),
    ('[[pages]]\nbuttons = "abc"', "'buttons' must be an array"),
    ('[[pages]]\nbuttons = [{ app = "A", open = "B" }]', "exactly one action"),
    ('[[pages]]\nbuttons = [{ media = "louder" }]', "media must be one of"),
    ('[[pages]]\nbuttons = [{ app = "A", colour = "red" }]', "unknown field"),
    ("[[pages]\n", "Expected"),  # TOML syntax error
])
def test_malformed_pages_raise_value_error(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        config.load_pages(write(tmp_path, text))


def test_missing_file(tmp_path):
    # Reload path: a plain OSError the caller can log. Start-up: a readable message and exit.
    with pytest.raises(FileNotFoundError):
        config.load_pages(tmp_path / "config.toml")
    with pytest.raises(SystemExit, match="config.toml.example"):
        config.load(tmp_path / "config.toml")
