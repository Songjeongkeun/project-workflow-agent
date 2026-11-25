"""미처리 요청 및 복구용 상태를 저장한다.

Persist unsuccessful user requests separately from confirmed requirements."""
from hashlib import sha256
from core.planning_schema import PendingInput


def record_failed_input(state, text, reason):
    """failed input 관련 작업을 기록한다"""
    text = text.strip()
    if not text or '\x00' in text:
        return
    digest = sha256(text.encode('utf-8')).hexdigest()
    if any(row.digest == digest and row.status == 'pending' for row in state.input_requests):
        return
    state.input_requests.append(PendingInput(id=f'I{len(state.input_requests)+1:04d}',
        text=text,digest=digest,reason=reason))


def resolve_retried_input(state, text):
    """retried input 관련 작업을 해결한다"""
    text = text.strip()
    digest = sha256(text.encode('utf-8')).hexdigest()
    for request in state.input_requests:
        if request.status == 'pending' and request.digest == digest and request.text == text:
            request.status, request.resolved_by = 'resolved', text
