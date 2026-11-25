"""Conservative command parsing and multi-action planning for the CLI.

Do not split arbitrary requirements documents. Unknown natural language is handed
back to the existing extraction/interpretation path.
"""
from __future__ import annotations

import re

CLI_COMMANDS = frozenset({
    'questions', 'question-history', 'suggestions', 'state', 'sources', 'source',
    'pending-inputs', 'retry-input', 'withdraw-input', 'resume', 'defer',
    'propose', 'approve', 'reject', 'document-status', 'approve-document',
    'generate-document', 'export', 'brief', 'handoff',
})

_SIMPLE_NATURAL = (
    (re.compile(r'^(?:(?:지금까지\s*나온|현재|지금)\s*)?(?:질문\s*목록|남은\s*질문|질문)(?:을|도|은)?\s*(?:보여줘|알려줘|조회해줘|확인해줘)[.!?\s]*$'), '/questions'),
    (re.compile(r'^(?:이전\s*)?(?:질문\s*이력|질문\s*기록)(?:을|도|은)?\s*(?:보여줘|알려줘|조회해줘|확인해줘)[.!?\s]*$'), '/question-history'),
    (re.compile(r'^(?:현재\s*)?(?:제안\s*목록|보완\s*제안\s*목록)(?:을|도)?\s*(?:보여줘|알려줘|조회해줘)[.!?\s]*$'), '/suggestions'),
    (re.compile(r'^(?:기획서|문서)(?:의)?\s*(?:승인\s*상태|상태)(?:를|도)?\s*(?:보여줘|알려줘|확인해줘)[.!?\s]*$'), '/document-status'),
    (re.compile(r'^(?:(?:기획서|요구사항)(?:에서|의)\s*)?(?:부족한\s*(?:부분|기능|점)|보완할\s*(?:부분|점|기능))(?:을|도)?\s*(?:찾아줘|검토해줘|알려줘|제안해줘)[.!?\s]*$'), '/propose'),
)
_SPLIT = re.compile(r'\s*(?:,?\s*그리고\s+|,?\s*그다음\s+|,?\s*다음에\s+|(?<=고),?\s+|(?<=한)\s+다음(?:에)?,?\s+)')


# Only complete, short, explicit user requests are interpreted as commands.
# This keeps free-form requirement descriptions on the extract/interpret path.
_REQUEST_END = re.compile(
    r'(?:보여\s*줘|알려\s*줘|조회\s*해\s*줘|확인\s*해\s*줘|'
    r'찾아\s*줘|검토\s*해\s*줘|제안\s*해\s*줘|'
    r'생성\s*해\s*줘|추천\s*해\s*줘|정리\s*해\s*줘)'
    r'[.!?\s]*$'
)
_READ_VERB = re.compile(r'(?:보여|알려|조회|확인|정리)')
_PROPOSE_VERB = re.compile(r'(?:찾아|검토|제안|추천|생성)')


def _natural_command(text: str) -> str | None:
    """Classify clearly requested read/propose actions, without changing requirements."""
    if not _REQUEST_END.search(text):
        return None
    # A list/history request is read-only even when it contains 'AI' or '제안'.
    if re.search(r'질문', text) and _READ_VERB.search(text) and not re.search(r'제안|보완|부족|수정|변경|삭제|승인|반영', text):
        if re.search(r'이력|기록|답변\s*내역', text):
            return '/question-history'
        return '/questions'
    if re.search(r'제안|보완', text) and re.search(r'목록|이력|기존\s*제안', text) and _READ_VERB.search(text) and not re.search(r'생성|새로\s*만들|추가\s*제안|수정|변경|삭제|승인|반영', text):
        return '/suggestions'
    if (re.search(r'제안|보완|부족', text) and _PROPOSE_VERB.search(text)
            and not re.search(r'제안\s*목록|제안\s*이력|수정|변경|삭제|승인|반영', text)):
        return '/propose'
    return None


