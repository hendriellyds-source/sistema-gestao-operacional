from __future__ import annotations

import base64
import binascii
import logging
import secrets
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

import imageio_ffmpeg

from app.settings import settings
from app.whatsapp_client import WhatsAppAPIError, WhatsAppConfigurationError, whatsapp_client
from app.whatsapp_store import update_message_media


logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
FILE_TYPES = {
    ".pdf": ("document", "application/pdf"),
    ".txt": ("document", "text/plain"),
    ".doc": ("document", "application/msword"),
    ".docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xls": ("document", "application/vnd.ms-excel"),
    ".xlsx": ("document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".ppt": ("document", "application/vnd.ms-powerpoint"),
    ".pptx": ("document", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
}


def media_root() -> Path:
    configured = str(getattr(settings, "whatsapp_media_root_path", "") or "").strip()
    root = Path(configured) if configured else Path(settings.whatsapp_db_path).parent / "whatsapp_media"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _detected_audio_type(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm", ".webm"
    if content.startswith(b"OggS") and b"OpusHead" in content[:128]:
        return "audio/ogg", ".ogg"
    if content.startswith(b"#!AMR"):
        return "audio/amr", ".amr"
    if len(content) > 2 and content[0] == 0xFF and content[1] & 0xF6 == 0xF0:
        return "audio/aac", ".aac"
    if content.startswith(b"ID3") or (
        len(content) > 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg", ".mp3"
    if len(content) > 12 and content[4:8] == b"ftyp":
        return "audio/mp4", ".m4a"
    return None


def decode_audio_upload(
    *, content_base64: str, filename: str, declared_mime: str | None
) -> tuple[bytes, str, str]:
    encoded = str(content_base64 or "").strip()
    if encoded.startswith("data:") and "," in encoded:
        header, encoded = encoded.split(",", 1)
        if ";base64" not in header.lower():
            raise ValueError("O áudio precisa ser enviado em base64.")
    if len(encoded) > 4 * ((MAX_AUDIO_BYTES + 2) // 3):
        raise ValueError("O áudio ultrapassa o limite de 16 MB.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("O arquivo de áudio está corrompido ou incompleto.") from exc
    if not content:
        raise ValueError("O arquivo de áudio está vazio.")
    if len(content) > MAX_AUDIO_BYTES:
        raise ValueError("O áudio ultrapassa o limite de 16 MB.")
    detected = _detected_audio_type(content)
    if detected is None:
        raise ValueError("Formato de áudio não aceito. Use MP3, M4A, AAC, AMR ou OGG.")
    mime_type, extension = detected
    normalized_declared = str(declared_mime or "").split(";", 1)[0].strip().lower()
    compatible = {
        "audio/mp3": "audio/mpeg",
        "audio/x-m4a": "audio/mp4",
    }.get(normalized_declared, normalized_declared)
    if compatible and compatible not in {mime_type, "application/octet-stream"}:
        raise ValueError("O tipo informado não corresponde ao conteúdo do áudio.")
    if mime_type == "audio/webm":
        content = transcode_webm_to_ogg(content)
        mime_type, extension = "audio/ogg", ".ogg"
    safe_stem = "".join(
        character for character in Path(str(filename or "audio")).stem
        if character.isalnum() or character in "-_"
    )[:60] or "audio"
    return content, f"{safe_stem}{extension}", mime_type


def _safe_filename(filename: str) -> str:
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem = "".join(character for character in Path(name).stem if character.isalnum() or character in "-_. ")[:100].strip(" .")
    extension = Path(name).suffix.lower()
    return f"{stem or 'arquivo'}{extension}"


def _validate_file_content(content: bytes, extension: str) -> None:
    signatures = {
        ".pdf": content.startswith(b"%PDF-"),
        ".jpg": content.startswith(b"\xff\xd8\xff"),
        ".jpeg": content.startswith(b"\xff\xd8\xff"),
        ".png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        ".doc": content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ".xls": content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ".ppt": content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    }
    if extension == ".txt":
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("O arquivo TXT precisa estar em UTF-8.") from exc
        return
    if extension in {".docx", ".xlsx", ".pptx"}:
        expected = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}[extension]
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                valid = expected in archive.namelist()
        except (OSError, ValueError, zipfile.BadZipFile):
            valid = False
    else:
        valid = signatures.get(extension, False)
    if not valid:
        raise ValueError("O conteúdo não corresponde ao formato do arquivo.")


def decode_file_upload(
    *, content_base64: str, filename: str, declared_mime: str | None
) -> tuple[bytes, str, str, str]:
    encoded = str(content_base64 or "").strip()
    if encoded.startswith("data:") and "," in encoded:
        header, encoded = encoded.split(",", 1)
        if ";base64" not in header.lower():
            raise ValueError("O arquivo precisa ser enviado em base64.")
    if len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise ValueError("O arquivo ultrapassa o limite de 20 MB.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("O arquivo está corrompido ou incompleto.") from exc
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("O arquivo está vazio ou ultrapassa o limite de 20 MB.")
    safe_name = _safe_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if extension not in FILE_TYPES:
        raise ValueError("Formato não aceito. Use PDF, TXT, Word, Excel, PowerPoint, JPG ou PNG.")
    media_type, mime_type = FILE_TYPES[extension]
    normalized_declared = str(declared_mime or "").split(";", 1)[0].strip().lower()
    if normalized_declared and normalized_declared not in {mime_type, "application/octet-stream"}:
        raise ValueError("O tipo informado não corresponde à extensão do arquivo.")
    _validate_file_content(content, extension)
    return content, safe_name, mime_type, media_type


def transcode_webm_to_ogg(content: bytes) -> bytes:
    try:
        result = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0", "-vn", "-c:a", "libopus",
                "-b:a", "32k", "-f", "ogg", "pipe:1",
            ],
            input=content,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Não foi possível converter a gravação de áudio.") from exc
    if result.returncode != 0 or not result.stdout.startswith(b"OggS"):
        raise ValueError("A gravação não pôde ser convertida para o WhatsApp.")
    if len(result.stdout) > MAX_AUDIO_BYTES:
        raise ValueError("A gravação convertida ultrapassa o limite de 16 MB.")
    return result.stdout


def save_audio_file(*, content: bytes, extension: str) -> str:
    suffix = extension if extension.startswith(".") else f".{extension}"
    relative = f"{secrets.token_urlsafe(24)}{suffix.lower()}"
    destination = media_root() / relative
    destination.write_bytes(content)
    return relative


def save_media_file(*, content: bytes, extension: str) -> str:
    return save_audio_file(content=content, extension=extension)


def resolve_audio_file(relative_path: str) -> Path:
    root = media_root()
    candidate = (root / str(relative_path or "")).resolve()
    if candidate.parent != root or not candidate.is_file():
        raise FileNotFoundError("Áudio não encontrado.")
    return candidate


def resolve_media_file(relative_path: str) -> Path:
    return resolve_audio_file(relative_path)


def download_and_store_audio(
    *, message_id: str, media_id: str, declared_mime: str | None = None
) -> None:
    try:
        content, downloaded_mime = whatsapp_client.download_media(media_id)
        detected = _detected_audio_type(content)
        if detected is None:
            raise ValueError("O conteúdo recebido não é um áudio suportado.")
        mime_type, extension = detected
        media_path = save_audio_file(content=content, extension=extension)
        update_message_media(
            message_id,
            media_path=media_path,
            media_mime_type=downloaded_mime or declared_mime or mime_type,
            media_filename=f"audio-recebido{extension}",
        )
    except (WhatsAppAPIError, WhatsAppConfigurationError, OSError, ValueError):
        logger.exception("Não foi possível armazenar o áudio recebido do WhatsApp.")


def download_and_store_file(
    *, message_id: str, media_id: str, message_type: str,
    filename: str | None = None, declared_mime: str | None = None,
) -> None:
    try:
        content, downloaded_mime = whatsapp_client.download_media(media_id)
        downloaded_type = str(downloaded_mime or "").split(";", 1)[0].lower()
        declared_type = str(declared_mime or "").split(";", 1)[0].lower()
        supported_types = {kind[1] for kind in FILE_TYPES.values()}
        mime = downloaded_type if downloaded_type in supported_types else declared_type
        extension = Path(str(filename or "")).suffix.lower()
        if not extension:
            extension = next((suffix for suffix, kind in FILE_TYPES.items() if kind == (message_type, mime)), "")
        if extension not in FILE_TYPES or FILE_TYPES[extension] != (message_type, mime):
            raise ValueError("Tipo de arquivo recebido não suportado.")
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("O arquivo recebido ultrapassa 20 MB.")
        _validate_file_content(content, extension)
        media_path = save_media_file(content=content, extension=extension)
        update_message_media(
            message_id,
            media_path=media_path,
            media_mime_type=mime,
            media_filename=_safe_filename(filename or f"arquivo{extension}"),
        )
    except (WhatsAppAPIError, WhatsAppConfigurationError, OSError, ValueError):
        logger.exception("Não foi possível armazenar o arquivo recebido do WhatsApp.")
