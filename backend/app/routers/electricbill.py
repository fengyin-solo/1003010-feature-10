"""电费管理接口。

覆盖：账单登记/订正/状态动作、按站点环比异常比对、共站分摊口径维护、
环比阈值维护、异常站点待核实清单。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.electricbill import service

router = APIRouter(prefix="/api/electricbill", tags=["电费管理"])

LIST_FIELDS = ["记录编号", "所属站点", "电表读数", "用电量", "电费金额", "缴费月份", "缴费状态", "票据编号"]
STATUSES = ["待缴费", "已缴费", "电费异常", "已核实"]


class SitePolicyPayload(BaseModel):
    """共站分摊口径：站点 + 各家运营商及合同约定比例。"""

    站点名称: str
    运营商: list[dict[str, Any]] = Field(default_factory=list)
    remark: str | None = None


class ThresholdPayload(BaseModel):
    """环比涨幅阈值：0.5 表示 50%。"""

    环比涨幅阈值: float
    remark: str | None = None


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号或所属站点检索"),
    status: str | None = Query(default=None, description="待缴费、已缴费、电费异常、已核实"),
    site: str | None = Query(default=None, description="按所属站点精确过滤"),
    month: str | None = Query(default=None, description="按缴费月份（YYYY-MM）过滤"),
    abnormal: bool | None = Query(default=None, description="只看异常 / 只看正常"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """账单列表：缴费状态、异常标记都是和明细联动后的实时结果。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, site=site, month=month,
        abnormal=abnormal, page=page, size=size,
    )
    items = [service.decorate(dict(row)) for row in items]
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/abnormal-pending")
def abnormal_pending() -> dict[str, Any]:
    """异常站点待核实清单：按站点聚合当前所有未核实的异常账单。"""
    return {"total": len(service.abnormal_list()), "items": service.abnormal_list()}


@router.get("/sites")
def list_sites() -> dict[str, Any]:
    """共站分摊口径清单（含独站）。"""
    return {"items": service.list_sites()}


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """读取当前环比涨幅阈值等电费比对配置。"""
    return service.get_setting()


@router.get("/export")
def export_entries(
    site: str | None = None,
    month: str | None = None,
) -> dict[str, Any]:
    """导出电费管理清单：可按站点/月份限定范围。"""
    items, total = service.list_entries(site=site, month=month, page=1, size=10000)
    return {
        "module": "electricbill",
        "total": total,
        "items": [service.decorate(dict(row)) for row in items],
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条电费记录明细，含分摊明细与历史账单留档。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"电费记录 {entry_id} 不存在或已归档")
    return service.decorate(entry)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条电费记录，登记后立即按站点口径算分摊、做环比比对。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="电费记录已登记，分摊与环比比对已完成", entry=service.decorate(entry))


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """订正账单明细（金额/读数/月份）：存量记录按新值重算分摊并重做环比，旧结果留档。"""
    entry, errors = service.update_entry(entry_id, payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="账单明细已订正，分摊与环比已重算，历史账单原值已留档", entry=service.decorate(entry))


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """缴纳电费 / 登记异常 / 核实确认；缴费状态与异常标记随动作联动。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=service.decorate(entry))


@router.put("/settings/threshold", response_model=ActionResult)
def update_threshold(payload: ThresholdPayload) -> ActionResult:
    """调整环比涨幅阈值，调整后全部存量账单按新阈值重新比对。"""
    setting, message = service.update_threshold(payload.环比涨幅阈值)
    if setting is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=setting)


@router.put("/sites/policy", response_model=ActionResult)
def upsert_site_policy(payload: SitePolicyPayload) -> ActionResult:
    """登记/调整共站分摊口径（合同比例、运营商名单），存量账单自动按新口径重算。"""
    site, message = service.upsert_site(payload.站点名称, payload.运营商)
    if site is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=site)

