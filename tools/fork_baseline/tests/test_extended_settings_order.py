"""Source-contract regressions, not native UI rendering or interaction proof."""
from pathlib import Path
import itertools
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "submodules/SettingsUI/Sources/LocalArchiveSettingsController.swift"


class ExtendedSettingsOrderTests(unittest.TestCase):
    def setUp(self):
        text = SOURCE.read_text()
        self.body = text.split("func localArchiveSettingsController(", 1)[1].split("private func localArchiveExclusionsController(", 1)[0]
        self.rows = [(int(a), int(b), int(c)) for a, b, c in re.findall(r"LocalArchiveRow\(stableId: \.fixed\((\d+)\), order: (\d+), section: (\d+)", self.body)]

    def test_actual_settings_state_sorts_before_item_list_precondition(self):
        states = re.findall(r"ItemListNodeState\(presentationData: data, entries: ([^,]+),", self.body)
        self.assertEqual(states, ["entries.sorted()"] )
        node = (ROOT / "submodules/ItemListUI/Sources/ItemListControllerNode.swift").read_text()
        self.assertIn("assert(state.entries[i - 1].isLessThan(state.entries[i]))", node)

    def test_all_conditional_settings_combinations_have_unique_sorted_order_and_sections(self):
        self.assertEqual(len(self.rows), 20)
        for error, missed, chat in itertools.product((False, True), repeat=3):
            rows = [r for r in self.rows if (r[0] != 7 or error) and (r[0] != 14 or missed) and (r[0] not in (15, 16, 17) or chat)]
            with self.subTest(error=error, missed=missed, chat=chat):
                self.assertEqual(len({r[0] for r in rows}), len(rows))
                self.assertEqual(len({r[1] for r in rows}), len(rows))
                ordered = sorted(rows, key=lambda r: r[1])
                self.assertTrue(all(a[1] < b[1] and a[2] <= b[2] for a, b in zip(ordered, ordered[1:])))

    def test_extended_switches_keep_stable_ids_and_real_distinct_account_setters(self):
        self.assertIn((24, 2, 0), self.rows)
        self.assertIn((25, 3, 0), self.rows)
        self.assertIn("archive.setIncludeSecretChats(enabled)", self.body)
        self.assertIn("archive.setIncludeExpiringMessages(enabled)", self.body)
        self.assertIn('title: "Enable"', self.body)


if __name__ == "__main__":
    unittest.main()
