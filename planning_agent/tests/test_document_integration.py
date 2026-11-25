"""2단계 기획서 생성, 승인 및 변경 감지 동작을 검증한다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from document.document_flow import (approve_document, document_paths,
                                    document_status, generate_document)


class DocumentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / 'CASE_requirements.json'
        self.data = {
            'overview': {'project_name': '공연 신청', 'summary': '테스트'},
            'user_problem': {'target_users': '동아리 회원'},
            'goals': {'metrics': []},
            'release_scope': {
                'features': [{'id': 'FEAT-001', 'name': '곡 신청',
                              'behavior_and_value': '회원이 곡을 신청한다.',
                              'priority': None, 'included_in_release': True,
                              'done_criteria': ['신청 접수 확인']}],
                'out_of_scope': ['투표'], 'supported_platforms': ['웹'],
                'project_type': '웹서비스', 'tech_stack': []},
            'user_experience': {'screens': []},
            'policies': {'exceptions': [{'condition': '중복 신청',
                                        'system_response': '유지',
                                        'user_guidance': '중복 안내',
                                        'recovery': None,
                                        'related_features': ['FEAT-001']}]},
            'data_rules': {'fields': []},
            'release_plan': {'milestones': [], 'glossary_and_references': []},
        }
        self.write()

    def tearDown(self):
        self.directory.cleanup()

    def write(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def test_not_generated(self):
        self.assertEqual(document_status(self.path)['status'], 'missing')

    def test_document_path(self):
        names = [p.name for p in document_paths(self.path)]
        self.assertEqual(names, ['CASE_planning.docx', 'CASE_planning.pdf', 'CASE_planning.review.json'])

    def test_invalid_filename(self):
        with self.assertRaises(ValueError):
            document_paths(self.root / 'other.json')

    def test_generate_approve_and_idempotent_approval(self):
        generated = generate_document(self.path)
        self.assertEqual(generated['status'], 'pending')
        self.assertEqual(document_status(self.path)['status'], 'pending')
        approved = approve_document(self.path)
        self.assertEqual(approved['status'], 'approved')
        self.assertEqual(approve_document(self.path), approved)

    def test_docx_has_requirements(self):
        from docx import Document
        generate_document(self.path)
        docx, _, _ = document_paths(self.path)
        doc = Document(docx)
        contents = '\n'.join([p.text for p in doc.paragraphs] +
                             [c.text for table in doc.tables for row in table.rows for c in row.cells])
        for value in ('FEAT-001', '신청 접수 확인', '투표', '중복 신청'):
            self.assertIn(value, contents)

    def test_changes_invalidate_approval(self):
        generate_document(self.path)
        approve_document(self.path)
        self.data['overview']['summary'] = '수정됨'
        self.write()
        self.assertEqual(document_status(self.path)['status'], 'stale')
        with self.assertRaises(ValueError):
            approve_document(self.path)
        generate_document(self.path)
        self.assertEqual(document_status(self.path)['status'], 'pending')

    def test_docx_change_invalidates_approval(self):
        generate_document(self.path)
        docx, _, _ = document_paths(self.path)
        docx.write_bytes(b'bad docx')
        self.assertEqual(document_status(self.path)['status'], 'stale')
        with self.assertRaises(ValueError):
            approve_document(self.path)

    def test_pdf_change_invalidates_approval(self):
        generate_document(self.path)
        _, pdf, _ = document_paths(self.path)
        pdf.write_bytes(b'%PDF-damaged')
        self.assertEqual(document_status(self.path)['status'], 'stale')

    def test_pdf_failure_does_not_erase_approved_document(self):
        generate_document(self.path)
        approve_document(self.path)
        paths = document_paths(self.path)
        before = [p.read_bytes() for p in paths]
        with patch('document.document_flow.convert_docx_to_pdf', side_effect=ValueError('pdf error')):
            with self.assertRaisesRegex(ValueError, 'pdf error'):
                generate_document(self.path)
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertEqual(document_status(self.path)['status'], 'approved')

    def test_bad_input_rejected(self):
        self.path.write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            generate_document(self.path)

    def test_missing_json_rejected(self):
        self.path.unlink()
        with self.assertRaises(ValueError):
            generate_document(self.path)

    def test_corrupted_review_record(self):
        generate_document(self.path)
        _, _, review = document_paths(self.path)
        review.write_text('not json', encoding='utf-8')
        self.assertEqual(document_status(self.path)['status'], 'stale')


if __name__ == '__main__':
    unittest.main()
