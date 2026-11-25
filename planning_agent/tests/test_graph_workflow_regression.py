"""Routing, sequential execution, validation and persistence regression checks.

These are offline tests (no live OpenAI calls). Run together with all legacy tests.
"""
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.action_routing import command_for, plan_actions, classify_readonly_natural
from core.planning_logic import TurnResult
from core.planning_schema import Cell, Evidence, Extraction, ProjectState, Template
from main import Project
from planning_agent import build_processor
from planning_graph import build_planning_graph, invoke_planning_graph, combine_action_results
from questions.required_check import RequiredAssessment, question_key

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / 'templates' / 'planning-template.json'


def processor_for(template):
    return build_processor(template, lambda _: Extraction(operations=[]),
                           assess=lambda _: RequiredAssessment(questions=[]),
                           require_semantic_audits=False, require_quality_audits=False)


class ActionRoutingRegression(unittest.TestCase):
    def test_exact_cli_avoids_llm(self):
        self.assertEqual(command_for('/questions'), '/questions')
        self.assertEqual(command_for('/source DOC-000001'), '/source DOC-000001')
        self.assertIsNone(command_for('/unrecognized'))

    def test_read_only_natural_equivalence(self):
        self.assertEqual(command_for('질문 보여줘'), '/questions')
        self.assertEqual(command_for('이전 질문 이력 보여줘'), '/question-history')
        self.assertEqual(command_for('부족한 부분 찾아줘'), '/propose')

    def test_multi_python_commands_keep_order(self):
        actions = plan_actions('질문 보여주고, 이전 질문 이력 보여줘')
        self.assertEqual([a['command'] for a in actions], ['/questions', '/question-history'])

    def test_three_step_natural_query_and_proposal(self):
        actions = plan_actions('지금까지 나온 질문 보여주고, 기획서에서 부족한 부분 찾아줘. 그리고 이전 질문 이력도 보여줘.')
        self.assertEqual([a['command'] for a in actions],
                         ['/questions', '/propose', '/question-history'])

    def test_multi_python_and_proposal(self):
        actions = plan_actions('질문 보여주고 부족한 부분 찾아줘')
        self.assertEqual([a['command'] for a in actions], ['/questions', '/propose'])

    def test_mixed_edit_and_proposal(self):
        actions = plan_actions('보관 기간을 14일로 변경하고 부족한 부분 찾아줘')
        self.assertEqual([a['command'] for a in actions], [None, '/propose'])
        self.assertIn('14일', actions[0]['text'])

    def test_live_proposal_generation_phrase_routes_to_command(self):
        phrases = (
            '새로운 AI 보완 제안을 생성해 줘.',
            '현재 기획서에서 부족한 기능이나 예외 상황을 찾아서 제안해 줘.',
            '기획서의 부족한 기능을 검토해 줘.',
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual([a['command'] for a in plan_actions(phrase)], ['/propose'])

    def test_live_compound_question_and_suggestions_keeps_both(self):
        self.assertEqual([a['command'] for a in plan_actions(
            '현재 질문 목록을 보여주고, 기존 AI 제안 목록도 보여줘.')],
            ['/questions', '/suggestions'])

    def test_three_action_chain_preserves_generation_before_lookup(self):
        self.assertEqual([a['command'] for a in plan_actions(
            '현재 질문 목록 보여주고, 부족한 부분도 검토한 다음, 제안 목록 보여줘.')],
            ['/questions', '/propose', '/suggestions'])

    def test_requirement_text_and_approval_are_not_commands(self):
        examples = (
            '직원은 로그인한 후 책을 검색하고 대여 신청할 수 있어.',
            '같은 직원이 같은 도서를 다시 신청하면 중복 신청 안내를 보여줘.',
            '도서 대여 신청을 승인해 줘.',
        )
        for phrase in examples:
            with self.subTest(phrase=phrase):
                self.assertEqual(plan_actions(phrase), [{'text': phrase, 'command': None}])

    def test_freeform_requirement_is_not_split(self):
        source = '동아리 회원이 곡을 신청하고 취소할 수 있어. 운영자가 공연 목록을 선정해.'
        self.assertEqual(plan_actions(source), [{'text': source, 'command': None}])

    def test_source_file_is_never_split(self):
        text = '질문 보여줘 그리고 제안 목록 보여줘'
        self.assertEqual(plan_actions(text, source_name='input.txt'),
                         [{'text': text, 'command': None}])

    def test_llm_fallback_readonly_is_bounded(self):
        calls = []
        def request(payload, prompt, schema):
            calls.append(payload['user_message'])
            return SimpleNamespace(action='question-history')
        processor = SimpleNamespace(model=SimpleNamespace(request=request))
        self.assertEqual(classify_readonly_natural('질문 이력 좀 알려줄래?', processor), '/question-history')
        self.assertEqual(classify_readonly_natural('제안을 승인하고 수정해 줘', processor), None)
        self.assertEqual(calls, ['질문 이력 좀 알려줄래?'])


class ProposalDisplayDedupRegression(unittest.TestCase):
    PROPOSAL = ('AI 제안 P0002 (미확정 — 요구사항 JSON에는 아직 반영하지 않았어.)\n'
                '[C1] 로그인 조건\n[E1] 승인 후 취소 방지')

    def test_repeated_proposal_generation_and_lookup_print_once(self):
        result = combine_action_results([
            '대기 중인 질문이 없어.', self.PROPOSAL, self.PROPOSAL,
        ])
        self.assertEqual(result.count('AI 제안 P0002'), 1)
        self.assertIn('대기 중인 질문이 없어.', result)

    def test_blocked_generation_keeps_warning_but_not_duplicate_proposal(self):
        result = combine_action_results([
            '대기 중인 질문이 없어.',
            '기존 제안을 먼저 승인하거나 반려해 줘.\n' + self.PROPOSAL,
            self.PROPOSAL,
        ])
        self.assertEqual(result.count('AI 제안 P0002'), 1)
        self.assertEqual(result.count('기존 제안을 먼저 승인하거나 반려해 줘.'), 1)

    def test_distinct_proposal_ids_remain(self):
        other = self.PROPOSAL.replace('P0002', 'P0003')
        result = combine_action_results([self.PROPOSAL, other])
        self.assertEqual(result.count('AI 제안 P0002'), 1)
        self.assertEqual(result.count('AI 제안 P0003'), 1)

    def test_same_id_but_changed_content_is_preserved(self):
        other = self.PROPOSAL.replace('로그인 조건', '로그인 예외')
        result = combine_action_results([self.PROPOSAL, other])
        self.assertEqual(result.count('AI 제안 P0002'), 2)

    def test_non_proposal_identical_messages_are_untouched(self):
        result = combine_action_results(['대기 중인 질문이 없어.', '대기 중인 질문이 없어.'])
        self.assertEqual(result.count('대기 중인 질문이 없어.'), 2)

    def test_normal_compound_messages_in_order(self):
        result = combine_action_results(['질문 조회 완료', self.PROPOSAL, '문서 상태 확인'])
        self.assertLess(result.index('질문 조회 완료'), result.index('AI 제안 P0002'))
        self.assertLess(result.index('AI 제안 P0002'), result.index('문서 상태 확인'))


class ManagedGraphRegression(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.template = Template(TEMPLATE_PATH)
        self.processor = processor_for(self.template)
        self.project = Project(Path(self.directory.name), 'GRAPH_CHECK',
                               self.template, self.processor)

    def tearDown(self):
        self.directory.cleanup()

    def test_fourteen_nodes_and_legacy_smoke(self):
        # Compiled LangGraph includes the synthetic __start__ entrypoint.
        # Count and verify only the 14 explicitly registered business nodes.
        graph_nodes = set(build_planning_graph(self.processor).nodes) - {'__start__'}
        self.assertEqual(graph_nodes, {
            'route', 'extract', 'interpret', 'command',
            'candidate_validation', 'source_repair', 'review_recovery',
            'source_stop', 'completeness', 'question', 'finish',
            'save_requirements', 'generate_document', 'document_review',
        })
        result = invoke_planning_graph(self.processor, ProjectState(),
                                       '새 프로젝트를 기획해.', 'test.txt')
        self.assertEqual({question_key(q) for q in result.state.questions},
                         {'purpose', 'users', 'features'})

    def test_lookup_has_no_json_write(self):
        self.assertEqual(self.project.turn('/questions'), '대기 중인 질문이 없어.')
        self.assertFalse(self.project.output_path.exists())
        self.assertFalse(self.project.state_path.exists())

    def test_mixed_lookup_aggregates_without_duplicate_responses(self):
        result = self.project.turn('질문 보여줘 그리고 이전 질문 이력 보여줘')
        self.assertEqual(result.count('대기 중인 질문이 없어.'), 1)
        self.assertIn('저장된 필수 질문 답변 이력이 없어.', result)
        self.assertFalse(self.project.output_path.exists())

    def test_empty_manual_document_generation_is_safe(self):
        self.assertEqual(self.project.turn('/generate-document'),
                         '저장된 요구사항이 없어. 먼저 요구사항을 입력해 줘.')

    def test_initial_input_and_state_are_preserved(self):
        response = self.project.turn('새 공연 신청 웹서비스를 만들고 싶어.')
        self.assertIn('핵심 문제', response)
        self.assertTrue(self.project.state_path.is_file())
        self.assertEqual(len(self.project.read().questions), 3)

    def _existing_requirement(self):
        state = ProjectState(cells={'overview.project_name': Cell(value='OLD', evidence=[])})
        self.project._commit(state)
        return state

    @staticmethod
    def _edit_to_new(processor, before, text, source_name=None, *, trace=None, budget=None):
        changed = before.model_copy(deep=True)
        index = text.index('NEW')
        changed.cells['overview.project_name'] = Cell(value='NEW', evidence=[Evidence(
            source='chat', digest=sha256(text.encode('utf-8')).hexdigest(),
            start=index, end=index+3, quote='NEW')])
        return TurnResult(state=changed, updates=['이름을 변경했어.'], saved=1)

    def test_change_commits_then_generates_once(self):
        self._existing_requirement()
        with patch('planning_graph.process_turn', self._edit_to_new), \
             patch('document.document_flow.generate_document', return_value={
                 'pdf': 'generated.pdf', 'docx': 'generated.docx'}) as generated:
            response = self.project.turn('프로젝트명을 NEW로 수정해줘')
        self.assertEqual(generated.call_count, 1)
        self.assertIn('generated.pdf', response)
        self.assertEqual(self.project.read().cells['overview.project_name'].value, 'NEW')
        self.assertIn('NEW', self.project.output_path.read_text(encoding='utf-8'))

    def test_identical_edit_neither_rewrites_json_nor_generates(self):
        self._existing_requirement()
        before = self.project.output_path.read_bytes()
        def no_change(processor, state, text, source_name=None, *, trace=None, budget=None):
            return TurnResult(state=state.model_copy(deep=True), saved=1)
        with patch('planning_graph.process_turn', no_change), \
             patch('document.document_flow.generate_document') as generated:
            response = self.project.turn('프로젝트명을 OLD로 수정해줘')
        self.assertIn('이미 동일한 내용', response)
        generated.assert_not_called()
        self.assertEqual(before, self.project.output_path.read_bytes())

    def test_save_failure_cannot_generate_document(self):
        self._existing_requirement()
        before = self.project.output_path.read_bytes()
        with patch('planning_graph.process_turn', self._edit_to_new), \
             patch.object(self.project, '_commit', side_effect=ValueError('저장 오류')), \
             patch('document.document_flow.generate_document') as generated:
            with self.assertRaisesRegex(ValueError, '저장 오류'):
                self.project.turn('프로젝트명을 NEW로 수정해줘')
        generated.assert_not_called()
        self.assertEqual(before, self.project.output_path.read_bytes())

    def test_document_generation_failure_keeps_json(self):
        self._existing_requirement()
        with patch('planning_graph.process_turn', self._edit_to_new), \
             patch('document.document_flow.generate_document', side_effect=RuntimeError('PDF 실패')):
            response = self.project.turn('프로젝트명을 NEW로 수정해줘')
        self.assertIn('기획서 생성 실패', response)
        self.assertEqual(self.project.read().cells['overview.project_name'].value, 'NEW')

    def test_document_approval_not_automatic_in_multi_action(self):
        self._existing_requirement()
        with patch('document.document_flow.approve_document') as approve:
            response = self.project.turn('질문 보여줘 그리고 /approve-document')
        approve.assert_not_called()
        self.assertIn('별도 메시지로 승인', response)

    def test_edit_then_proposal_uses_new_saved_state(self):
        self._existing_requirement()
        seen = []
        def fake_propose(proc, result, budget, trace, force):
            seen.append(result.state.cells['overview.project_name'].value)
            result.updates.append('새 값 기준 제안 완료')
        with patch('planning_graph.process_turn', self._edit_to_new), \
             patch('planning_graph.create_proposal', fake_propose), \
             patch('document.document_flow.generate_document', return_value={
                 'pdf': 'latest.pdf', 'docx': 'latest.docx'}):
            response = self.project.turn('프로젝트명을 NEW로 수정하고 부족한 부분 찾아줘')
        self.assertEqual(seen, ['NEW'])
        self.assertIn('latest.pdf', response)
        self.assertIn('새 값 기준 제안 완료', response)
        self.assertLess(response.index('latest.pdf'), response.index('새 값 기준 제안 완료'))

    def test_failed_edit_does_not_run_dependent_proposal(self):
        self._existing_requirement()
        def failed_edit(proc, state, text, source_name=None, *, trace=None, budget=None):
            return TurnResult(state=state.model_copy(deep=True),
                              input_rejected=True, reviews=['검증 실패'])
        with patch('planning_graph.process_turn', failed_edit), \
             patch('planning_graph.create_proposal') as proposed, \
             patch('document.document_flow.generate_document') as generated:
            response = self.project.turn('프로젝트명을 NEW로 수정하고 부족한 부분 찾아줘')
        proposed.assert_not_called()
        generated.assert_not_called()
        self.assertIn('검증 실패', response)

    def test_live_two_read_actions_are_both_returned(self):
        response = self.project.turn('현재 질문 목록을 보여주고, 기존 AI 제안 목록도 보여줘.')
        self.assertIn('대기 중인 질문이 없어.', response)
        self.assertIn('검토 대기 중인 제안이 없어.', response)
        self.assertLess(response.index('대기 중인 질문이 없어.'),
                        response.index('검토 대기 중인 제안이 없어.'))
        self.assertFalse(self.project.output_path.exists())

    def test_live_proposal_intent_reaches_command_not_interpret(self):
        self._existing_requirement()
        with patch('planning_graph.create_proposal') as proposed, \
             patch('planning_graph.process_turn', side_effect=AssertionError('interpret should not run')):
            response = self.project.turn('새로운 AI 보완 제안을 생성해 줘.')
        proposed.assert_called_once()
        self.assertNotIn('의미·대상·변경 범위', response)

    def test_actual_managed_graph_does_not_repeat_generated_proposal(self):
        self._existing_requirement()
        proposal = ('AI 제안 P0002 (미확정 — 요구사항 JSON에는 아직 반영하지 않았어.)\n'
                    '[C1] 새 완료 조건')
        def fake_propose(proc, result, budget, trace, force):
            result.updates.append(proposal)
        with patch('planning_graph.create_proposal', fake_propose), \
             patch('core.command_handlers.proposal_text', return_value=proposal):
            response = self.project.turn(
                '현재 질문 목록 보여주고, 부족한 기능도 검토한 다음, 제안 목록 보여줘.')
        self.assertIn('대기 중인 질문이 없어.', response)
        self.assertEqual(response.count(proposal), 1)

    def test_python_lookup_then_llm_proposal_sequential(self):
        self._existing_requirement()
        def fake_propose(proc, result, budget, trace, force):
            result.updates.append('AI 보완 제안 생성')
        with patch('planning_graph.create_proposal', fake_propose):
            response = self.project.turn('질문 보여주고 부족한 부분 찾아줘')
        self.assertLess(response.index('대기 중인 질문이 없어.'), response.index('AI 보완 제안 생성'))


if __name__ == '__main__':
    unittest.main()
