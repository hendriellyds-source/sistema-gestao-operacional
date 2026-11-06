from __future__ import annotations

import base64
import binascii
import secrets
from pathlib import Path
from typing import Any

from .supabase_client import create_admin_supabase


ALLOWED_PRIVATE_FILES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
EMPLOYEE_CERTIFICATES_BUCKET = "employee-certificates"
FINANCIAL_ATTACHMENTS_BUCKET = "financial-calendar-attachments"
PROFILE_PHOTOS_BUCKET = "user-profile-photos"


class PrivateStorageError(RuntimeError):
    pass


def _safe_component(value: str, fallback: str) -> str:
    filtered = "".join(
        char for char in str(value or "") if char.isalnum() or char in "-_"
    )
    return filtered[:100] or fallback


def _detected_mime(content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def decode_private_file(
    *,
    filename: str,
    content_base64: str,
    declared_mime: str | None,
    max_bytes: int,
) -> tuple[bytes, str, str, str]:
    original_name = Path(str(filename or "").replace("\\", "/")).name
    extension = Path(original_name).suffix.lower()
    expected_mime = ALLOWED_PRIVATE_FILES.get(extension)
    if not original_name or expected_mime is None:
        raise ValueError("Formato não permitido. Envie somente PDF, JPG ou PNG.")

    encoded = str(content_base64 or "").strip()
    if encoded.startswith("data:") and "," in encoded:
        header, encoded = encoded.split(",", 1)
        if ";base64" not in header.lower():
            raise ValueError("O arquivo precisa ser enviado em base64.")
    max_encoded_length = 4 * ((int(max_bytes) + 2) // 3)
    if len(encoded) > max_encoded_length:
        limit_mb = int(max_bytes) / (1024 * 1024)
        raise ValueError(f"O arquivo ultrapassa o limite de {limit_mb:.0f} MB.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("O arquivo enviado está corrompido ou incompleto.") from exc
    if not content:
        raise ValueError("O arquivo está vazio.")
    if len(content) > int(max_bytes):
        limit_mb = int(max_bytes) / (1024 * 1024)
        raise ValueError(f"O arquivo ultrapassa o limite de {limit_mb:.0f} MB.")

    detected_mime = _detected_mime(content)
    if detected_mime is None or detected_mime != expected_mime:
        raise ValueError("O conteúdo do arquivo não corresponde à extensão informada.")
    normalized_declared = str(declared_mime or "").split(";", 1)[0].strip().lower()
    if normalized_declared and normalized_declared != expected_mime:
        raise ValueError("O tipo MIME informado não corresponde ao arquivo.")
    return content, original_name, extension, detected_mime


def random_object_path(
    *,
    organization_id: str,
    namespace: str,
    owner_id: str,
    extension: str,
) -> str:
    # All components except the generated token originate from trusted server-side
    # records and are reduced to safe characters. Browser-provided paths are ignored.
    return "/".join(
        (
            _safe_component(organization_id, "organization"),
            _safe_component(namespace, "files"),
            _safe_component(owner_id, "item"),
            f"{secrets.token_urlsafe(24)}{extension}",
        )
    )


def validate_private_object_reference(
    *,
    bucket: str,
    object_path: str,
    expected_bucket: str,
    organization_id: str,
    namespace: str,
    owner_id: str,
) -> tuple[str, str]:
    normalized_bucket = str(bucket or "")
    normalized_path = str(object_path or "")
    expected_prefix = "/".join(
        (
            _safe_component(organization_id, "organization"),
            _safe_component(namespace, "files"),
            _safe_component(owner_id, "item"),
            "",
        )
    )
    filename = normalized_path.removeprefix(expected_prefix)
    suffix = Path(filename).suffix.lower()
    stem = filename[: -len(suffix)] if suffix else ""
    if normalized_bucket != expected_bucket:
        raise PrivateStorageError("Bucket privado incompatível com este registro.")
    if (
        not normalized_path.startswith(expected_prefix)
        or normalized_path.startswith("/")
        or ".." in normalized_path.split("/")
        or "\\" in normalized_path
        or "/" in filename
        or suffix not in ALLOWED_PRIVATE_FILES
        or not stem
        or any(not (char.isalnum() or char in "-_") for char in stem)
    ):
        raise PrivateStorageError("Referência de arquivo privado inválida.")
    return normalized_bucket, normalized_path


def upload_private_object(
    *,
    bucket: str,
    object_path: str,
    content: bytes,
    mime_type: str,
) -> None:
    try:
        (
            create_admin_supabase()
            .storage.from_(bucket)
            .upload(
                object_path,
                content,
                file_options={"content-type": mime_type, "upsert": "false"},
            )
        )
    except Exception as exc:
        raise PrivateStorageError(
            f"Não foi possível gravar no bucket privado '{bucket}'. "
            "Confirme a criação manual do bucket e das políticas."
        ) from exc


def signed_private_url(
    *,
    bucket: str,
    object_path: str,
    expires_in: int,
) -> str:
    try:
        result: Any = (
            create_admin_supabase()
            .storage.from_(bucket)
            .create_signed_url(object_path, int(expires_in))
        )
    except Exception as exc:
        raise PrivateStorageError(
            "Não foi possível gerar o link temporário do arquivo privado."
        ) from exc
    if isinstance(result, dict):
        url = result.get("signedURL") or result.get("signed_url")
    else:
        url = getattr(result, "signed_url", None)
    if not url:
        raise PrivateStorageError("O Supabase não retornou uma URL temporária.")
    return str(url)


def delete_private_object(*, bucket: str, object_path: str) -> None:
    if not object_path or object_path.startswith("/") or ".." in object_path.split("/"):
        raise PrivateStorageError("Referência de arquivo privado inválida.")
    try:
        create_admin_supabase().storage.from_(bucket).remove([object_path])
    except Exception as exc:
        raise PrivateStorageError(
            "Não foi possível excluir o arquivo do armazenamento privado."
        ) from exc
