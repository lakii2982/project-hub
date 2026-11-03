"""PR reference lines: the light-delivery Queue line and the legacy Task line."""
from pathlib import Path
import tempfile
import unittest

from scripts import check_pr


class ReferenceLineTests(unittest.TestCase):
    def test_task_only_queue_only_and_both_are_accepted(self):
        self.assertEqual(check_pr.extract_references('Task: PH-120\n'), ('PH-120', None))
        self.assertEqual(check_pr.extract_references('Queue: Q-08\n'), (None, 'Q-08'))
        self.assertEqual(check_pr.extract_references('Queue: Q-08\nTask: PH-120\n'),
                         ('PH-120', 'Q-08'))

    def test_missing_or_duplicate_references_are_rejected(self):
        for body in ('', 'no reference', 'Queue: Q-08\nQueue: Q-09\n',
                     'Task: PH-120\nTask: PH-121\n',
                     'Queue: q-08\n', 'Queue: Q-8\n', 'Task: PH-1\n'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                check_pr.extract_references(body)
        with self.assertRaises(ValueError):
            check_pr.extract_references(None)

    def test_reference_lines_are_anchored_to_their_own_lines(self):
        _, queue_id = check_pr.extract_references('说明 Task: PH-120 已废弃\nQueue: Q-08\n')
        self.assertEqual(queue_id, 'Q-08')


class QueueDocumentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='check-pr-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, content):
        path = self.root / 'project' / 'QUEUE.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def test_items_are_read_from_headings_and_index_rows(self):
        self.write('# Q\n\n### Q-01 · 一项\n\n- 波次：W1\n\n| id | wave |\n| --- | --- |\n| Q-02 | W1 |\n')
        self.assertEqual(check_pr.queue_items(self.root), ['Q-01', 'Q-02'])
        self.assertTrue(check_pr.validate_queue_reference(self.root, 'Q-02'))

    def test_missing_document_or_unknown_item_is_rejected(self):
        with self.assertRaises(ValueError):
            check_pr.validate_queue_reference(self.root, 'Q-01')
        self.write('# Q\n')
        with self.assertRaises(ValueError):
            check_pr.validate_queue_reference(self.root, 'Q-01')
        self.write('# Q\n\n### Q-01 · 一项\n')
        with self.assertRaises(ValueError):
            check_pr.validate_queue_reference(self.root, 'Q-99')
