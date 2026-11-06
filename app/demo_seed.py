"""Small, relationally consistent synthetic scenario, seeded only once."""
from datetime import date, timedelta

from .settings import settings
from .system_state import get_system_state, save_system_state


def build_demo_state():
    def day(offset):
        return (date.today() + timedelta(days=offset)).isoformat()

    data = {key: [] for key in (
        "employees clinics exams vacations leaves vehicles maintenances maintenanceHistory "
        "maintenanceDrafts fines companies works workTeams workScheduleEntries billingEntities "
        "invoices projectClients projectScheduleRequests projectsWorks projectsProcesses "
        "employeeAppFiles events fleetVehicles fleetGeofences fleetTrips fleetStops "
        "fleetDrivingEvents fleetFuel fleetExpenses fleetDocuments fleetDebts fleetHiddenVehicles"
    ).split()}
    data["employeeImportVersion"] = "portfolio-v1"
    data["settings"] = {"title": "Dashboard", "subtitle": "Demonstração • todos os dados são fictícios", "primary": "#0b5e8e", "primaryLight": "#3a9ed0", "primaryDark": "#244f7d", "background": "#f5f8fb"}
    for i, name in enumerate(["Pessoa Demo Alfa", "Pessoa Demo Beta", "Pessoa Demo Gama"], 1):
        data["employees"].append({"id": i, "name": name, "registration": f"DEMO-{i:03}", "code": f"DEMO-{i:03}", "cpf": "", "email": f"pessoa{i}@example.invalid", "phone": "", "role": "Topógrafo" if i == 1 else "Auxiliar de campo", "sector": "Operações", "status": "Ativo", "admission": day(-180), "currentSalary": 3000 + 200*i, "birth": "1995-01-01", "appLogin": f"pessoa.demo{i}", "appPassword": "DemoPortal123", "notes": "Registro inteiramente fictício"})
    data["companies"] = [{"id": 1, "name": "Construtora Horizonte Demo", "legalName": "Construtora Horizonte Demo", "tradeName": "Horizonte Demo", "notes": "Empresa fictícia"}]
    data["works"] = [{"id": 1, "companyId": 1, "name": "Parque Horizonte — Obra Demo", "location": "Local demonstrativo", "dailyRate": 1200, "halfDailyRate": 650, "notes": "Obra fictícia"}]
    data["workTeams"] = [{"id": 1, "name": "Equipe Alfa", "employeeIds": [1, 2], "active": True}, {"id": 2, "name": "Equipe Beta", "employeeIds": [3], "active": True}]
    data["workScheduleEntries"] = [{"id": i+10, "date": day(offset), "teamId": 1 if i%2 == 0 else 2, "type": "Obra", "companyId": 1, "workId": 1, "dailyType": "Inteira", "value": 1200, "status": "Confirmado" if offset <= 0 else "Planejado", "observation": "Agendamento fictício", "billingEntityId": "", "projectClientId": ""} for i, offset in enumerate([-2, -1, 0, 1, 3])]
    data["vehicles"] = [{"id": 1, "plate": "ABC1D23", "brand": "Fiat", "model": "Strada", "year": 2024, "odometer": 50120, "status": "Em uso", "holderEmployeeId": 1, "nextMaintenanceDate": day(20), "notes": "Veículo fictício"}, {"id": 2, "plate": "DEF4G56", "brand": "Volkswagen", "model": "Saveiro", "year": 2024, "odometer": 73420, "status": "Disponível", "holderEmployeeId": "", "nextMaintenanceDate": day(40), "notes": "Veículo fictício"}]
    data["clinics"] = [{"id": 1, "name": "Clínica Demo", "email": "clinica@example.invalid", "city": "Cidade Demo", "phone": ""}]
    data["exams"] = [{"id": 1, "employeeId": 1, "clinicId": 1, "type": "Periódico", "date": day(7), "due": day(20), "scheduled": day(7), "aso": "Pendente", "status": "Agendado", "notes": "Exame fictício"}]
    data["vacations"] = [{"id": 1, "employeeId": 3, "start": day(20), "end": day(34), "days": 15, "status": "Programada"}]
    data["invoices"] = [{"id": 1, "companyId": 1, "workId": 1, "number": "DEMO-001", "invoiceNumber": "DEMO-001", "scheduleEntryIds": [10, 11, 12], "total": 3600, "month": day(0)[:7], "description": "Serviços demonstrativos", "amount": 3600, "value": 3600, "dueDate": day(10), "issueDate": day(-2), "status": "Pendente", "notes": "Sem cobrança real"}]
    data["events"] = [{"id": 1, "title": "Reunião de planejamento Demo", "date": day(3), "category": "Reunião"}]
    return data


def seed_demo_state():
    if settings.demo_mode and not get_system_state()["has_state"]:
        save_system_state(build_demo_state(), source="demo-seed-v1", updated_by="demo")
