"""CLI 실행, 프로젝트 저장·복원 및 JSON 내보내기를 담당한다."""

import argparse
import base64
import os
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4
import json
from pathlib import Path
import re
import sys
import tempfile

from dotenv import load_dotenv
from filelock import FileLock, Timeout
from pydantic import ValidationError

from storage.file_reader import DocumentInput, read_document
from planning_agent import ModelError, build_processor, process
from core.planning_logic import question_text
from questions.required_check import rank_questions, prune_advisory_questions
from proposals.proposal_flow import handoff_status, proposal_text, record_source_review
from core.planning_schema import ProjectState, Template, validate_state


HERE = Path(__file__).resolve().parent
SHELL_LAUNCH = re.compile(
    r'(?:^|[>\s"])(?:[^\s"]+[\\/])?(?:python(?:3(?:\.\d+)?)?(?:\.exe)?|py(?:\.exe)?)["]?\s+'
    r'(?:-\S+(?:\s+utf8)?\s+)*(?:[^\s"]+[\\/])?main\.py(?:\s|$)',
    re.IGNORECASE,
)


def atomic_bytes(path: Path, data: bytes):
    """임시 파일을 기록하고 원본을 원자적으로 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def atomic_write(path: Path, text: str):
    """문자열을 UTF-8 파일로 저장한다."""
    atomic_bytes(path, text.encode('utf-8'))


def json_text(data) -> str:
    """JSON을 읽기 쉬운 문자열로 변환한다."""
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


class Project:
    def __init__(self, root: Path, name: str, template: Template, processor,
                 *, diagnostics: bool = False):
        """프로젝트의 저장 경로와 실행기를 초기화한다."""
        self.directory = root / 'projects'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / f'{name}.json'
        self.journal_path = self.directory / f'{name}.pending-commit.json'
        self.input_path = self.directory / 'input_history' / f'{name}.jsonl'
        self.source_dir = self.directory / 'sources' / name
        self.output_path = root / 'outputs' / f'{name}_requirements.json'
        self.history_dir = self.output_path.parent / 'history' / name
        self.lock = FileLock(str(self.directory / f'{name}.lock'), timeout=0)
        self.template = template
        self.processor = processor
        self.diagnostics = diagnostics
        self.diagnostic_dir = root / 'diagnostics' / name
        self.last_diagnostic: Path | None = None

    def _read_bytes(self, path):
        """파일이 없으면 빈 상태를 반환한다."""
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def _history_paths(self):
        """버전 이력 파일 목록을 읽는다."""
        return {p.name: p for p in self.history_dir.glob('*.json')
                if p.stem.isascii() and p.stem.isdecimal()}

    def _begin_commit(self, new_source_digest=None):
        """쓰기 전 상태를 복구할 수 있도록 저장한다."""
        snapshots = {}
        for key, path in self._commit_paths().items():
            data = self._read_bytes(path)
            snapshots[key] = None if data is None else base64.b64encode(data).decode('ascii')
        journal = {'version': 1, 'files': snapshots, 'history': sorted(self._history_paths())}
        if new_source_digest is not None:
            journal['new_source_digest'] = new_source_digest
        atomic_write(self.journal_path, json_text(journal))

    def _commit_paths(self):
        """복구 가능한 파일 범위를 고정한다."""
        return {'state': self.state_path, 'input': self.input_path, 'output': self.output_path}

    def _source_path(self, digest):
        """원문 해시로 프로젝트 안의 저장 경로를 찾는다."""
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('TXT 원문 식별자가 올바르지 않아.')
        return self.source_dir / f'{digest}.txt'

    def _recover_commit(self):
        """중단된 저장을 이전 상태로 되돌린다."""
        if not self.journal_path.exists():
            return
        try:
            journal = json.loads(self.journal_path.read_bytes())
            paths = self._commit_paths()
            if journal['version'] != 1 or set(journal['files']) != set(paths):
                raise ValueError('저장 복구 기록 형식 오류')
            history = journal['history']
            if not isinstance(history, list) or any(not isinstance(n, str) or not re.fullmatch(r'[0-9]+\.json', n) for n in history):
                raise ValueError('저장 복구 이력 형식 오류')
            new_source = self._source_path(journal['new_source_digest']) if 'new_source_digest' in journal else None
            snapshots = {key: None if data is None else base64.b64decode(data, validate=True)
                         for key, data in journal['files'].items()}
            for key, path in paths.items():
                before = snapshots[key]
                if self._read_bytes(path) != before:
                    if before is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_bytes(path, before)
            for name, path in self._history_paths().items():
                if name not in history:
                    path.unlink()
            if new_source is not None:
                new_source.unlink(missing_ok=True)
            self.journal_path.unlink()
        except Exception as exc:
            raise ValueError('이전 저장 복구가 완료되지 않았어. 파일 잠금과 권한을 확인해 줘. 복구 기록은 보존했어.') from exc

    def _commit(self, state: ProjectState, input_text=None, *, document: DocumentInput | None = None):
        """상태와 결과 및 이력을 한 번의 저장 작업으로 처리한다."""
        validate_state(self.template, state)
        self._recover_commit()
        document_id, new_source_digest = None, None
        try:
            record_input = document is not None or input_text is not None
            previous = (self._read_bytes(self.input_path) or b'') if record_input else b''
            if previous and not previous.endswith(b'\n'):
                raise ValueError('입력 기록의 마지막 줄이 손상됐어. 기존 입력 기록을 확인해 줘.')
            entry = {'received_at': datetime.now(timezone.utc).isoformat()}
            if document is not None:
                digest = sha256(document.raw).hexdigest()
                source_path = self._source_path(digest)
                archived = self._read_bytes(source_path)
                if archived is None:
                    new_source_digest = digest
                elif archived != document.raw:
                    raise ValueError('저장된 TXT 원문이 손상됐어. 원문 기록을 확인해 줘.')
                sequence = previous.count(b'\n') + 1
                document_id = f'DOC-{sequence:06d}'
                entry.update(kind='document', document_id=document_id, name=document.name, sequence=sequence,
                             sha256=digest, evidence_digest=sha256(document.text.encode('utf-8')).hexdigest(),
                             evidence_offset=document.evidence_offset)
            elif input_text is not None:
                entry['text'] = input_text
            self._begin_commit(new_source_digest)
            if new_source_digest is not None:
                atomic_bytes(source_path, document.raw)
            self.export(state)
            if record_input:
                atomic_write(self.input_path, previous.decode('utf-8') + json.dumps(entry, ensure_ascii=False) + '\n')
            atomic_write(self.state_path, state.model_dump_json(indent=2) + '\n')
            self.journal_path.unlink()
        except BaseException as exc:
            self._recover_commit()
            if isinstance(exc, OSError):
                raise ValueError('저장에 실패했어. 기존 상태와 JSON을 유지했어. 파일 잠금이나 권한을 확인하고 같은 입력을 다시 실행해 줘.') from exc
            raise
        return document_id

    def read(self) -> ProjectState:
        """저장된 프로젝트 상태를 읽고 검증한다."""
        with self.lock:
            self._recover_commit()
            try:
                state = ProjectState.model_validate_json(self.state_path.read_bytes())
                validate_state(self.template, state)
                return state
            except FileNotFoundError:
                return ProjectState()
            except ValidationError as exc:
                raise ValueError('프로젝트 상태 파일을 읽을 수 없어. 기존 상태 파일을 확인해 줘.') from exc

    def export(self, state: ProjectState):
        """저장된 상태를 요구사항 JSON으로 내보낸다."""
        document = self.template.export(state)
        serialized = json_text(document)
        current = self.output_path.read_text(encoding='utf-8-sig') if self.output_path.is_file() else None
        latest = max((p for p in self.history_dir.glob('*.json') if p.stem.isascii() and p.stem.isdecimal()),
                     key=lambda p: int(p.stem), default=None)
        if latest:
            previous = json.loads(latest.read_text(encoding='utf-8'))['after']
        else:
            try:
                previous = json.loads(current) if current is not None else None
            except json.JSONDecodeError:
                previous = current
        if previous != document:
            version = int(latest.stem) + 1 if latest else 1
            atomic_write(self.history_dir / f'{version:06d}.json',
                         json_text({'before': previous, 'after': document}))
        if current != serialized:
            atomic_write(self.output_path, serialized)

    def turn(self, text: str | DocumentInput, name: str | None = None, notes: tuple[str, ...] = ()) -> str:
        """Execute sequential CLI/natural-language actions in one LangGraph run."""
        document = text if isinstance(text, DocumentInput) else None
        if document is not None:
            text, name, notes = document.text, document.name, document.review_notes
        with self.lock:
            events = []
            state = None
            self.last_diagnostic = None
            original_text = text
            persisted_first_input = False

            def trace(stage: str, data: dict):
                events.append({'stage': stage, 'at': datetime.now(timezone.utc).isoformat(),
                               'data': data})

            def execute_command(command_text: str):
                """Reuse the existing Python CLI command without a second implementation."""
                reply = self.command(command_text)
                return reply, self.read()

            def persist_action(result, action_text: str, before: ProjectState, is_first: bool):
                nonlocal persisted_first_input
                if is_first:
                    result.reviews.extend(notes)
                if result.input_rejected:
                    from storage.request_state import record_failed_input
                    record_failed_input(result.state, action_text, '; '.join(result.reviews))
                elif (result.source_review_verified or result.request_verified) and not result.blocking_reviews and not result.replay:
                    from storage.request_state import resolve_retried_input
                    resolve_retried_input(result.state, action_text)
                record_source_review(self.template, before, result)
                previous = self._read_bytes(self.output_path)
                record_input = not persisted_first_input
                input_text = (original_text if record_input and document is None and name is None
                              and original_text.strip() and '\x00' not in original_text else None)
                document_id = self._commit(result.state, input_text,
                                           document=document if record_input else None)
                persisted_first_input = True
                updated = self._read_bytes(self.output_path)
                requirements_changed = (previous != updated and bool(result.state.cells)
                                        and not result.input_rejected)
                if self.diagnostics:
                    trace('persisted', {'state_path': str(self.state_path),
                                        'output_path': str(self.output_path),
                                        'requirements_changed': requirements_changed,
                                        'warnings': list(dict.fromkeys([*result.reviews,*result.advisory_reviews])),
                                        'source_review_status': result.state.source_review_status,
                                        'assessment_pending': result.state.assessment_pending,
                                        'proposal_generation_status': result.state.proposal_generation_status})
                return {'document_id': document_id,
                        'requirements_changed': requirements_changed}

            try:
                state = self.read()
                if document is None and name is None and isinstance(text, str):
                    retry = re.fullmatch(r'\s*/retry-input\s+(I\d{4,})\s*', text, re.I)
                    if retry:
                        request = next((r for r in state.input_requests
                                        if r.id == retry.group(1).upper() and r.status == 'pending'), None)
                        if request is None:
                            return '재시도할 미처리 입력 ID가 없어. /pending-inputs로 확인해 줘.'
                        if self.diagnostics:
                            trace('pending_input_retry_requested', {'request_id': request.id})
                        text = request.text
                if document is None and name is None:
                    from planning_graph import is_proposal_request
                    if (text.strip().lower() == '/propose' or is_proposal_request(text)) and not state.cells:
                        if self.diagnostics:
                            trace('proposal_blocked_empty_requirements', {'reason': 'no_confirmed_requirements'})
                        return '저장된 요구사항이 없어 AI 제안을 생성할 수 없어. 먼저 자연어 요구사항을 입력하고 추출·검증을 완료해 줘.'
                if name is not None or not re.match(r'^\s*(?:Q\d+\s*[:：]|/resume\b)', text, re.I):
                    prune_advisory_questions(state)
                    rank_questions(state)
                from planning_graph import invoke_managed_graph
                result = invoke_managed_graph(self.processor, state, text,
                                              source_name=name,
                                              trace=trace if self.diagnostics else None,
                                              requirements_path=self.output_path,
                                              persist_action=persist_action,
                                              execute_command=execute_command)
                return result
            except Exception as exc:
                if state is not None and isinstance(exc, (ModelError, ConnectionError, TimeoutError)):
                    from storage.request_state import record_failed_input
                    failed = self.read().model_copy(deep=True)
                    record_failed_input(failed, original_text, str(exc))
                    try:
                        self._commit(failed, original_text if document is None else None, document=document)
                    except (OSError, ValueError):
                        pass
                if self.diagnostics:
                    trace('failed', {'error_type': type(exc).__name__, 'message': str(exc),
                                     'issues': list(getattr(exc, 'issues', ()))})
                raise
            finally:
                if self.diagnostics:
                    try:
                        self._write_diagnostics(events)
                    except (ValueError, OSError) as exc:
                        print(f'주의 · {exc}', file=sys.stderr)

    def _write_diagnostics(self, events: list[dict]):
        """수집한 진단 기록을 파일에 저장한다."""
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        path = self.diagnostic_dir / f'{stamp}_{uuid4().hex[:8]}.jsonl'
        try:
            atomic_write(path, ''.join(json.dumps(event, ensure_ascii=False) + '\n'
                                        for event in events))
        except OSError as exc:
            raise ValueError('진단 로그를 저장하지 못했어. diagnostics 폴더 권한을 확인해 줘.') from exc
        self.last_diagnostic = path

    def _source_command(self, message):
        """Compatibility wrapper: original source command API."""
        from core.command_handlers import source_command
        return source_command(self, message)

    def command(self, message: str) -> str:
        """Public command interface, also used by LangGraph command node."""
        from core.command_handlers import execute_command
        return execute_command(self, message)


def parse_args(argv):
    """실행 인자를 해석한다."""
    parser = argparse.ArgumentParser(description='자연어와 파일에서 기획 요구사항 추출')
    parser.add_argument('--project', default='P001', help='같은 프로젝트 ID로 다음 답변과 변경을 이어서 저장')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--file', help='입력 TXT 파일 경로')
    group.add_argument('--text', help='자연어 입력, 확인 질문 답변 또는 수정 요청')
    group.add_argument('--export', action='store_true', help='저장된 프로젝트 기록을 JSON으로 다시 내보내기')
    group.add_argument('--handoff', action='store_true', help='현재 요구사항의 인계 상태와 검토된 본문을 조회')
    parser.add_argument('--once', action='store_true', help='한 번 처리하고 종료. 답변은 같은 ID로 다시 실행')
    parser.add_argument('--diagnostics', action='store_true', help='진단 로그 저장(원문 인용·LLM 응답 포함, 민감정보 주의)')
    parser.add_argument('--check-graph', action='store_true', help='실행 중인 LangGraph 코드와 필수 질문 연결 확인')
    parser.add_argument('--data-dir', type=Path, default=HERE, help='프로젝트와 결과를 저장할 폴더')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.project) or args.project.upper() in {
        'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)), *(f'LPT{n}' for n in range(1, 10)),
    }:
        parser.error('프로젝트 ID에 영문, 숫자, -, _를 사용해 줘. Windows 예약 이름은 사용할 수 없어.')
    if args.once and args.text is None and args.file is None and not args.export and not args.handoff and not args.check_graph:
        parser.error('--once는 --file, --text, --export 또는 --handoff와 함께 사용해 줘.')
    return args


def main(argv=None, *, extract=None) -> int:
    """명령줄 실행을 시작한다."""
    args = parse_args(argv)
    if args.check_graph:
        import inspect
        import langgraph.graph
        from planning_graph import invoke_planning_graph
        from planning_agent import PlanningProcessor
        print('A01-STEP01-REQUIREMENTS-18.42 | LangGraph active')
        print('main.py:', Path(__file__).resolve())
        print('planning_graph.py:', Path(inspect.getfile(invoke_planning_graph)).resolve())
        print('planning_agent.py:', Path(inspect.getfile(PlanningProcessor)).resolve())
        print('graph library:', Path(inspect.getfile(langgraph.graph)).resolve())


        from core.planning_schema import Extraction
        from questions.required_check import RequiredAssessment, question_key
        smoke_template = Template(HERE / 'templates' / 'planning-template.json')
        smoke_processor = build_processor(
            smoke_template,
            lambda _: Extraction(operations=[]),
            assess=lambda _: RequiredAssessment(questions=[]),
        )
        smoke_result = invoke_planning_graph(smoke_processor, ProjectState(),
                                             '새 프로젝트를 기획해.', 'graph_smoke.txt')
        smoke_keys = {question_key(q) for q in smoke_result.state.questions}
        if smoke_keys != {'purpose','users','features'}:
            raise RuntimeError('LangGraph 필수 질문 분기 스모크 테스트 실패')
        print('LangGraph runtime smoke: PASS (실제 그래프 실행, API 호출 없음)')
        return 0
    load_dotenv(HERE / '.env')
    template = Template(HERE / 'templates' / 'planning-template.json')
    project = Project(args.data_dir.resolve(), args.project, template, build_processor(template, extract),
                      diagnostics=args.diagnostics)
    initial = args.text
    file = args.file
    if args.export:
        initial = '/export'
    elif args.handoff:
        print(project.command('/handoff'))
        return 0
    resume = initial is None and file is None
    while True:
        project.last_diagnostic = None
        try:
            if resume:
                resume = False
                with project.lock:
                    state = project.read()
                    prune_advisory_questions(state)
                    rank_questions(state)
                if state.questions:
                    print(question_text(state,state.questions[0]))
                elif any(request.status == 'pending' for request in state.input_requests):
                    print('미해결 입력이 있어. /pending-inputs로 확인한 뒤 /retry-input I번호 또는 /withdraw-input I번호를 사용해 줘.')
                else:
                    print(proposal_text(state) if any(b.status=='pending' for b in state.advisory_batches) else '대기 중인 질문이 없어. 요구사항을 입력해 줘.')
            if file is not None:
                path, file = file, None
                document = read_document(path)
                print(project.turn(document))
                print(f'결과 JSON · {project.output_path}')
                if project.last_diagnostic:
                    print(f'진단 로그 · {project.last_diagnostic}')
            else:
                if initial is not None:
                    raw_message, initial = initial, None
                    from_prompt = False
                else:
                    raw_message = input('\n사용자> ')
                    from_prompt = True
                message = raw_message.strip()
                if message == '/exit':
                    return 0
                if not message and from_prompt:
                    continue
                if from_prompt and SHELL_LAUNCH.search(message):
                    print('/exit로 돌아간 뒤 CMD에서 실행해 줘.')
                    continue
                if message.startswith('/load '):
                    file = message[6:].strip().strip('"')
                    continue
                elif message.startswith('/') and from_prompt and not re.search(r'\s', message) and message not in ('/defer','/propose') and not re.match(r'^/(?:questions|question-history|suggestions|pending-inputs|export|brief|sources|source|handoff|generate-document|approve-document|document-status|state|withdraw-input|retry-input|resume|approve|reject)\b', message):
                    print('사용 가능한 명령은 /load 파일경로, /sources, /source 문서ID, /questions, /question-history, /pending-inputs, /retry-input I번호, /withdraw-input I번호, /resume Q번호, /defer, /propose, /suggestions, /approve 제안ID, /reject 제안ID, /state, /export, /generate-document, /document-status, /approve-document, /exit야.')
                    continue
                else:
                    print(project.turn(raw_message))
                    print(f'결과 JSON · {project.output_path}')
                if project.last_diagnostic:
                    print(f'진단 로그 · {project.last_diagnostic}')
            if args.once or args.export:
                return 0
        except (KeyboardInterrupt, EOFError):
            return 0
        except (ValueError, OSError, RuntimeError, Timeout) as exc:
            message = '같은 프로젝트를 처리 중인 실행 창이 있어.' if isinstance(exc, Timeout) else str(exc)
            print(f'오류 · {message}', file=sys.stderr)
            if args.diagnostics:
                for issue in list(getattr(exc,'issues',()))[:3]:
                    print(f'검토 사유 · {issue}',file=sys.stderr)
            if project.last_diagnostic:
                print(f'진단 로그 · {project.last_diagnostic}',file=sys.stderr)
            if args.once or args.export:
                return 1


if __name__ == '__main__':
    raise SystemExit(main())
