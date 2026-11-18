"""Regression checks for source-index boundaries, not wording of generated docs."""
import json
from pathlib import Path
import tempfile
import unittest

import inventory_telemetry as inventory


class InventoryTests(unittest.TestCase):
    def test_comments_strings_and_nested_arguments_do_not_create_fake_sites(self):
        source = '''
// info!("not code");
/* nested /* debug!("not code") */ comment */
let text = r##"warn!("also not code")"##;
tracing::info!(target: "hopr::test", value = f(1, (2, 3)), "real {}", text);
'''
        mask, _ = inventory.mask_noncode(source)
        calls = list(inventory.EVENT.finditer(mask))
        self.assertEqual(len(calls), 1)
        m = calls[0]
        end = inventory.closing(mask, m.end() - 1)
        self.assertEqual(inventory.parts(source[m.end():end]), [
            'target: "hopr::test"', 'value = f(1, (2, 3))', '"real {}"', 'text'])

    def test_test_gates_do_not_hide_later_production_items(self):
        source = '''
#[cfg(all(test, feature = "serde"))]
mod unusual_test_name { info!("test only"); }
#[cfg(all(feature = "telemetry", not(test)))]
fn producer() { info!("production"); }
#[cfg(any(test, feature = "serde"))]
fn optional_producer() { info!("production with serde"); }
'''
        mask, _ = inventory.production_mask(source)
        self.assertEqual(len(list(inventory.EVENT.finditer(mask))), 2)
        self.assertEqual(inventory.cfg_values('all(test, feature = "serde")'), {False})
        self.assertEqual(inventory.cfg_values('not(test)'), {True})

    def test_catalog_preserves_types_labels_buckets_levels_and_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "Cargo.toml").write_text('[package]\nname="fixture"\nversion="0.1.0"\n')
            code = '''
let m = telemetry::MultiHistogram::new("hopr_duration", "Time", vec![0.1, 1.0], &["operation"]);
#[tracing::instrument(name = "work", level = "trace", skip_all)]
fn work() { tracing::debug!(count = 4, "finished"); }
#[cfg(test)]
mod examples_of_tests { let m = SimpleCounter::new("test", "test"); }
'''
            (root / "src/lib.rs").write_text(code)
            spec = dict(root=str(root), repo="hoprnet/fixture", commit="a" * 40)
            metrics, events, files = inventory.scan("fixture", spec)
            self.assertEqual(len(metrics), 1)
            self.assertEqual(metrics[0]["labels"], ["operation"])
            self.assertEqual(metrics[0]["buckets_expression"], "vec![0.1, 1.0]")
            self.assertEqual(metrics[0]["otel_kind"], "f64_histogram")
            self.assertEqual(metrics[0]["line"], 2)
            self.assertEqual([(e["name"], e["level"]) for e in events],
                             [("work", "TRACE"), ("finished", "DEBUG")])
            self.assertEqual(events[1]["fields"], {"count": "4"})
            self.assertEqual(files, ["src/lib.rs"])
            json.dumps(metrics + events)
            (root / "src/lib.rs").write_text('let m = SimpleCounter::new(dynamic_name, "description");')
            with self.assertRaisesRegex(ValueError, "Dynamic metric"):
                inventory.scan("fixture", spec)

    def test_overlay_identity_ignores_indentation_but_preserves_message_content(self):
        a = dict(path="src/lib.rs", declaration='info!(a = 1, "two words")')
        b = dict(path="src/lib.rs", declaration='info!(\n a=1,\n "two words"\n)')
        c = dict(path="src/lib.rs", declaration='info!(a = 1, "twowords")')
        self.assertEqual(inventory.signature(a), inventory.signature(b))
        self.assertNotEqual(inventory.signature(a), inventory.signature(c))


if __name__ == "__main__":
    unittest.main()
