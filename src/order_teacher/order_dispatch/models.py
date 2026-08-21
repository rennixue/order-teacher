import os
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any, Generic, Literal, Self, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# region result
class BaseError(BaseModel):
    msg: str


_T = TypeVar("_T", bound=BaseModel)
_E = TypeVar("_E", bound=BaseError)


class BaseResult(BaseModel, Generic[_T, _E]):
    ok: bool
    data: _T | None = None
    err: _E | None = None

    @model_validator(mode="after")
    def validate_ok(self) -> Self:
        if self.ok:
            if self.data is None:
                raise ValueError()
            if self.err is not None:
                raise ValueError()
        else:
            if self.err is None:
                raise ValueError()
            if self.data is not None:
                raise ValueError()
        return self

    def unwrap(self) -> _T:
        assert self.ok
        assert self.data is not None
        return self.data

    def unwrap_err(self) -> _E:
        assert not self.ok
        assert self.err is not None
        return self.err


# endregion


# region main operation
class OperationError(Exception):
    def __init__(self, msg: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(msg)
        self.msg = msg
        self.data = data or {}


class OrderRawInfo(BaseModel):
    order_type: int | None = None
    order_name: str | None = None
    created_at: datetime | None = None
    univ_id: int | None = None
    course_code: str | None = None
    course_name: str | None = None
    prof_id: int | None = None
    spec_id: int | None = None
    needs: dict[str, str] | None = None

    @classmethod
    def from_course_info(cls, info: "CourseInfo") -> Self:
        return cls(
            order_name=info.order_name,
            order_type=info.order_type,
            created_at=info.created_at,
            univ_id=info.univ_id,
            course_code=info.course_code,
            course_name=info.course_name,
            prof_id=info.prof_id,
            spec_id=info.spec_id,
            needs=info.needs,
        )


class ProcessOrderData(BaseModel):
    order_id: int
    summary: str
    major_ids: list[int]
    processed_paths: list[str]
    raw_info: OrderRawInfo

    @classmethod
    def fallback(cls, order_id: int, info: "CourseInfo") -> Self:
        return cls(
            order_id=order_id,
            summary="",
            major_ids=[],
            processed_paths=[],
            raw_info=OrderRawInfo.from_course_info(info),
        )


class ProcessOrderResult(BaseResult[ProcessOrderData, BaseError]):
    pass


class TeacherProfileTranscriptGrade(BaseModel):
    course_code: str
    course_name: str
    grade: str


class TeacherProfileTranscript(BaseModel):
    univ_id: int | None
    university: str
    grades: list[TeacherProfileTranscriptGrade]


class TeacherProfile(BaseModel):
    subject_areas: list[str]
    skills: list[str]
    transcripts: list[TeacherProfileTranscript]

    @classmethod
    def from_teacher_bio_summary(cls, summary: "TeacherBioSummary") -> Self:
        return cls(
            subject_areas=summary.subject_areas,
            skills=summary.skills,
            transcripts=[
                TeacherProfileTranscript(
                    univ_id=transcript.univ_id,
                    university=transcript.university,
                    grades=[
                        TeacherProfileTranscriptGrade(
                            course_code=it.course_code,
                            course_name=it.course_name,
                            grade=it.grade,
                        )
                        for it in transcript.grades
                    ],
                )
                for transcript in summary.transcripts
            ],
        )


class TeacherEducation(BaseModel):
    edu_type: int | None = None
    univ_id: int | None = None
    prof_ids: list[int] | None = None


class TeacherRawInfo(BaseModel):
    username: str | None = None
    nickname: str | None = None
    wxwork_name: str | None = None
    job_status: int | None = None
    job_type: int | None = None
    intro: str | None = None
    educations: list[TeacherEducation] | None = None

    @classmethod
    def from_teacher_info(cls, info: "TeacherInfo") -> Self:
        return cls(
            username=info.username,
            nickname=info.nickname,
            wxwork_name=info.wxwork_name,
            job_status=info.job_status,
            job_type=info.job_type,
            intro=info.intro,
            educations=[
                TeacherEducation(
                    edu_type=edu.edu_type,
                    univ_id=edu.univ_id,
                    prof_ids=[it.prof_id for it in edu.profs],
                )
                for edu in info.edus
            ],
        )


class ProcessTeacherStableData(BaseModel):
    teacher_id: int
    profile: TeacherProfile
    major_ids: list[int]
    raw_info: TeacherRawInfo

    @classmethod
    def fallback(cls, teacher_id: int, info: "TeacherInfo") -> Self:
        return cls(
            teacher_id=teacher_id,
            profile=TeacherProfile(subject_areas=[], skills=[], transcripts=[]),
            major_ids=[],
            raw_info=TeacherRawInfo.from_teacher_info(info),
        )


class ProcessTeacherStableResult(BaseResult[ProcessTeacherStableData, BaseError]):
    pass


class ProcessTeacherUnstableData(BaseModel):
    teacher_id: int
    summary: str
    major_ids: list[int]
    processed_order_ids: list[int]

    @classmethod
    def fallback(cls, teacher_id: int) -> Self:
        return cls(teacher_id=teacher_id, summary="", major_ids=[], processed_order_ids=[])


class ProcessTeacherUnstableResult(BaseResult[ProcessTeacherUnstableData, BaseError]):
    pass


class MatchOrderData(BaseModel):
    pairs: list[tuple[int, float]]


class MatchOrderResult(BaseResult[MatchOrderData, BaseError]):
    pass


def minmax(v: float, min_: float, max_: float) -> float:
    return min(max(v, min_), max_)


class RunOrderData(BaseModel):
    pairs: list[tuple[int, float]]

    @field_validator("pairs", mode="after")
    @classmethod
    def validate_pairs(cls, pairs: list[tuple[int, float]]) -> list[tuple[int, float]]:
        uniq_pairs: list[tuple[int, float]] = []
        teacher_ids: set[int] = set()
        for teacher_id, score in sorted(pairs, key=lambda it: it[1], reverse=True):
            if teacher_id in teacher_ids:
                continue
            uniq_pairs.append((teacher_id, minmax(round(score, 4), 0.0, 1.0)))
            teacher_ids.add(teacher_id)
        return uniq_pairs


class RunOrderResult(BaseResult[RunOrderData, BaseError]):
    order_ok: bool


# endregion

# region settings
StrPath = str | os.PathLike[str]


class AgentSettings(BaseModel):
    api_key: str
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    concurrency: int = 10
    models: dict[str, str]


class MoonshotSettings(BaseModel):
    api_key: str
    base_url: str = "https://api.moonshot.cn/v1"
    concurrency: int = 10


class OperationSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_nested_delimiter="_", env_nested_max_split=1)

    database_url: str
    daobi_database_url: str
    moonshot: MoonshotSettings
    tmp_dir: Path
    volcengine: AgentSettings


