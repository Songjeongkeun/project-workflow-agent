"""의미 교정 시 명시된 행만 삭제하도록 제한하는 회귀 테스트."""
import unittest
from extraction.structured_initial import (StructuredDraft, StructuredField, StructuredPatch,
    StructuredPatchTarget, inspect_semantic_patch, referenced_repair_rows)


class SemanticRepairScopeTests(unittest.TestCase):
    """실제 진단 로그의 분류 정정과 허용되지 않은 변경을 검증한다."""

    def setUp(self):
        """같은 섹션의 여러 행과 다른 섹션의 행을 준비한다."""
        self.draft = StructuredDraft(fields=[
            StructuredField(path='data_rules.fields.name', local_key='예약 정보 삭제 시점', value='예약 정보 삭제 시점'),
            StructuredField(path='data_rules.fields.purpose', local_key='예약 정보 삭제 시점', value='예약 삭제 시점 기록'),
            StructuredField(path='data_rules.fields.source', local_key='예약 정보 삭제 시점', value='예약 시스템'),
            StructuredField(path='data_rules.fields.related_features', local_key='예약 정보 삭제 시점', feature_keys=['곡 신청']),
            StructuredField(path='data_rules.fields.name', local_key='예약 번호', value='예약 번호'),
            StructuredField(path='release_scope.features.name', local_key='예약 정보 삭제 시점', value='동명 기능'),
        ])
        self.issues = ["'예약 정보 삭제 시점'을 예약 데이터 필드로 분류했지만, 원문은 이를 실제 저장 필드가 아니라 수령 완료 후 30일 뒤 삭제하는 data_rules.storage_and_management 정책으로 명시한다."]
        self.paths = {'data_rules.storage_and_management', 'release_scope.supported_platforms'}
        self.rows = referenced_repair_rows(self.issues, self.draft, self.paths)

    def test_reported_remove_is_accepted(self):
        """검토자가 명시한 같은 데이터 행의 잘못된 필드만 제거한다."""
        patch = StructuredPatch(remove=[StructuredPatchTarget(path=p,local_key='예약 정보 삭제 시점')
             for p in ('data_rules.fields.name','data_rules.fields.purpose','data_rules.fields.source','data_rules.fields.related_features')])
        self.assertEqual(inspect_semantic_patch(self.draft,patch,self.paths,self.rows),[])

    def test_other_row_is_rejected(self):
        """동일 컬렉션이라도 다른 행은 변경할 수 없다."""
        patch=StructuredPatch(remove=[StructuredPatchTarget(path='data_rules.fields.name',local_key='예약 번호')])
        self.assertIn('semantic_repair_scope_violation', inspect_semantic_patch(self.draft,patch,self.paths,self.rows)[0])

    def test_other_section_same_name_is_rejected(self):
        """이름이 같아도 검토 섹션 밖의 행 변경은 차단한다."""
        patch=StructuredPatch(remove=[StructuredPatchTarget(path='release_scope.features.name',local_key='예약 정보 삭제 시점')])
        self.assertIn('semantic_repair_scope_violation', inspect_semantic_patch(self.draft,patch,self.paths,self.rows)[0])

    def test_nonexistent_row_is_rejected(self):
        """존재하지 않는 행의 제거는 검증을 통과할 수 없다."""
        patch=StructuredPatch(remove=[StructuredPatchTarget(path='data_rules.fields.name',local_key='없는 행')])
        self.assertNotEqual(inspect_semantic_patch(self.draft,patch,self.paths,self.rows),[])

    def test_regular_review_path_is_still_allowed(self):
        """검토 대상 경로의 일반적인 수정은 이전대로 허용한다."""
        patch=StructuredPatch(upsert=[StructuredField(path='release_scope.supported_platforms',value=['웹'])])
        self.assertEqual(inspect_semantic_patch(self.draft,patch,self.paths,self.rows),[])

    def test_no_named_row_means_no_row_permission(self):
        """행 이름을 명시하지 않은 피드백은 전체 행 수정 권한을 부여하지 않는다."""
        rows=referenced_repair_rows(['데이터 보관 정책이 잘못되었다.'],self.draft,self.paths)
        self.assertEqual(rows,set())

    def test_no_reviewed_section_means_no_row_permission(self):
        """관련 섹션이 검토 범위에 없다면 행 변경을 허용하지 않는다."""
        rows=referenced_repair_rows(self.issues,self.draft,{'goals.expected_change'})
        self.assertEqual(rows,set())


if __name__ == '__main__':
    unittest.main()
