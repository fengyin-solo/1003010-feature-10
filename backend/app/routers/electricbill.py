"""电费管理接口。

- 站点账单：登记 / 查询 / 缴费 / 核实，明细自带环比与各家分摊；
- 分表读数：登记或补录后相关账单自动按用电量重算分摊；
- 分摊口径：切换口径或改合同比例，存量账单重算、原值留档；
- 异常待核实清单：按站点汇总环比越线账单。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.electricbill import (
    DEFAULT_THRESHOLD,
    SHARING_MODES,
    service,
)

router = APIRouter(prefix="/api/electricbill", tags=["电费管理"])

LIST_FIELDS = ["记录编号", "所属站点", "用电量", "电费金额", "缴费月份", "票据编号"]
STATUSES = ["待缴费", "已缴费", "电费异常", "已核实"]


@router.get("/config")
def get_config() -> dict[str, Any]:
    """读取环比异常阈值等电费比对配置。"""
    return service.get_config()


@router.put("/config", response_model=ActionResult)
def update_config(payload: EntryPayload) -> ActionResult:
    """调整环比异常阈值（0.5 表示 50%），全部账单立即重新比对。"""
    config, message = service.update_config(payload.values)
    if config is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=config)


@router.get("/abnormal-sites")
def abnormal_sites(
    include_verified: bool = Query(default=False, description="是否包含已核实的异常账单"),
) -> dict[str, Any]:
    """异常站点待核实清单：按站点汇总环比越线账单，说明超阈值多少。"""
    items = service.list_abnormal_sites(only_unverified=not include_verified)
    return {"total": len(items), "环比异常阈值": service.get_config()["环比异常阈值"], "items": items}


@router.get("/readings")
def list_readings(
    site: str | None = None,
    month: str | None = None,
) -> dict[str, Any]:
    """分表读数明细，可按站点 / 月份过滤。"""
    items = service.list_readings(site=site, month=month)
    return {"total": len(items), "items": items}


@router.post("/readings", response_model=ActionResult)
def upsert_reading(payload: EntryPayload) -> ActionResult:
    """登记或补录某家某月分表读数；同站点同月份同运营商重复提交即更新。

    读数变化会触发相关账单重算：有读数后不再按合同比例兜底，缴费状态与异常标记随明细刷新。
    """
    entry, message = service.upsert_reading(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/sharing")
def list_sharing(site: str | None = None) -> dict[str, Any]:
    """各共站站点的分摊口径与各家合同比例。"""
    items = service.list_sharing(site=site)
    return {"supported_modes": list(SHARING_MODES), "total": len(items), "items": items}


@router.put("/sharing/basis", response_model=ActionResult)
def update_sharing_basis(payload: EntryPayload) -> ActionResult:
    """切换站点分摊口径（分表 / 合同比例）。

    存量账单先按旧口径留档，再按新口径重算分摊金额；账单总额（历史原值）不变。
    可选 ratios：{运营商: 比例} 一并更新合同比例，比例之和必须为 1。
    """
    values = payload.values
    site = str(values.get("所属站点") or "").strip()
    mode = str(values.get("分摊口径") or "").strip()
    ratios = values.get("ratios")
    if not isinstance(ratios, dict):
        ratios = None
    items, message = service.update_basis(site, mode, ratios)
    if items is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={"所属站点": site, "items": items})


@router.put("/sharing/ratio", response_model=ActionResult)
def update_sharing_ratio(payload: EntryPayload) -> ActionResult:
    """调整某站点某家运营商的合同分摊比例；按比例兜底的存量月份重算并留档。"""
    values = payload.values
    site = str(values.get("所属站点") or "").strip()
    carrier = str(values.get("运营商") or "").strip()
    try:
        ratio = float(values.get("合同分摊比例"))
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="合同分摊比例必须是数字")
    entry, message = service.update_ratio(site, carrier, ratio)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/archive")
def list_archive(
    site: str | None = None,
    bill_id: int | None = Query(default=None, alias="billId"),
) -> dict[str, Any]:
    """口径 / 比例变更的重算留档：账单历史原值与原分摊明细，按时间倒序。"""
    items = service.list_archive(site=site, bill_id=bill_id)
    return {"total": len(items), "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    site: str | None = Query(default=None, description="按所属站点过滤"),
    month: str | None = Query(default=None, description="按缴费月份过滤，如 2026-09"),
    status: str | None = Query(default=None, description="待缴费、已缴费、电费异常、已核实"),
    abnormal: bool | None = Query(default=None, description="true 只看环比越线账单"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按站点与缴费月份查电费明细，每条自带环比、超阈值幅度与各家分摊金额。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, site=site, month=month, status=status,
        abnormal=abnormal, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    site: str | None = None,
    month: str | None = None,
) -> dict[str, Any]:
    """导出电费清单（含环比与分摊明细），支持按站点 / 月份过滤。"""
    items, total = service.list_entries(site=site, month=month, page=1, size=10000)
    return {"module": "electricbill", "total": total, "items": items, "环比异常阈值": DEFAULT_THRESHOLD}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条电费记录明细（含环比与分摊）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"电费记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条站点电费账单，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="电费账单已登记，环比与分摊已同步计算", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """缴纳电费 / 核实确认 / 撤销核实；异常标记与综合状态随明细一起更新。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
