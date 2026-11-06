from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import tablet_agenda
from app.main import app
from app.settings import settings
from app.system_state import get_system_state, save_system_state


@pytest.fixture()
def agenda_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "system_state_db_path", str(tmp_path / "state.db"))
    save_system_state({
        "workTeams": [{"id": 1, "name": "Neri"}, {"id": 4, "name": "Responsável Demo"}, {"id": 5, "name": "Apoio"}],
        "companies": [{"id": 2, "name": "MDM"}],
        "works": [{"id": 3, "companyId": 2, "name": "Talantaia", "dailyRate": 700, "halfDailyRate": 350}],
        "workScheduleEntries": [],
    }, source="test", updated_by="test")
    return SimpleNamespace(state=SimpleNamespace(system_user={"role_code": "admin", "profile_id": "test"}))


def test_ink_is_idempotent_and_review_publishes_once(agenda_db):
    day = "2026-09-25"
    stroke_id, review_id = str(uuid4()), str(uuid4())
    stroke = tablet_agenda.Stroke(id=stroke_id, points=[(100, 160), (120, 164)])
    tablet_agenda.add_stroke(day, stroke, agenda_db)
    tablet_agenda.add_stroke(day, stroke, agenda_db)
    page = tablet_agenda.read_page(day, agenda_db)
    assert len(page["strokes"]) == 1
    assert page["strokes"][0]["points"] == [[100.0, 160.0], [120.0, 164.0]]

    review = tablet_agenda.Review(
        id=review_id, line_number=0, text_original="MDM Neri Talantaia 7h30",
        team_id=1, company_id=2, work_id=3, scheduled_time="07:30",
    )
    tablet_agenda.save_review(day, review, agenda_db)
    first = tablet_agenda.confirm_review(day, review_id, agenda_db)
    second = tablet_agenda.confirm_review(day, review_id, agenda_db)
    assert first["schedule_id"] == second["schedule_id"]
    entries = get_system_state()["data"]["workScheduleEntries"]
    assert len(entries) == 1
    assert entries[0]["tabletAgendaId"] == review_id
    assert entries[0]["status"] == "Planejado"
    assert entries[0]["value"] == 700

    # A workspace tab saved with stale schedule data cannot erase a tablet entry.
    save_system_state({
        "workTeams": [{"id": 1, "name": "Neri"}],
        "companies": [{"id": 2, "name": "MDM"}],
        "works": [{"id": 3, "companyId": 2, "name": "Talantaia"}],
        "workScheduleEntries": [],
    }, source="stale-tab", updated_by="test")
    assert len(get_system_state()["data"]["workScheduleEntries"]) == 1


def test_search_company_across_days_and_change_linked_work(agenda_db):
    state = get_system_state()
    data = state["data"]
    data["companies"].append({"id": 9, "name": "Empresa Nova"})
    data["works"].append({"id": 8, "companyId": 9, "name": "Nova obra", "dailyRate": 900})
    save_system_state(data, source="test", updated_by="test")
    for day in ("2026-09-25", "2026-09-26"):
        review_id = str(uuid4())
        tablet_agenda.save_review(day, tablet_agenda.Review(
            id=review_id, line_number=0, text_original="MDM Neri Talantaia",
            team_id=1, company_id=2, work_id=3,
        ), agenda_db)
    result = tablet_agenda.search_schedule(agenda_db, q="MDM")
    assert result["days"] == 2
    assert len(result["items"]) == 2
    target = result["items"][0]
    tablet_agenda.edit_schedule_entry(target["id"], tablet_agenda.ScheduleEdit(
        company_id=9, work_id=8, value=900, status="Confirmado",
        include_financial=True, observation="Obra corrigida",
    ), agenda_db)
    matches = tablet_agenda.search_schedule(agenda_db, q="Nova obra")["items"]
    assert len(matches) == 1
    assert matches[0]["id"] == target["id"]
    assert matches[0]["company"] == "Empresa Nova"
    assert matches[0]["value"] == 900
    assert matches[0]["status"] == "Confirmado"
    assert len(tablet_agenda.search_schedule(agenda_db, q="Talantaia")["items"]) == 1
    assert len(tablet_agenda.search_schedule(agenda_db, q="MDM")["items"]) == 1
    review = tablet_agenda.read_page(target["date"], agenda_db)["reviews"][0]
    assert review["company_id"] == 9
    assert review["work_id"] == 8
    assert review["price_override"] == 900