# endregion


# region course operation
class RemoteCourseware(BaseModel):
    course_id: int
    courseware_id: int
    name: str
    url: str


class LocalCourseware(BaseModel):
    course_id: int
    courseware_id: int
    namelike: Path
    path: Path

    @cached_property
    def name(self) -> str:
        return str(self.namelike)

    def with_content(self, content: str) -> "ParsedCourseware":
        return ParsedCourseware(**self.model_dump(), content=content)


class ParsedCourseware(LocalCourseware):
    content: str

    def with_summary(self, summary: str) -> "ProcessedCourseware":
        return ProcessedCourseware(**self.model_dump(), summary=summary)


class ProcessedCourseware(ParsedCourseware):
    summary: str


class CourseInfo(BaseModel):
    course_id: int
    created_at: datetime
    order_type: int
    order_name: str
    univ_id: int
    univ_name: str
    prof_id: int
    prof_name: str
    spec_id: int | None
    spec_name: str | None
    course_code: str
    course_name: str
    needs: dict[str, str] = Field(default_factory=lambda: {})

    @field_validator("order_type", "univ_id", "prof_id", mode="before")
    @classmethod
    def validate_int(cls, v: object) -> object:
        if isinstance(v, int):
            return v
        if v is None:
            return -1
        return v

    @field_validator("univ_name", "prof_name", "course_code", "course_name", mode="before")
    @classmethod
    def validate_str(cls, v: object) -> object:
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        return v


# endregion


# region teacher operation
class RemoteTeacherFile(BaseModel):
    teacher_id: int
    teacher_file_id: int
    file_type: Literal[0, 1]
    name: str
    url: str


class LocalTeacherFile(BaseModel):
    teacher_id: int
    teacher_file_id: int
    file_type: Literal[0, 1]
    namelike: Path
    path: Path

    @cached_property
    def name(self) -> str:
        return str(self.namelike)

    def with_content(self, content: str) -> "ParsedTeacherFile":
        return ParsedTeacherFile(**self.model_dump(), content=content)


