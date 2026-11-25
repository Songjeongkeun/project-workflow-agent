"""질문 우선순위 오류의 재발을 방지하는 테스트를 정의한다.

Offline regression for V18.42 question tier reference correction.

Run: python -m unittest -v test_question_tier_regression
"""
import unittest
from types import SimpleNamespace
from questions.question_policy import validate_queue_tiers, apply_question_tiers
from questions.simple_questions import QueueTier
from core.planning_schema import Target


def question(qid, kind='missing', path='data_rules.storage_and_management'):
    """question 관련 작업을 질문을 처리한다"""
    return SimpleNamespace(id=qid, kind=kind, target=Target(path=path), answer_hint=None,
                           text='질문 '+qid)


def tier(qid, name='P1', reason='개발 전에 확인이 필요한 선택 정책이다.'):
    """tier 관련 작업을 수행한다"""
    return QueueTier(question_id=qid, tier=name, reason=reason)


class QuestionTierRegression(unittest.TestCase):
    def make_state(self, active=(), deferred=()):
        """state 관련 작업을 수행한다"""
        return SimpleNamespace(questions=list(active), deferred_questions=list(deferred))

    def test_reported_incident_deferred_p1_p1(self):
        """reported incident deferred p1 p1 조건을 검증한다."""
        state=self.make_state(deferred=(question('Q0001'), question('Q0002')))
        self.assertEqual(validate_queue_tiers([tier('Q0001'), tier('Q0002')], state), [])
        initial_ids=[q.id for q in state.deferred_questions]
        assessment=SimpleNamespace(_queue_tiers=[{'question_id':'Q0001','tier':'P1'},
                                                   {'question_id':'Q0002','tier':'P1'}],
                                   _new_question_tiers=[])
        self.assertTrue(apply_question_tiers(state, assessment))
        self.assertEqual([q.id for q in state.deferred_questions],initial_ids)
        self.assertEqual(state.questions,[])

    def test_active_optional_question_can_be_deferred(self):
        """active optional question can be deferred 조건을 검증한다."""
        state=self.make_state(active=(question('Q0003'),))
        self.assertEqual(validate_queue_tiers([tier('Q0003')],state),[])
        assessment=SimpleNamespace(_queue_tiers=[{'question_id':'Q0003','tier':'P1'}],_new_question_tiers=[])
        self.assertTrue(apply_question_tiers(state,assessment))
        self.assertEqual([q.id for q in state.deferred_questions],['Q0003'])

    def test_deferred_p0_requires_explicit_reopen(self):
        """deferred p0 requires explicit reopen 조건을 검증한다."""
        state=self.make_state(deferred=(question('Q0001'),))
        self.assertEqual(validate_queue_tiers([tier('Q0001','P0')],state),
                         ['deferred_question_requires_reopening:Q0001'])

    def test_unknown_question_fails(self):
        """unknown question fails 조건을 검증한다."""
        self.assertEqual(validate_queue_tiers([tier('Q9999')],self.make_state()),
                         ['invalid_question_tier:Q9999'])

    def test_answered_question_is_not_deferred(self):
        """answered question is not deferred 조건을 검증한다."""
        self.assertEqual(validate_queue_tiers([tier('Q0003')],self.make_state()),
                         ['invalid_question_tier:Q0003'])

    def test_duplicate_reference_fails(self):
        """duplicate reference fails 조건을 검증한다."""
        state=self.make_state(deferred=(question('Q0001'),))
        self.assertEqual(validate_queue_tiers([tier('Q0001'),tier('Q0001')],state),
                         ['invalid_question_tier:Q0001'])

    def test_empty_reason_fails(self):
        """empty reason fails 조건을 검증한다."""
        state=self.make_state(deferred=(question('Q0001'),))
        self.assertEqual(validate_queue_tiers([tier('Q0001',reason=' ')],state),
                         ['invalid_question_tier:Q0001'])

    def test_active_conflict_cannot_be_deferred(self):
        """active conflict cannot be deferred 조건을 검증한다."""
        state=self.make_state(active=(question('Q0007',kind='conflict'),))
        self.assertEqual(validate_queue_tiers([tier('Q0007')],state),
                         ['blocking_question_cannot_be_deferred:Q0007'])

    def test_active_p0_preserved(self):
        """active p0 preserved 조건을 검증한다."""
        state=self.make_state(active=(question('Q0009'),))
        self.assertEqual(validate_queue_tiers([tier('Q0009','P0')],state),[])
        assessment=SimpleNamespace(_queue_tiers=[{'question_id':'Q0009','tier':'P0'}],_new_question_tiers=[])
        self.assertFalse(apply_question_tiers(state,assessment))
        self.assertEqual([q.id for q in state.questions],['Q0009'])

    def test_deferred_p2_allowed_without_state_change(self):
        """deferred p2 allowed without state change 조건을 검증한다."""
        state=self.make_state(deferred=(question('Q0001'),))
        self.assertEqual(validate_queue_tiers([tier('Q0001','P2')],state),[])
        self.assertEqual([q.id for q in state.deferred_questions],['Q0001'])


if __name__ == '__main__':
    unittest.main()
