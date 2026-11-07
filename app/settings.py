from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator


class Settings(BaseSettings):
    openai_api_key: str = ""
    historical_vision_model: str = "gpt-4.1"
    selsyn_base_url: str = ""
    selsyn_api_key: str = ""
    selsyn_timeout_seconds: int = 30

    poll_interval_seconds: int = 10
    demo_mode: bool = True
    integrations_enabled: bool = False

    # Credenciais locais usadas somente quando DEMO_MODE=true.
    demo_email: str = "demo@portfolio.local"
    demo_password: str = "Demo@12345"
    demo_display_name: str = "Administrador Demo"
    # Supabase
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    supabase_request_timeout_seconds: int = 20
    auth_cookie_secure: bool = True

    employee_certificate_file_max_bytes: int = Field(
        default=10_485_760, gt=0, le=10_485_760
    )
    financial_attachment_file_max_bytes: int = Field(
        default=10_485_760, gt=0, le=10_485_760
    )
    profile_photo_file_max_bytes: int = Field(
        default=5_242_880, gt=0, le=10_485_760
    )
    private_download_url_seconds: int = Field(default=300, ge=30, le=3600)
    admin_token: str = ""
    employee_portal_db_path: str = "data/employee_portal.db"
    employee_session_hours: int = 24
    employee_file_max_bytes: int = 5_242_880
    documents_db_path: str = "data/documents.db"
    documents_root_path: str = "data/documents"
    document_file_max_bytes: int = 20_971_520
    system_state_db_path: str = "data/system_state.db"
    auth_db_path: str = "data/system_auth.db"
    auth_session_hours: int = 12
    tablet_session_days: int = Field(default=30, ge=1, le=90)
    aline_db_path: str = "data/aline_projects.db"
    aline_documents_root_path: str = "data/aline/clients"
    aline_imports_root_path: str = "data/aline/imports"
    aline_master_key_path: str = "data/aline/master.key"
    aline_document_file_max_bytes: int = 26_214_400
    aline_email_root_path: str = "data/aline/email_inbox"
    aline_email_default_interval_seconds: int = Field(default=300, ge=60, le=3600)
    aline_email_max_messages_per_sync: int = Field(default=100, ge=1, le=500)
    aline_email_max_bytes: int = Field(default=35_000_000, ge=1_048_576, le=50_000_000)
    audit_db_path: str = "data/audit.db"
    history_db_path: str = "data/history.db"
    app_timezone: str = "America/Sao_Paulo"

    # Canais institucionais públicos confirmados nos materiais oficiais.
    public_contact_email: str = "contato@example.com"
    public_contact_phone: str = "(00) 00000-0000"
    public_whatsapp_url: str = ""
    public_instagram_handle: str = "@portfolio.demo"
    public_instagram_url: str = ""

    history_auto_sync: bool = True
    history_sync_interval_seconds: int = 300
    history_incremental_batch_size: int = 2000
    history_incremental_max_batches: int = 3
    history_import_page_size: int = 5000
    history_import_max_pages: int = 20
    history_max_range_days: int = 31
    history_max_map_points: int = 5000

    # DiagnÃ³stico de comunicaÃ§Ã£o e GPS.
    tracker_health_db_path: str = "data/tracker_health.db"
    tracker_communication_stale_minutes: int = 15
    tracker_position_stale_minutes: int = 15
    tracker_gps_freeze_minutes: int = 10
    tracker_movement_distance_threshold_m: float = 30.0
    tracker_movement_speed_threshold_kmh: float = 3.0
    tracker_minimum_satellites: int = 4
    tracker_observation_min_interval_seconds: int = 20
    tracker_observation_retention_days: int = 30


    # EndereÃ§os e links de localizaÃ§Ã£o.
    geocoding_db_path: str = "data/geocoding.db"
    reverse_geocoding_enabled: bool = True
    geocoding_timeout_seconds: int = 12
    geocoding_coordinate_precision: int = 5
    nominatim_min_interval_seconds: float = 1.1
    nominatim_user_agent: str = "OperationalManagementDemo/1.0 (portfolio)"

    # Regras operacionais padrÃ£o. Podem ser alteradas na tela de configuraÃ§Ãµes.
    default_work_start: str = "04:00"
    default_work_end: str = "20:00"
    default_allow_weekends: bool = False
    default_speed_limit_kmh: float = 100.0
    default_idle_limit_minutes: int = 20
    default_stop_minimum_minutes: int = 5
    default_trip_end_minutes: int = 10
    
    # Cora — emissão de boletos
    cora_enabled: bool = False
    cora_environment: str = "sandbox"
    cora_client_id: str = ""
    cora_certificate_path: str = ""
    cora_private_key_path: str = ""
    cora_timeout_seconds: int = 30
    cora_webhook_token: str = ""
    cora_webhook_db_path: str = "data/cora_webhooks.db"
    cora_webhook_public_url: str = ""

    # IPM/Atende.Net — emissão de NFS-e
    nfse_ipm_enabled: bool = False
    nfse_ipm_test_mode: bool = True
    nfse_ipm_city: str = ""
    nfse_ipm_username: str = ""
    nfse_ipm_password: str = ""
    nfse_ipm_timeout_seconds: int = 40    

    # Dados fiscais do prestador — Biguaçu
    nfse_provider_cnpj: str = ""
    nfse_provider_municipal_registration: str = ""
    nfse_provider_city_code: str = ""
    nfse_service_item_code: str = "07.20"
    nfse_service_nbs_code: str = "1.1404.21.00"
    nfse_service_tax_rate: str = "4,0246"
    nfse_tax_situation_code: str = ""
    nfse_simples_nacional: bool = True
    nfse_municipal_service_code: str = "72003"

    # WhatsApp Cloud API — Meta
    whatsapp_enabled: bool = False
    whatsapp_auto_menu_enabled: bool = True
    whatsapp_api_version: str = "v26.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_meta_app_id: str = ""
    whatsapp_embedded_signup_config_id: str = ""
    whatsapp_initial_template_name: str = "jaspers_market_order_confirmation_v1"
    whatsapp_initial_template_language: str = "en_US"
    whatsapp_timeout_seconds: int = 30
    whatsapp_db_path: str = "data/whatsapp.db"
    whatsapp_media_root_path: str = ""
    whatsapp_mobile_sender_name: str = "Equipe Demo"

    @field_validator("public_whatsapp_url")
    @classmethod
    def validate_public_whatsapp_url(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized and not normalized.startswith(
            ("https://wa.me/", "https://api.whatsapp.com/")
        ):
            raise ValueError(
                "PUBLIC_WHATSAPP_URL deve usar https://wa.me/ ou "
                "https://api.whatsapp.com/."
            )
        return normalized

    @model_validator(mode="after")
    def isolate_demo(self):
        # A demo cannot use production credentials even if present in the host.
        if self.demo_mode or not self.integrations_enabled:
            for key in ("selsyn_api_key", "supabase_url", "supabase_publishable_key",
                        "supabase_secret_key", "openai_api_key", "cora_client_id",
                        "cora_webhook_token", "whatsapp_access_token", "whatsapp_app_secret",
                        "whatsapp_verify_token", "nfse_ipm_username", "nfse_ipm_password"):
                setattr(self, key, "")
            for key in ("cora_enabled", "nfse_ipm_enabled", "whatsapp_enabled",
                        "history_auto_sync", "reverse_geocoding_enabled"):
                setattr(self, key, False)
        if self.demo_mode:
            # Keep synthetic data apart from an existing operational installation.
            for key in type(self).model_fields:
                if key.endswith(("_db_path", "_root_path", "_key_path")):
                    value = getattr(self, key)
                    if value.startswith("data/") and not value.startswith("data/demo/"):
                        setattr(self, key, "data/demo/" + value[5:])
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
