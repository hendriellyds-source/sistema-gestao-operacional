from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

from app.settings import settings
from app.whatsapp_client import WhatsAppAPIError, WhatsAppConfigurationError, whatsapp_client
from app.whatsapp_store import record_outbound_message, route_conversation


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WhatsAppQueue:
    key: str
    label: str
    menu_number: str
    default_assignee: str
    keywords: tuple[str, ...]


QUEUES = (
    WhatsAppQueue(
        key="finance",
        label="Financeiro",
        menu_number="1",
        default_assignee="Financeiro",
        keywords=("financeiro", "boleto", "pagamento", "nota fiscal", "cobranca"),
    ),
    WhatsAppQueue(
        key="surveying",
        label="Levantamentos",
        menu_number="2",
        default_assignee="Equipe Demo",
        keywords=("levantamento", "levantamentos", "campo", "topografia", "medicao"),
    ),
    WhatsAppQueue(
        key="projects",
        label="Projetos",
        menu_number="3",
        default_assignee="Helena",
        keywords=("projeto", "projetos", "processo", "prefeitura", "regularizacao"),
    ),
    WhatsAppQueue(
        key="commercial",
        label="Comercial",
        menu_number="4",
        default_assignee="Equipe Demo",
        keywords=("comercial", "orcamento", "proposta", "contratar", "servico"),
    ),
    WhatsAppQueue(
        key="other",
        label="Outros assuntos",
        menu_number="5",
        default_assignee="Equipe Demo",
        keywords=("outro", "outros", "atendente", "humano"),
    ),
)

QUEUE_BY_KEY = {queue.key: queue for queue in QUEUES}


def queue_payloads() -> list[dict[str, Any]]:
    return [asdict(queue) for queue in QUEUES]


def _normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text).strip().lower()


def menu_message() -> str:
    return (
        "Olá! Você entrou em contato com a empresa demonstrativa.\n\n"
        "Para encaminharmos seu atendimento, responda com uma opção:\n"
        "1 - Financeiro\n"
        "2 - Levantamentos\n"
        "3 - Projetos\n"
        "4 - Comercial\n"
        "5 - Outros assuntos\n\n"
        "A qualquer momento, envie *menu* para ver estas opções novamente."
    )


def find_queue(value: str | None) -> WhatsAppQueue | None:
    normalized = _normalize(value)
    if not normalized:
        return None

    for queue in QUEUES:
        if normalized == queue.menu_number or normalized == queue.key:
            return queue

    for queue in QUEUES:
        if normalized in queue.keywords:
            return queue
    return None


def extract_message_text(message: dict[str, Any]) -> tuple[str | None, str | None]:
    message_type = str(message.get("type") or "unknown")
    if message_type == "text":
        return str(message.get("text", {}).get("body") or "").strip() or None, None
    if message_type == "button":
        button = message.get("button", {})
        text = str(button.get("text") or "").strip() or None
        payload = str(button.get("payload") or "").strip() or None
        return text, payload
    if message_type == "interactive":
        interactive = message.get("interactive", {})
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text = str(reply.get("title") or reply.get("description") or "").strip() or None
        return text, str(reply.get("id") or "").strip() or None
    return None, None


def route_incoming_message(
    *,
    conversation: dict[str, Any],
    message_text: str | None,
    interactive_id: str | None = None,
    is_first_contact_message: bool,
) -> str | None:
    """Apply the deterministic menu and return the automatic reply, if any."""
    normalized = _normalize(interactive_id or message_text)
    conversation_id = int(conversation["id"])

    if normalized in {"0", "menu", "inicio", "início", "voltar"}:
        route_conversation(
            conversation_id,
            queue_key=None,
            queue_label=None,
            assignee=None,
            status="awaiting_menu",
        )
        return menu_message()

    if conversation.get("queue_key"):
        return None

    queue = find_queue(interactive_id or message_text)
    if queue is None:
        return menu_message() if is_first_contact_message else None

    route_conversation(
        conversation_id,
        queue_key=queue.key,
        queue_label=queue.label,
        assignee=queue.default_assignee,
        status="open",
    )
    return (
        f"Perfeito. Seu atendimento foi encaminhado para *{queue.label}*. "
        f"A equipe responsável continuará por aqui."
    )


def send_and_record(
    *,
    conversation_id: int,
    recipient: str,
    message: str,
) -> None:
    if not settings.whatsapp_enabled:
        logger.info(
            "Resposta automática não enviada porque WHATSAPP_ENABLED está desativado."
        )
        return

    try:
        response = whatsapp_client.send_text(recipient=recipient, message=message)
    except (WhatsAppConfigurationError, WhatsAppAPIError, ValueError):
        logger.exception("Falha ao enviar resposta pelo WhatsApp.")
        return

    messages = response.payload.get("messages") or []
    message_id = str(messages[0].get("id") or "") if messages else None
    record_outbound_message(
        conversation_id=conversation_id,
        message_id=message_id or None,
        body=message,
        status="sent",
        payload=response.payload,
        actor_name="Atendimento automático",
        source="automation",
    )
