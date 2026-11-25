"""확정 전 기획서 초안 생성과 사용자 승인 상태를 관리한다."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from document.planning_word import convert_docx_to_pdf, render_planning_docx


def _digest(content: bytes) -> str:
    """내용 검증에 사용할 SHA-256 해시를 계산한다."""
    return sha256(content).hexdigest()


def _write_atomic(path: Path, content: bytes) -> None:
    """임시 파일을 사용해 단일 파일을 안전하게 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def document_paths(requirements_path: Path) -> tuple[Path, Path, Path]:
    """요구사항 JSON에 대응하는 Word·PDF·승인 기록 경로를 반환한다."""
    name = requirements_path.name
    if not name.endswith('_requirements.json'):
        raise ValueError('요구사항 JSON 파일명은 *_requirements.json 형식이어야 해.')
    stem = requirements_path.with_name(name.removesuffix('_requirements.json') + '_planning')
    return stem.with_suffix('.docx'), stem.with_suffix('.pdf'), stem.with_suffix('.review.json')


def generate_document(requirements_path: Path) -> dict:
    """현재 JSON을 Word와 PDF로 변환하고 새로운 검토 대기 상태로 기록한다."""
    if not requirements_path.is_file():
        raise ValueError('먼저 저장된 요구사항 JSON이 필요해.')
    raw = requirements_path.read_bytes()
    data = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(data, dict) or not data.get('overview'):
        raise ValueError('기획서로 변환할 요구사항 JSON이 올바르지 않아.')
    docx_path, pdf_path, review_path = document_paths(requirements_path)
    docx = render_planning_docx(data)
    pdf = convert_docx_to_pdf(docx)
    if not docx.startswith(b'PK') or not pdf.startswith(b'%PDF-'):
        raise ValueError('생성된 Word 또는 PDF가 올바르지 않아.')
    # PDF 실패 시 기존 문서와 승인 기록은 변경되지 않는다.
    _write_atomic(docx_path, docx)
    _write_atomic(pdf_path, pdf)
    record = {
        'status': 'pending',
        'requirements_sha256': _digest(raw),
        'docx_sha256': _digest(docx),
        'pdf_sha256': _digest(pdf),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'approved_at': None,
    }
    _write_atomic(review_path, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return {'docx': str(docx_path), 'pdf': str(pdf_path), 'status': 'pending'}


def document_status(requirements_path: Path) -> dict:
    """문서 및 요구사항의 변경 여부를 검사하고 현재 승인 상태를 반환한다."""
    docx_path, pdf_path, review_path = document_paths(requirements_path)
    if not review_path.is_file():
        return {'status': 'missing', 'reason': '생성한 기획서가 없어. /generate-document를 실행해 줘.'}
    try:
        record = json.loads(review_path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('status') not in ('pending', 'approved'):
            raise ValueError('승인 기록의 상태가 올바르지 않아.')
        for path, key in ((requirements_path, 'requirements_sha256'),
                          (docx_path, 'docx_sha256'), (pdf_path, 'pdf_sha256')):
            if not path.is_file() or _digest(path.read_bytes()) != record[key]:
                return {'status': 'stale', 'reason': '요구사항 또는 기획서가 변경됐어. /generate-document로 다시 생성해 줘.'}
        return {'status': record['status'], 'docx': str(docx_path), 'pdf': str(pdf_path),
                'approved_at': record.get('approved_at')}
    except (ValueError, KeyError, TypeError):
        return {'status': 'stale', 'reason': '기획서 승인 기록을 읽을 수 없어. /generate-document로 다시 생성해 줘.'}


def approve_document(requirements_path: Path) -> dict:
    """현재 요구사항과 Word·PDF가 일치하는 경우에만 기획서를 승인한다."""
    status = document_status(requirements_path)
    if status['status'] not in ('pending', 'approved'):
        raise ValueError(status['reason'])
    if status['status'] == 'approved':
        return status
    _, _, review_path = document_paths(requirements_path)
    record = json.loads(review_path.read_text(encoding='utf-8'))
    record['status'] = 'approved'
    record['approved_at'] = datetime.now(timezone.utc).isoformat()
    _write_atomic(review_path, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return document_status(requirements_path)
