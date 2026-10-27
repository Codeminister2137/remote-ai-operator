"""Protected behavioural acceptance for the sample, not editable by model tools."""

import unittest

from issue_import import import_issues


class ImportAcceptance(unittest.TestCase):
    def test_valid_rows_normalize_and_preserve_order(self):
        result = import_issues("id,title,status\n 1 , First , OPEN \n2,Second,closed\n")
        self.assertEqual(
            result,
            {
                "issues": [
                    {"id": "1", "title": "First", "status": "open"},
                    {"id": "2", "title": "Second", "status": "closed"},
                ],
                "errors": [],
            },
        )

    def test_invalid_rows_do_not_reserve_ids(self):
        result = import_issues(
            "id,title,status\n1,,open\n1,Fixed,open\n1,Again,open\n2,Other,bogus\n"
        )
        self.assertEqual([item["id"] for item in result["issues"]], ["1"])
        self.assertEqual([item["row"] for item in result["errors"]], [2, 4, 5])

    def test_existing_and_input_unchanged(self):
        existing = [{"id": "old", "title": "Before", "status": "closed"}]
        result = import_issues("id,title,status\nold,Duplicate,open\nnew,New,open\n", existing)
        self.assertEqual(result["issues"], [{"id": "new", "title": "New", "status": "open"}])
        self.assertEqual(existing, [{"id": "old", "title": "Before", "status": "closed"}])
        self.assertEqual(len(result["errors"]), 1)

    def test_bad_headers_rejected(self):
        for text in ("id,title\n", "id,title,title\n", "id,title,status,extra\n", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                import_issues(text)

    def test_blank_rows_and_quoted_csv(self):
        result = import_issues('id,title,status\n\n1,"One, two",open\n')
        self.assertEqual(result["issues"][0]["title"], "One, two")
        self.assertEqual(result["errors"], [])

    def test_error_line_numbers_include_skipped_blank_lines(self):
        result = import_issues("id,title,status\n\n1,,open\n")
        self.assertEqual(result["errors"][0]["row"], 3)

    def test_short_and_long_rows(self):
        result = import_issues("id,title,status\n1,Short\n2,Long,open,extra\n")
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["errors"]), 2)


if __name__ == "__main__":
    unittest.main()
