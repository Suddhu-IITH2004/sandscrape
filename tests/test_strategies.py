import unittest

from strategies import build_daytona_script, parse_html, validate_url_syntax


class StrategyTests(unittest.TestCase):
    def test_extracts_markdown_tables_and_meaningful_images(self):
        html = """
        <html><head><title>Quarterly report</title></head><body>
          <main><p>Revenue increased across every reported region this quarter.</p>
            <table><tr><th>Region</th><th>Growth</th></tr><tr><td>North</td><td>12%</td></tr></table>
            <img src="/chart.png" alt="Revenue chart" width="640" height="360">
            <img src="/icon.svg" alt="Icon">
            <img src="/tiny.png" width="24" height="24">
          </main>
        </body></html>
        """

        result = parse_html(
            html,
            "https://example.com/report",
            "ai-heuristic",
            {"text", "tables", "images"},
        )

        self.assertTrue(result.valid)
        self.assertIn("# Quarterly report", result.markdown)
        self.assertIn("| Region | Growth |", result.markdown)
        self.assertIn("![Revenue chart](https://example.com/chart.png)", result.markdown)
        self.assertEqual(len(result.images), 1)

    def test_rejects_private_and_non_http_urls(self):
        for url in ("http://127.0.0.1", "http://localhost", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url_syntax(url)

    def test_daytona_script_is_self_contained_python(self):
        script = build_daytona_script(
            "https://example.com",
            "bs4-fast",
            {"text", "images"},
        )

        compile(script, "<daytona-strategy>", "exec")
        self.assertIn('"bs4-fast"', script)


if __name__ == "__main__":
    unittest.main()
