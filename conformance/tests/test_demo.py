"""The committed transfer examples are exactly what `tds-demo generate-examples` produces."""

import filecmp

from tds_conform.data import data_root
from tds_conform.demo import SCENARIOS, generate_examples


def test_examples_match_generator(tmp_path):
    generate_examples(tmp_path)
    for name in SCENARIOS:
        committed, fresh = data_root() / "examples" / name, tmp_path / name
        cmp = filecmp.dircmp(committed, fresh)
        assert not (cmp.left_only or cmp.right_only), (name, cmp.left_only, cmp.right_only)
        _, mismatch, errors = filecmp.cmpfiles(committed, fresh, cmp.common_files, shallow=False)
        assert not mismatch and not errors, f"{name}: {mismatch} differ; run `uv run tds-demo generate-examples`"