def test_alias_and_ambiguous_interpretation(agenda_db):
    tablet_agenda.add_alias(tablet_agenda.Alias(alias="N", kind="team", target_id=1), agenda_db)
    result = tablet_agenda.interpret("N MD 7h30", agenda_db)
    assert result["matches"]["team"][0]["id"] == 1
    assert result["scheduled_time"] == "07:30"


def test_work_name_implies_company_and_alias_can_be_managed(agenda_db):
    tablet_agenda.add_alias(tablet_agenda.Alias(alias="BETT", kind="work", target_id=3), agenda_db)
    result = tablet_agenda.interpret("N/BETT/7:30", agenda_db)
    assert result["matches"]["work"] == [{"id": 3, "name": "Talantaia"}]
    assert result["matches"]["company"] == [{"id": 2, "name": "MDM"}]
    assert result["scheduled_time"] == "07:30"
    tablet_agenda.delete_alias(tablet_agenda.Alias(alias="BETT", kind="work", target_id=3), agenda_db)
    assert not tablet_agenda.interpret("BETT", agenda_db)["matches"]["work"]


def test_half_day_words_and_fraction_default_to_full_day(agenda_db):
    assert tablet_agenda.interpret("Neri/Talantaia/7:30", agenda_db)["daily_type"] == "Inteira"
    assert tablet_agenda.interpret("Neri/Talantaia/7:30/MEIA", agenda_db)["daily_type"] == "Meia"
    assert tablet_agenda.interpret("Neri/Talantaia/7:30/1/2", agenda_db)["daily_type"] == "Meia"
    review_id = str(uuid4())
    tablet_agenda.save_review("2026-09-25", tablet_agenda.Review(
        id=review_id, line_number=0, text_original="Neri/Talantaia/7:30/1/2",
        team_id=1, company_id=2, work_id=3, scheduled_time="07:30", daily_type="Meia",
    ), agenda_db)
    tablet_agenda.confirm_review("2026-09-25", review_id, agenda_db)
    entry = get_system_state()["data"]["workScheduleEntries"][0]
    assert entry["dailyType"] == "Meia"
    assert entry["value"] == 350


def test_quarter_day_and_extra_team_surcharge(agenda_db):
    assert tablet_agenda.interpret("Neri/Talantaia/1/4", agenda_db)["daily_type"] == "Quarto"
    day = "2026-09-28"
    quarter_review = str(uuid4())
    tablet_agenda.save_review(day, tablet_agenda.Review(
        id=quarter_review, line_number=0, text_original="Neri/Talantaia/1/4",
        team_id=1, company_id=2, work_id=3, daily_type="Quarto",
    ), agenda_db)
    entries = get_system_state()["data"]["workScheduleEntries"]
    assert entries[0]["dailyType"] == "Quarto"
    assert entries[0]["value"] == 175

    support_review = str(uuid4())
    support_day = "2026-09-29"
    tablet_agenda.save_review(support_day, tablet_agenda.Review(
        id=support_review, line_number=1, text_original="Neri/Responsável Demo/Talantaia",
        team_id=1, company_id=2, work_id=3,
        extra_teams=[{"team_id": 4, "amount": "1/4"}, {"team_id": 5, "amount": "125,50"}],
    ), agenda_db)
    entries = get_system_state()["data"]["workScheduleEntries"]
    support = next(item for item in entries if item.get("appointmentExtraTeam"))
    support_primary = next(item for item in entries if item.get("tabletAgendaId") == support_review)
    assert support["teamId"] == 4
    assert support["value"] == 175
    assert support["appointmentGroupId"] == support_primary["id"]
    support_fixed = next(item for item in entries if item.get("appointmentExtraTeam") and item["teamId"] == 5)
    assert support_fixed["value"] == 125.5
    assert support_fixed["appointmentGroupId"] == support_primary["id"]
    page = tablet_agenda.read_page(support_day, agenda_db)
    review = next(item for item in page["reviews"] if item["id"] == support_review)
    assert review["extra_teams"] == [{"team_id": 4, "amount": "1/4"}, {"team_id": 5, "amount": "125,50"}]
    tablet_agenda.save_review(support_day, tablet_agenda.Review(id=support_review, line_number=1), agenda_db)
    remaining = get_system_state()["data"]["workScheduleEntries"]
    assert len(remaining) == 1
    assert remaining[0]["tabletAgendaId"] == quarter_review


