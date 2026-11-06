from datetime import datetime, timedelta, timezone


def live_positions():
    current = datetime.now(timezone.utc)
    now = current.isoformat()
    position_id = int(current.timestamp())
    return [
        {
            "id": 1,
            "rastreavelId": 1,
            "identificador": "ABC1D23",
            "descricao": "Veículo em demonstração",
            "status": "MOVING_ON",
            "offLine": False,
            "timeUltimaComunicacao": now,
            "ultimaPosicao": {
                "id": position_id,
                "time": now,
                "createDate": now,
                "latitude": -27.5954,
                "longitude": -48.5480,
                "speed": 42,
                "course": 126,
                "ignition": True,
                "imei": "860000000000001",
                "power": 13.8,
                "battery": 4.1,
                "satellite": 14,
                "rssi": 83,
                "distance": 50132.4,
                "driveHours": 1480.5,
            },
        },
        {
            "id": 2,
            "rastreavelId": 2,
            "identificador": "DEF4G56",
            "descricao": "Veículo parado",
            "status": "STOPPED_OFF",
            "offLine": False,
            "timeUltimaComunicacao": now,
            "ultimaPosicao": {
                "id": position_id + 1,
                "time": now,
                "createDate": now,
                "latitude": -27.6466,
                "longitude": -48.6704,
                "speed": 0,
                "course": 0,
                "ignition": False,
                "imei": "860000000000002",
                "power": 12.9,
                "battery": 4.0,
                "satellite": 11,
                "rssi": 71,
                "distance": 73420.8,
                "driveHours": 2210.0,
            },
        },
    ]


def alerts():
    now = datetime.now(timezone.utc)
    return [
        {
            "id": 101,
            "tipo": "SPEED_LIMIT",
            "status": "NEW",
            "identificador": "ABC1D23",
            "data": (now - timedelta(hours=2)).isoformat(),
            "descricao": "Velocidade acima do limite configurado.",
            "latitude": -27.6040,
            "longitude": -48.5620,
        },
        {
            "id": 102,
            "tipo": "STOPPED_OVERTIME",
            "status": "OPENED",
            "identificador": "DEF4G56",
            "data": (now - timedelta(hours=5)).isoformat(),
            "descricao": "Veículo parado por tempo excessivo.",
            "latitude": -27.6466,
            "longitude": -48.6704,
        },
        {
            "id": 103,
            "tipo": "DEVICE_BATTERY",
            "status": "CLOSED",
            "identificador": "ABC1D23",
            "data": (now - timedelta(days=1)).isoformat(),
            "descricao": "Alerta demonstrativo de bateria do dispositivo.",
            "latitude": -27.5954,
            "longitude": -48.5480,
        },
    ]
