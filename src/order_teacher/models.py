from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class OperationError(Exception):
    def __init__(self, msg: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(msg)
        self.msg = msg
        self.data = data or {}


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_nested_delimiter="_", env_nested_max_split=1)

    daobi_database_url: str
    database_url: str
    moonshot_api_key: str
    tmp_dir: Path
    volcengine_api_key: str
    volcengine_models: dict[str, str]


class PostJobReqBody(BaseModel):
    order_id: int


class PostJobRespBody(BaseModel):
    job_id: int
    status: Literal["pend", "fail"]
    order_id: int
    err_msg: str | None


class TeacherMatch(BaseModel):
    no: int
    teacher_id: int
    tier: int
    prof_score: float


class GetJobStatusRespBody(BaseModel):
    job_id: int
    status: Literal["pend", "succeed", "fail"]


class GetJobResultRespBody(BaseModel):
    job_id: int
    status: Literal["pend", "succeed", "fail"]
    order_id: int
    err_msg: str | None
    matches: list[TeacherMatch]


class PostFeedbackReqBody(BaseModel):
    order_id: int
    teacher_id: int
    choice: int | None = None
    message: str


class OrderTagging(BaseModel):
    order_id: int
    order_type: int
    parent_type: Literal["period", "project"]
    student_id: int | None
    t_assign: list[int]
    t_unassign: list[int]
    t_forbid: list[int]
    t_same_student: list[int]
    t_same_code: list[int]


class TeacherTagging(BaseModel):
    teacher_id: int
    has_accident: bool
    client_score: float | None
    fail_rate: float
    complain_rate: float
    prod_score: float | None
    bad_count: int