def test_new_work_autosaves_without_price_and_blank_line_deletes_both_views(agenda_db):
    day, review_id = "2026-09-26", str(uuid4())
    draft = tablet_agenda.Review(
        id=review_id, line_number=0, text_original="Romulo garopaba/gu",
        team_id=1, unregistered_work_text="Romulo garopaba",
    )
    first = tablet_agenda.save_review(day, draft, agenda_db)
    entries = get_system_state()["data"]["workScheduleEntries"]
    assert len(entries) == 1
    assert entries[0]["id"] == first["schedule_id"]
    assert entries[0]["tabletWorkText"] == "Romulo garopaba"
    assert entries[0]["registrationPending"] is True
    assert entries[0]["financialPending"] is True
    assert entries[0]["value"] == 0
    assert tablet_agenda.read_page(day, agenda_db)["schedule_entries"][0]["id"] == first["schedule_id"]

    draft.text_original = "Romulo garopaba/gu/7:30"
    draft.scheduled_time = "07:30"
    second = tablet_agenda.save_review(day, draft, agenda_db)
    assert second["schedule_id"] == first["schedule_id"]
    assert get_system_state()["data"]["workScheduleEntries"][0]["scheduledTime"] == "07:30"

    stale = get_system_state()["data"]
    deleted = tablet_agenda.save_review(day, tablet_agenda.Review(id=review_id, line_number=0), agenda_db)
    assert deleted["schedule_id"] is None
    assert tablet_agenda.read_page(day, agenda_db)["schedule_entries"] == []
    save_system_state(stale, source="stale-tab", updated_by="test")
    assert get_system_state()["data"]["workScheduleEntries"] == []


def test_tablet_price_and_billing_choice_update_existing_schedule(agenda_db):
    day, review_id = "2026-09-27", str(uuid4())
    review = tablet_agenda.Review(
        id=review_id, line_number=0, text_original="Neri/Obra Nova",
        team_id=1, company_id=2, unregistered_work_text="Obra Nova",
        price_override=1250, include_financial=False,
    )
    created = tablet_agenda.save_review(day, review, agenda_db)
    entry = get_system_state()["data"]["workScheduleEntries"][0]
    assert entry["id"] == created["schedule_id"]
    assert entry["value"] == 1250
    assert entry["financialPending"] is False
    assert entry["generateFinancial"] is False
    saved = tablet_agenda.read_page(day, agenda_db)["reviews"][0]
    assert saved["price_override"] == 1250
    assert saved["include_financial"] == 0

    review.price_override = 1400
    review.include_financial = True
    tablet_agenda.save_review(day, review, agenda_db)
    entries = get_system_state()["data"]["workScheduleEntries"]
    assert len(entries) == 1
    assert entries[0]["id"] == created["schedule_id"]
    assert entries[0]["value"] == 1400
    assert entries[0]["generateFinancial"] is True

    state = get_system_state()["data"]
    state["workScheduleEntries"][0]["value"] = 1550
    state["workScheduleEntries"][0]["generateFinancial"] = False
    save_system_state(state, source="workspace", updated_by="test",
                      client_revision=get_system_state()["revision"])
    assert get_system_state()["data"]["workScheduleEntries"][0]["value"] == 1550
    tablet_agenda.save_review(day, review, agenda_db)
    preserved = get_system_state()["data"]["workScheduleEntries"][0]
    assert preserved["value"] == 1550
    assert preserved["generateFinancial"] is False


