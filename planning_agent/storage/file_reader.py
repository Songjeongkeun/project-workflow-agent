"""TXT 입력을 읽고 문서 정보를 구성한다."""

from dataclasses import dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {'.txt'}


@dataclass(frozen=True)
class DocumentInput:
    text: str
    name: str
    raw: bytes
    evidence_offset: int
    review_notes: tuple[str, ...] = ()


def decode_text(data: bytes) -> str:
    """텍스트 파일의 인코딩을 확인하고 읽는다."""
    if data.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        return data.decode('utf-32')
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            return data.decode('cp949')
        except UnicodeDecodeError as exc:
            raise ValueError('텍스트 인코딩을 읽을 수 없어. UTF-8로 저장해 줘.') from exc


def read_document(filename: str) -> DocumentInput:
    """지원 문서를 읽고 입력 텍스트를 검증한다."""
    requested = Path(filename).expanduser()
    path = requested.resolve()
    if not path.is_file():
        raise FileNotFoundError(f'파일을 찾을 수 없어: {path}')
    limit = 25 * 1024 * 1024
    if path.stat().st_size > limit:
        raise ValueError('25MB 이하의 파일을 사용해 줘.')
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f'지원하지 않는 형식이야. 지원 형식은 {", ".join(sorted(SUPPORTED_SUFFIXES))}야.')
    with path.open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('25MB 이하의 파일을 사용해 줘.')
    original = decode_text(raw)
    text = original.strip()
    if not text or '\x00' in original:
        raise ValueError('읽을 수 있는 텍스트가 없어. 파일 형식과 인코딩을 확인해 줘.')
    return DocumentInput(text=text, name=requested.name, raw=raw,
                         evidence_offset=len(original) - len(original.lstrip()))
