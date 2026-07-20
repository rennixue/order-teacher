from collections.abc import Mapping
from typing import Literal, TypedDict


class OrderTypeDict(TypedDict):
    id: int
    name: str
    supported: bool
    parent_type: Literal["other", "period", "project"]
    prod_id: int


ORDER_TYPES: Mapping[int, OrderTypeDict] = {
    0: {"id": 0, "name": "定制辅导", "supported": True, "parent_type": "period", "prod_id": 2},
    1: {"id": 1, "name": "考前突击", "supported": True, "parent_type": "period", "prod_id": 1},
    26: {"id": 26, "name": "包课辅导", "supported": True, "parent_type": "period", "prod_id": 8},
    27: {"id": 27, "name": "论文润色", "supported": False, "parent_type": "project", "prod_id": -1},
    64: {"id": 64, "name": "班课辅导", "supported": False, "parent_type": "other", "prod_id": 9},
    65: {"id": 65, "name": "论文大礼包", "supported": True, "parent_type": "project", "prod_id": 7},
    66: {"id": 66, "name": "特殊订单", "supported": False, "parent_type": "other", "prod_id": -1},
    67: {"id": 67, "name": "毕业大论文", "supported": True, "parent_type": "project", "prod_id": 3},
    69: {"id": 69, "name": "文案类", "supported": False, "parent_type": "other", "prod_id": 6},
    70: {"id": 70, "name": "实习类", "supported": False, "parent_type": "other", "prod_id": -1},
    71: {"id": 71, "name": "作业辅导", "supported": True, "parent_type": "project", "prod_id": 4},
    72: {"id": 72, "name": "Course Package", "supported": False, "parent_type": "other", "prod_id": -1},
}

JOB_STATUSES: Mapping[int, Literal["pend", "succeed", "fail"]] = {0: "pend", 1: "succeed", 2: "fail"}