def command_for(text: str, *, is_proposal_request=None) -> str | None:
    """Returns a canonical slash command, or None; never guesses approvals."""
    raw = text.strip()
    if not raw:
        return None
    if raw.startswith('/'):
        match = re.match(r'^/([a-z-]+)(?:\s|$)', raw, re.I)
        if match and match.group(1).lower() in CLI_COMMANDS:
            return '/' + match.group(1).lower() + raw[match.end(1):]
        return None
    if len(raw) > 160 or '\n' in raw:
        return None
    for pattern, action in _SIMPLE_NATURAL:
        if pattern.fullmatch(raw):
            return action
    generalized = _natural_command(raw)
    if generalized:
        return generalized
    if is_proposal_request and is_proposal_request(raw):
        return '/propose'
    return None


def plan_actions(text: str, *, source_name=None, is_proposal_request=None) -> list[dict]:
    """Split only clearly command-like short utterances; preserve full source texts."""
    if source_name is not None:
        return [{'text': text, 'command': None}]
    # Split first: otherwise the last command verb can mask an earlier action
    # ("질문 보여주고, 기존 제안 목록 보여줘" is *two* reads).
    if len(text) > 350 or '\n' in text:
        return [{'text': text, 'command': None}]
    chunks = [chunk.strip(' ,.;') for chunk in _SPLIT.split(text) if chunk.strip(' ,.;')]
    # Conjunctive Korean verb forms become normal requests *only for parsing*.
    endings = {'보여주고': '보여줘', '알려주고': '알려줘',
               '찾아주고': '찾아줘', '검토하고': '검토해줘',
               '확인하고': '확인해줘', '찾아보고': '찾아줘', '변경하고': '변경해줘',
               '수정하고': '수정해줘', '삭제하고': '삭제해줘',
               '검토한': '검토해줘', '확인한': '확인해줘'}
    normalized = []
    for chunk in chunks:
        for ending, replacement in endings.items():
            if chunk.endswith(ending):
                chunk = chunk[:-len(ending)] + replacement
                break
        normalized.append(chunk)
    if len(chunks) > 1 and len(chunks) <= 8:
        mapped = [command_for(chunk, is_proposal_request=is_proposal_request) for chunk in normalized]
        explicit_change = (len(mapped) == 2 and mapped[0] is None and mapped[1] is not None
                           and bool(re.search(r'(?:변경해줘|수정해줘|삭제해줘|바꿔줘)$', normalized[0])))
        if all(mapped) or explicit_change:
            return [{'text': chunk, 'command': command} for chunk, command in zip(normalized, mapped)]
        # Do not run only the last action when a compound request is ambiguous.
        return [{'text': text, 'command': None}]
    canonical = command_for(text, is_proposal_request=is_proposal_request)
    return [{'text': text, 'command': canonical}]


def classify_readonly_natural(text: str, processor) -> str | None:
    """LLM fallback only for low-risk command intents; never approve or edit."""
    if not isinstance(text, str) or len(text) > 120 or '\n' in text or text.strip().startswith('/'):
        return None
    if not re.search(r'(?:질문|이력|제안|보완|부족|기획서\s*상태)', text):
        return None
    if not re.search(r'(?:보여|알려|찾아|검토|조회|확인|추천|제안해)', text):
        return None
    if re.search(r'(?:수정|삭제|변경|반영|적용|승인|철회|취소|바꿔)', text):
        return None
    model = getattr(processor, 'model', None)
    request = getattr(model, 'request', None)
    if not callable(request):
        return None
    from typing import Literal
    from pydantic import BaseModel
    class Intent(BaseModel):
        action: Literal['questions','question-history','suggestions','propose','document-status','none']
    response = request({'user_message': text},
        '사용자 요청이 조회/AI 보완 제안인지 분류한다. 새 기능 요구사항이나 불명확한 입력은 none. '
        '허용: questions, question-history, suggestions, document-status, propose, none. '
        '요구사항 수정/승인/삭제 의도는 절대 조회 또는 제안으로 바꾸지 않는다.', Intent)
    return None if response.action == 'none' else '/' + response.action
