from __future__ import annotations

import gc
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from app import aline_email, aline_projects
from app.settings import settings


class FakeImap:
    raw = b""

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port

    def login(self, username: str, password: str):
        return "OK", []

    def select(self, mailbox: str, readonly: bool = True):
        if readonly is not True:
            raise AssertionError("A caixa Terra deve ser aberta somente para leitura.")
        return "OK", [b"1"]

    def uid(self, command: str, *args):
        if command == "search":
            return "OK", [b"7"]
        if command == "fetch":
            return "OK", [(b"7 (RFC822)", self.raw)]
        if command == "store":
            raise AssertionError("A automação não pode alterar mensagens no Terra.")
        raise AssertionError(command)

    def logout(self):
        return "BYE", []


class AlineEmailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.previous = {
            "db": settings.aline_db_path,
            "documents": settings.aline_documents_root_path,
            "imports": settings.aline_imports_root_path,
            "email": settings.aline_email_root_path,
            "key": settings.aline_master_key_path,
        }
        root = Path(self.directory.name)
        settings.aline_db_path = str(root / "aline.db")
        settings.aline_documents_root_path = str(root / "documents")
        settings.aline_imports_root_path = str(root / "imports")
        settings.aline_email_root_path = str(root / "email")
        settings.aline_master_key_path = str(root / "master.key")
        aline_email.init_aline_email()
        client = aline_projects.create_client(aline_projects.DataPayload(data={
            "person_type": "PF", "name": "Cliente Terra", "city": "Joinville",
        }), x_portfolio_profile="Aline")
        self.process = aline_projects.create_process(aline_projects.DataPayload(data={
            "client_id": client["id"], "title": "Processo pelo e-mail",
            "process_type": "Prefeitura", "service_type": "Retificação de área",
            "city": "Joinville", "third_code": "TER-2026-00991",
            "status_manual": "OK",
        }), x_portfolio_profile="Aline")
        with aline_projects._connect() as connection:
            connection.execute(
                """
                UPDATE aline_email_settings SET enabled=1, username_enc=?, password_enc=?
                WHERE id=1
                """,
                (aline_projects._encrypt("projetos@demo.local"), aline_projects._encrypt("segredo")),
            )
            connection.commit()

    def tearDown(self) -> None:
        settings.aline_db_path = self.previous["db"]
        settings.aline_documents_root_path = self.previous["documents"]
        settings.aline_imports_root_path = self.previous["imports"]
        settings.aline_email_root_path = self.previous["email"]
        settings.aline_master_key_path = self.previous["key"]
        gc.collect()
        self.directory.cleanup()

    def _message(self, code: str = "TER-2026-00991") -> bytes:
        message = EmailMessage()
        message["From"] = "notificacoes@demo.local"
        message["To"] = "projetos@demo.local"
        message["Subject"] = f"Movimentação do processo {code}"
        message["Message-ID"] = "<mensagem@demo.local>"
        message.set_content("Houve uma nova notificação e o prazo precisa ser conferido.")
        message.add_attachment(b"documento de teste", maintype="application", subtype="pdf", filename="notificacao.pdf")
        return message.as_bytes()

    def test_sync_matches_third_code_imports_attachment_and_is_idempotent(self) -> None:
        FakeImap.raw = self._message()
        first = aline_email.sync_aline_email_once(FakeImap)
        second = aline_email.sync_aline_email_once(FakeImap)
        self.assertTrue(first["ok"])
        self.assertEqual(first["matched"], 1)
        self.assertTrue(second["ok"])
        with aline_projects._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM aline_email_messages").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM aline_documents").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM aline_events WHERE event_type='E-mail automático'").fetchone()[0], 1)
        notifications = aline_projects.list_notifications(unread_only=False)
        self.assertTrue(any(item["key"].startswith("email:") for item in notifications))

    def test_unknown_code_is_not_copied_from_terra(self) -> None:
        FakeImap.raw = self._message("CODIGO-SEM-CADASTRO")
        result = aline_email.sync_aline_email_once(FakeImap)
        self.assertEqual(result["matched"], 0)
        self.assertEqual(aline_email.list_email_messages(status="", limit=100), [])
        self.assertEqual(list(Path(settings.aline_email_root_path).rglob("*.eml")), [])

    def test_third_code_cannot_be_reused_by_another_process(self) -> None:
        with self.assertRaises(Exception) as context:
            aline_projects.create_process(aline_projects.DataPayload(data={
                "client_id": self.process["client_id"], "title": "Duplicado",
                "process_type": "Prefeitura", "service_type": "Retificação de área",
                "city": "Joinville", "third_code": "ter-2026-00991",
            }), x_portfolio_profile="Aline")
        self.assertEqual(getattr(context.exception, "status_code", None), 409)


if __name__ == "__main__":
    unittest.main()