def test_line_price_sets_appointment_value_without_changing_work_catalog(agenda_db):
    day = "2026-09-26"
    total_id, surcharge_id, new_id = str(uuid4()), str(uuid4()), str(uuid4())
    tablet_agenda.save_review(day, tablet_agenda.Review(
        id=total_id, line_number=0, text_original="Neri/Talantaia/3000",
        team_id=1, company_id=2, work_id=3,
    ), agenda_db)
    tablet_agenda.save_review(day, tablet_agenda.Review(
        id=surcharge_id, line_number=1, text_original="Neri/Talantaia/+400",
        team_id=1, company_id=2, work_id=3,
    ), agenda_db)
    tablet_agenda.save_review(day, tablet_agenda.Review(
        id=new_id, line_number=2, text_original="Neri/Rua Nova/2000",
        team_id=1, unregistered_work_text="Rua Nova",
    ), agenda_db)
    entries = get_system_state()["data"]["workScheduleEntries"]
    by_review = {entry.get("tabletAgendaId"): entry for entry in entries}
    assert by_review[total_id]["value"] == 3000
    assert by_review[total_id]["tabletPriceMode"] == "total"
    assert by_review[surcharge_id]["value"] == 1100
    assert by_review[surcharge_id]["tabletPriceMode"] == "surcharge"
    assert by_review[new_id]["value"] == 2000
    assert by_review[new_id]["registrationPending"] is True
    assert by_review[new_id]["financialPending"] is False
    assert by_review[new_id]["tabletWorkText"] == "Rua Nova"
    assert get_system_state()["data"]["works"][0]["dailyRate"] == 700


def test_spaced_price_and_half_day_surcharge_reach_schedule(agenda_db):
    day = "2026-09-27"
    new_id, half_id = str(uuid4()), str(uuid4())
    tablet_agenda.save_review(day, tablet_agenda.Review(
        id=new_id, line_number=0, text_original="Neri/MDM/Rua Nova rtk 2600",
        team_id=1, company_id=2, unregistered_work_text="Rua Nova rtk",
    ), agenda_db)
    tablet_agenda.save_review(day, tablet_agenda.Review(
        id=half_id, line_number=1, text_original="Neri/Talantaia/meia +400",
        team_id=1, company_id=2, work_id=3, daily_type="Meia",
    ), agenda_db)
    entries = {item["tabletAgendaId"]: item for item in
               get_system_state()["data"]["workScheduleEntries"]}
    assert entries[new_id]["value"] == 2600
    assert entries[new_id]["financialPending"] is False
    assert entries[new_id]["generateFinancial"] is True
    assert entries[half_id]["value"] == 750
    assert entries[half_id]["generateFinancial"] is True


def test_repairs_only_old_zero_price_without_deleting_service(agenda_db):
    day, review_id = "2026-09-28", str(uuid4())
    tablet_agenda.save_review(day, tablet_agenda.Review(
        id=review_id, line_number=0, text_original="Neri/MDM/Rua Nova rtk",
        team_id=1, company_id=2, unregistered_work_text="Rua Nova rtk",
    ), agenda_db)
    before = get_system_state()["data"]["workScheduleEntries"][0]
    assert before["value"] == 0
    with tablet_agenda._connect() as db:
        db.execute("UPDATE tablet_review SET text_original=?,unregistered_work_text=? WHERE id=?",
                   ("Neri/MDM/Rua Nova rtk 2600", "Rua Nova rtk 2600", review_id))
        db.execute("DELETE FROM tablet_migration WHERE key='spaced-line-prices-v1'")
    tablet_agenda._init()
    after = get_system_state()["data"]["workScheduleEntries"][0]
    assert after["id"] == before["id"]
    assert after["value"] == 2600
    assert after["tabletWorkText"] == "Rua Nova rtk"
    assert after["financialPending"] is False
    assert tablet_agenda.read_page(day, agenda_db)["reviews"][0]["unregistered_work_text"] == "Rua Nova rtk"


