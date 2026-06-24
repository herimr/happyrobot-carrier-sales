from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_auth_token: str = "change-me"  # Bearer token HappyRobot's Webhook nodes must send
    tms_host: str = "localhost"
    tms_port: int = 9999
    tms_auth_token: str = "change-me-tms-token"
    fmcsa_mock_mode: bool = True
    fmcsa_web_key: str = ""
    otp_test_code: str = ""  # Demo/test bypass code for OTP validation. Leave empty in production.

    class Config:
        env_file = ".env"


settings = Settings()