class ParsedTeacherFile(LocalTeacherFile):
    content: str

    def with_summary(self, summary: "ResumeOverviewSchema | TranscriptOverviewSchema") -> "ProcessedTeacherFile":
        return ProcessedTeacherFile(**self.model_dump(), **summary.model_dump())


class TeacherFileGrade(BaseModel):
    course_code: str
    course_name: str
    grade: str


class ProcessedTeacherFile(ParsedTeacherFile):
    subject_areas: list[str] = Field(default_factory=lambda: [])
    skills: list[str] = Field(default_factory=lambda: [])
    university: str = ""
    university_id: int = -1
    grades: list[TeacherFileGrade] = Field(default_factory=lambda: [])


class TeacherProf(BaseModel):
    prof_id: int
    prof_name: str

    @field_validator("prof_id", mode="before")
    @classmethod
    def validate_int(cls, v: object) -> object:
        if isinstance(v, int):
            return v
        if v is None:
            return -1
        return v

    @field_validator("prof_name", mode="before")
    @classmethod
    def validate_str(cls, v: object) -> object:
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        return v


class TeacherEdu(BaseModel):
    edu_type: int
    univ_id: int
    univ_name: str
    profs: list[TeacherProf]

    @field_validator("edu_type", "univ_id", mode="before")
    @classmethod
    def validate_int(cls, v: object) -> object:
        if isinstance(v, int):
            return v
        if v is None:
            return -1
        return v

    @field_validator("univ_name", mode="before")
    @classmethod
    def validate_str(cls, v: object) -> object:
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        return v


class TeacherInfo(BaseModel):
    teacher_id: int
    username: str
    nickname: str
    wxwork_name: str
    job_status: int
    job_type: int
    intro: str
    edus: list[TeacherEdu] = Field(default_factory=lambda: [])

    @field_validator("job_status", "job_type", mode="before")
    @classmethod
    def validate_int(cls, v: object) -> object:
        if isinstance(v, int):
            return v
        if v is None:
            return -1
        return v

    @field_validator("nickname", "wxwork_name", "intro", mode="before")
    @classmethod
    def validate_str(cls, v: object) -> object:
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        return v


class TeacherBioSummaryTranscript(BaseModel):
    univ_id: int | None
    university: str
    grades: list[TeacherFileGrade]


class TeacherBioSummary(BaseModel):
    subject_areas: list[str]
    skills: list[str]
    transcripts: list[TeacherBioSummaryTranscript]

    @classmethod
    def fallback(cls) -> Self:
        return cls(subject_areas=[], skills=[], transcripts=[])

    def is_empty(self) -> bool:
        return len(self.subject_areas) == 0 and len(self.skills) == 0 and len(self.transcripts) == 0


class TeacherStatistic(BaseModel):
    data_type: Literal[1, 2]
    course_type: int | None
    num_order: int
    high_rate: float
    fail_rate: float
    complaint_rate: float
    avg_score: float
    experience: float = 0.0

    @field_validator("num_order", mode="before")
    @classmethod
    def validate_int(cls, v: object) -> object:
        if isinstance(v, int):
            return v
        if v is None:
            return 0
        return v

    @field_validator("high_rate", "fail_rate", "complaint_rate", "avg_score", "experience", mode="before")
    @classmethod
    def validate_float(cls, v: object) -> object:
        if isinstance(v, float) or isinstance(v, int):
            return v
        if v is None:
            return 0.0
        return v


class TeacherProduct(BaseModel):
    some_type: int
    status: int


# endregion


# region agent
class IdsSchema(BaseModel):
    ids: list[int]


class ResumeOverviewSchema(BaseModel):
    subject_areas: list[str]
    skills: list[str]

    @classmethod
    def fallback(cls) -> Self:
        return cls(subject_areas=[], skills=[])


class TranscriptOverviewSchema(BaseModel):
    university: str
    university_id: int
    grades: list[TeacherFileGrade]

    @classmethod
    def fallback(cls) -> Self:
        return cls(university="", university_id=-1, grades=[])


# endregion


class CourseProfessionRecord(BaseModel):
    prof_id: int
    prof_en_name: str
    prof_zh_name: str
    prof_name_input: str
    course_id: int
    created_at: datetime
    order_name: str
    course_name: str


class FileTooLarge(RuntimeError):
    pass
