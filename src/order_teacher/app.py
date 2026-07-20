import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypedDict, TypeVar, cast

from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .constants import JOB_STATUSES, ORDER_TYPES
from .daobi_database import DaobiDatabaseService
from .database import DatabaseService
from .models import *  # noqa: F403
from .operation import OperationService

logger = logging.getLogger(__name__)


class AppJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        if self.status_code // 100 in (4, 5):
            msg = str(content)
            data = None
        else:
            msg = "ok"
            if isinstance(content, BaseModel):
                data = content.model_dump()
            elif isinstance(content, list) and all(isinstance(it, BaseModel) for it in cast(list[object], content)):
                data = [it.model_dump() for it in cast(list[BaseModel], content)]
            else:
                data = cast(Any, content)
            data = cast(Any, data)
        wrapped_content = {"code": self.status_code, "msg": msg, "data": data}
        return super().render(wrapped_content)


class AppState(TypedDict):
    daobi_database: DaobiDatabaseService
    database: DatabaseService
    operation: OperationService


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[AppState]:
    settings = AppSettings.model_validate({**os.environ})
    daobi_database = DaobiDatabaseService(settings.daobi_database_url)
    database = DatabaseService(settings.database_url)
    operation = OperationService(
        daobi_database_url=settings.daobi_database_url,
        database_url=settings.database_url,
        moonshot_api_key=settings.moonshot_api_key,
        tmp_dir=settings.tmp_dir,
        volcengine_api_key=settings.volcengine_api_key,
        volcengine_models=settings.volcengine_models,
    )
    state: AppState = {
        "daobi_database": daobi_database,
        "database": database,
        "operation": operation,
    }
    yield state


class InvalidRequest(Exception):
    def __init__(self, msg: str, data: Any | None = None) -> None:
        super().__init__(msg)
        self.msg = msg
        self.data = data

    def __repr__(self) -> str:
        if self.data:
            return f"{self.__class__.__name__}({self.msg}!r, data={self.data!r})"
        return super().__repr__()


def extract_job_id(request: Request[AppState]) -> int:
    job_id = request.path_params["job_id"]
    try:
        return int(job_id)
    except ValueError:
        raise InvalidRequest("job_id must be an integer")


_BaseModelT = TypeVar("_BaseModelT", bound=BaseModel)


async def extract_req_body(request: Request[AppState], model_type: type[_BaseModelT]) -> _BaseModelT:
    body = await request.body()
    try:
        return model_type.model_validate_json(body)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise InvalidRequest(f"invalid request body: {errors}")


async def get_health(request: Request[AppState]) -> AppJSONResponse:
    if all(await asyncio.gather(request.state["daobi_database"].is_healthy(), request.state["database"].is_healthy())):
        status = "UP"
    else:
        status = "DOWN"
    return AppJSONResponse({"status": status})


async def process_job(database: DatabaseService, job_id: int, operation: OperationService, order_id: int) -> None:
    logger.info(f"operation start {job_id=} {order_id=}")
    try:
        matches = await operation.run(order_id)
    except Exception as exc:
        logger.info(f"operation fail {job_id=} {order_id=}: {exc!r}")
        await database.mark_job_fail(job_id, "operation error", repr(exc)[:10000])
    else:
        logger.info(f"operation finish {job_id=} {order_id=}")
        await database.mark_job_succeed(job_id, matches)


async def post_job(request: Request[AppState]) -> AppJSONResponse:
    req_body = await extract_req_body(request, PostJobReqBody)
    order_id = req_body.order_id
    job_id = await request.state["database"].create_job(order_id)
    order_type = await request.state["daobi_database"].select_order_type(order_id)
    err_msg = ""
    if order_type is None:
        err_msg = "order does not exist"
    elif order_type == -1:
        err_msg = "order type is null"
    else:
        if order_type_dict := ORDER_TYPES.get(order_type):
            if not order_type_dict["supported"]:
                err_msg = f"order type {order_type} ({order_type_dict['name']}) is not supported"
        else:
            err_msg = f"order type {order_type} is not supported"
    if err_msg:
        await request.state["database"].mark_job_fail(job_id, err_msg)
        resp_body = PostJobRespBody(job_id=job_id, status="fail", order_id=order_id, err_msg=err_msg)
        return AppJSONResponse(resp_body, status_code=400)
    asyncio.create_task(process_job(request.state["database"], job_id, request.state["operation"], order_id))
    resp_body = PostJobRespBody(job_id=job_id, status="pend", order_id=order_id, err_msg=None)
    return AppJSONResponse(resp_body, status_code=202)


async def get_job_status(request: Request[AppState]) -> AppJSONResponse:
    job_id = extract_job_id(request)
    job = await request.state["database"].get_job(job_id)
    if job is None:
        raise HTTPException(404, "job_id does not exist")
    status = JOB_STATUSES[job.status]
    return AppJSONResponse(GetJobStatusRespBody(job_id=job_id, status=status))


async def get_job_result(request: Request[AppState]) -> AppJSONResponse:
    job_id = extract_job_id(request)
    job = await request.state["database"].get_job(job_id, with_matches=True)
    if job is None:
        raise HTTPException(404, "job_id does not exist")
    status = JOB_STATUSES[job.status]
    matches = [
        TeacherMatch.model_validate(it, from_attributes=True)
        for it in job.matches
        if it.tier < 3 or (it.tier < 10 and it.prof_score >= 0.15)
    ]
    matches = sorted(matches, key=lambda it: it.no)
    for no, it in enumerate(matches, 1):
        it.no = no
    return AppJSONResponse(
        GetJobResultRespBody(
            job_id=job_id,
            status=status,
            order_id=job.order_id,
            err_msg=job.err_msg,
            matches=matches,
        )
    )


async def post_feedback(request: Request[AppState]) -> AppJSONResponse:
    req_body = await extract_req_body(request, PostFeedbackReqBody)
    await request.state["database"].create_feedback(
        order_id=req_body.order_id, teacher_id=req_body.teacher_id, choice=req_body.choice, message=req_body.message
    )
    return AppJSONResponse({}, status_code=202)


async def get_doc(request: Request[AppState]) -> HTMLResponse:
    return HTMLResponse(Path("./static/doc.html").read_bytes())


async def handle_http_exception(request: Request[AppState], exc: HTTPException) -> AppJSONResponse:
    return AppJSONResponse(exc.detail, status_code=exc.status_code)


async def handle_invalid_request(request: Request[AppState], exc: InvalidRequest) -> JSONResponse:
    return AppJSONResponse(exc.msg, status_code=422)


async def handle_exception(request: Request[AppState], exc: Exception) -> JSONResponse:
    return AppJSONResponse("Internal Server Error", status_code=500)


def create_app() -> Starlette:
    return Starlette(
        lifespan=lifespan,
        exception_handlers={
            HTTPException: handle_http_exception,
            InvalidRequest: handle_invalid_request,
            Exception: handle_exception,
        },  # pyright: ignore[reportArgumentType]
        routes=[
            Route("/api/health", get_health, methods=["GET"]),
            Route("/api/job", post_job, methods=["POST"]),
            Route("/api/job/{job_id}/status", get_job_status, methods=["GET"]),
            Route("/api/job/{job_id}/result", get_job_result, methods=["GET"]),
            Route("/api/feedback", post_feedback, methods=["POST"]),
            Route("/doc", get_doc, methods=["GET"]),
        ],
    )
