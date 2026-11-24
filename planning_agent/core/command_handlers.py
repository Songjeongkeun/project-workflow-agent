"""Python-only project management commands used by the LangGraph command node.

No model call occurs here. The Project object still supplies lock, state, and
atomic storage; the CLI/UI share this same code path.
"""
import json
import re
from hashlib import sha256
from core.planning_logic import question_text
from questions.required_check import rank_questions, prune_advisory_questions
from proposals.proposal_flow import handoff_status, proposal_text


def json_text(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def source_command(self, message):
    """저장된 TXT 목록과 원문 경로를 표시한다."""
    rows = (self._read_bytes(self.input_path) or b'').splitlines()
    documents = [row for line in rows if (row := json.loads(line)).get('kind') == 'document']
    if message == '/sources':
        return '\n'.join(f'[{row["document_id"]}] {row["name"]}' for row in documents) or '저장된 TXT 원문이 없어.'
    document_id = message.removeprefix('/source').strip().upper()
    if not re.fullmatch(r'DOC-[0-9]{6,}', document_id):
        raise ValueError('사용법은 /source DOC-000001이야. /sources에서 실제 원문 ID를 확인해 줘.')
    row = next((item for item in documents if item['document_id'] == document_id), None)
    if row is None:
        raise ValueError('이 프로젝트에서 해당 TXT 원문 ID를 찾을 수 없어.')
    path = self._source_path(row['sha256'])
    raw = self._read_bytes(path)
    if raw is None or sha256(raw).hexdigest() != row['sha256']:
        raise ValueError('저장된 TXT 원문을 읽을 수 없거나 내용이 손상됐어.')
    return f'[{document_id}] {row["name"]}\n원문 파일 · {path}'

def execute_command(self, message: str) -> str:
    """CLI 명령을 처리한다."""
    with self.lock:
        state = self.read()
        if message in ('/sources', '/source') or message.startswith('/source '):
            return source_command(self, message)
        if message in ('/generate-document', '/approve-document', '/document-status'):
            from document.document_flow import document_status
            if message == '/document-status':
                return json_text(document_status(self.output_path)).rstrip()
            if not self.output_path.is_file() or not state.cells:
                return '저장된 요구사항이 없어. 먼저 요구사항을 입력해 줘.'
            from planning_graph import invoke_document_graph
            action = 'approve' if message == '/approve-document' else 'generate'
            result = invoke_document_graph(self.processor, state, self.output_path, action=action)
            if action == 'approve':
                return f"기획서를 승인했어.\nPDF · {result['pdf']}\nWord · {result['docx']}"
            return f"기획서 초안을 생성했어.\nPDF · {result['pdf']}\nWord · {result['docx']}"
        if message == '/state':
            return json_text(state.model_dump(mode='json')).rstrip()
        if message == '/handoff':
            return json_text(handoff_status(self.template, state)).rstrip()
        if message == '/questions':
            prune_advisory_questions(state)
            rank_questions(state)
            return (f'[{state.questions[0].id}] {question_text(state,state.questions[0])}' if state.questions else '대기 중인 질문이 없어.')
        if message == '/suggestions':
            return proposal_text(state)
        if message == '/pending-inputs':
            return '\n'.join(f'[{r.id}] {r.reason}\n{r.text}' for r in state.input_requests if r.status == 'pending') or '미해결 입력이 없어.'
        withdrawal = re.fullmatch(r'\s*/withdraw-input\s+(I\d{4,})\s*',message,re.I)
        if withdrawal:
            row = next((r for r in state.input_requests
                        if r.id == withdrawal.group(1).upper() and r.status == 'pending'),None)
            if row is None:
                return '철회할 미처리 입력 ID가 없어. /pending-inputs로 확인해 줘.'
            if not self.state_path.exists():
                raise ValueError('저장된 프로젝트 기록이 없어.')
            row.status='withdrawn'
            row.resolved_by=message.strip()
            self._commit(state)
            return f'{row.id} 요청을 철회했어. 확정 요구사항과 변경 이력은 유지했어.'
        if message == '/question-history':
            latest = {}
            for record in state.required_answer_history:
                latest[record.token] = record
            labels = {'answered':'답변 완료', 'partial':'일부 답변', 'deferred':'보류', 'cancelled':'취소', 'reopened':'다시 열림', 'superseded':'변경되어 재검토 필요'}
            lines = [f"[{record.question.id}] {token} · {('제안 단계로 전환' if record.status=='deferred' and not record.raw_answer and token.split(':',1)[0] in ('done','technology') else labels[record.status])} · {question_text(state, record.question)}"
                     for token, record in latest.items()]
            lines.extend(f'[{token}] 보류 (이전 버전 기록)' for token in state.deferred_required if token not in latest)
            lines.extend(f'[{token}] 답변 완료 (이전 버전 기록)' for token in state.resolved_required if token not in latest)
            lines.extend(f'[{q.id}] 보류 · {question_text(state,q)}' for q in state.deferred_questions)
            return '\n'.join(lines) or '저장된 필수 질문 답변 이력이 없어.'
        if not self.state_path.exists():
            raise ValueError('저장된 프로젝트 기록이 없어.')
        self._commit(state)
        return f'요구사항 JSON을 저장했어. {self.output_path}'