def test_old_saved_review_is_published_without_duplicate(agenda_db):
    day, review_id, new_work_id = "2026-09-25", str(uuid4()), str(uuid4())
    tablet_agenda._init()
    with tablet_agenda._connect() as db:
        db.execute("""INSERT INTO tablet_review
            (id,day,line_number,text_original,team_id,company_id,work_id,scheduled_time,
             daily_type,observation,unregistered_work_text,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (review_id, day, 0, "Neri/Talantaia/7:30", 1, 2, 3, "07:30",
             "Inteira", "", "", tablet_agenda._now()))
        db.execute("""INSERT INTO tablet_review
            (id,day,line_number,text_original,team_id,company_id,work_id,scheduled_time,
             daily_type,observation,unregistered_work_text,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_work_id, day, 1, "N/RD 2002/", 1, None, None, None,
             "Inteira", "", "", tablet_agenda._now()))
    assert tablet_agenda.read_page(day, agenda_db)["schedule_entries"] == []
    assert tablet_agenda.publish_pending_reviews(day, agenda_db)["published"] == 2
    assert tablet_agenda.publish_pending_reviews(day, agenda_db)["published"] == 0
    page = tablet_agenda.read_page(day, agenda_db)
    assert len(page["schedule_entries"]) == 2
    by_review = {item["tabletAgendaId"]: item for item in page["schedule_entries"]}
    assert page["reviews"][0]["schedule_id"] == by_review[review_id]["id"]
    assert by_review[new_work_id]["tabletWorkText"] == "RD 2002"
    assert by_review[new_work_id]["financialPending"] is True


def test_existing_schedule_and_projectist_request_are_visible(agenda_db):
    state = get_system_state()["data"]
    state["workScheduleEntries"] = [{"id": 90, "date": "2026-09-26", "type": "Obra",
                                      "teamId": 1, "companyId": 2, "workId": 3, "status": "Planejado",
                                      "value": 875, "billingEntityId": 6}]
    state["projectClients"] = [{"id": 8, "name": "Cliente Projetista", "serviceType": "Topografia",
                                "landLocation": "Garopaba"}]
    state["projectScheduleRequests"] = [{"id": 7, "clientId": 8, "status": "Pendente"}]
    save_system_state(state, source="test", updated_by="test")
    page = tablet_agenda.read_page("2026-09-26", agenda_db)
    assert page["schedule_entries"][0]["id"] == 90
    assert page["pending_requests"][0]["client_name"] == "Cliente Projetista"
    review_id = str(uuid4())
    linked = tablet_agenda.save_review("2026-09-26", tablet_agenda.Review(
        id=review_id, line_number=0, text_original="Neri/Talantaia",
        team_id=1, company_id=2, work_id=3,
    ), agenda_db)
    assert linked["schedule_id"] == 90
    assert len(get_system_state()["data"]["workScheduleEntries"]) == 1
    tablet_agenda.save_review("2026-09-26", tablet_agenda.Review(
        id=review_id, line_number=0, text_original="Neri/Talantaia/7:30",
        team_id=1, company_id=2, work_id=3, scheduled_time="07:30",
    ), agenda_db)
    updated = get_system_state()["data"]["workScheduleEntries"][0]
    assert updated["value"] == 875
    assert updated["billingEntityId"] == 6
    tablet_agenda.save_review("2026-09-26", tablet_agenda.Review(id=review_id, line_number=0), agenda_db)
    assert get_system_state()["data"]["workScheduleEntries"] == []


def test_fresh_system_deletion_clears_linked_tablet_line(agenda_db):
    day, review_id = "2026-09-27", str(uuid4())
    tablet_agenda.save_review(day, tablet_agenda.Review(
        id=review_id, line_number=0, text_original="Neri/Talantaia",
        team_id=1, company_id=2, work_id=3,
    ), agenda_db)
    current = get_system_state()
    current["data"]["workScheduleEntries"] = []
    save_system_state(current["data"], source="sistema-web", updated_by="test",
                      client_revision=current["revision"])
    assert tablet_agenda.read_page(day, agenda_db)["schedule_entries"] == []
    review = tablet_agenda.read_page(day, agenda_db)["reviews"][0]
    assert review["schedule_id"] is None
    assert review["text_original"] == ""


def test_installed_app_page_and_worker_require_existing_access():
    user = {"role_code": "admin", "profile_id": "test"}
    with patch("app.main.user_from_session", return_value=user):
        client = TestClient(app)
        page = client.get("/agenda-tablet")
        worker = client.get("/agenda-tablet/sw.js")
        client.close()
    assert page.status_code == 200
    assert "Agenda Gestão Operacional" in page.text
    assert worker.status_code == 200
    assert worker.headers["service-worker-allowed"] == "/"

    with patch("app.main.user_from_session", return_value=None):
        client = TestClient(app)
        response = client.get("/agenda-tablet", follow_redirects=False)
        client.close()
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/agenda-tablet"
