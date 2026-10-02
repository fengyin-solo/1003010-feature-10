"""电费管理业务规则。

围绕需求里的四件事组织：
1. 按站点 + 缴费月份做环比，涨幅越过阈值标异常，并写明超幅；
2. 共站站点按各家分表用电量分摊，缺分表读数时回退到合同约定比例；
3. 分摊口径（合同比例/运营商名单/分表读数）变更后，存量记录按新口径重算
   分摊金额，重算前的分摊结果作为「历史账单」按原值留档；
4. 缴费状态与异常标记随明细一起联动更新，异常站点单独汇总成待核实清单。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "electricbill"
SITE_MODULE = "electricbill_site"
SETTING_MODULE = "electricbill_setting"

REQUIRED_FIELDS = ["所属站点", "缴费月份", "电费金额"]

# 账单状态沿用模块既有四态：异常优先于已缴费展示，已核实为终态。
STATUS_PENDING = "待缴费"
STATUS_PAID = "已缴费"
STATUS_ABNORMAL = "电费异常"
STATUS_VERIFIED = "已核实"

DEFAULT_THRESHOLD = 0.5  # 默认环比涨幅阈值 50%


# ---------------------------------------------------------------------------
# 工具函数：类型宽松地吃前端/种子数据，金额一律保留两位小数
# ---------------------------------------------------------------------------

def _round_money(value: Any) -> float:
    return round(float(value or 0) + 1e-9, 2)


def _to_float(value: Any) -> float | None:
    """分表读数/比例等可空数字；空串、None、非数字都视为未提供。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _prev_month(month: str) -> str | None:
    """缴费月份约定为 YYYY-MM，返回相邻上一个月。"""
    try:
        year, mon = (int(part) for part in month.split("-"))
    except (ValueError, AttributeError):
        return None
    if not 1 <= mon <= 12:
        return None
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year:04d}-{mon - 1:02d}"


def _normalize_operators(values: list[dict[str, Any]]) -> list[dict[str, float | str]]:
    """清洗站点运营商配置：去空名；比例留原值（含负数），由调用方校验。"""
    result: list[dict[str, float | str]] = []
    for item in values:
        name = str(item.get("运营商") or "").strip()
        if not name:
            continue
        ratio = _to_float(item.get("合同比例"))
        result.append({"运营商": name, "合同比例": ratio if ratio is not None else 0.0})
    return result


# ---------------------------------------------------------------------------
# 主服务
# ---------------------------------------------------------------------------

class ElectricbillService:
    # ----------------------------- 启动引导 --------------------------------

    def bootstrap(self) -> None:
        """服务启动时跑一次：存量数据按当前口径归位。

        种子/迁移进来的旧账单可能还挂着「按家数均摊」的旧口径结果，
        这里统一重算（旧结果进历史账单留档，账单原值不动），再重做环比。
        """
        for row in store.rows(MODULE):
            row.setdefault("分表读数", [])
            row.setdefault("历史账单", [])
            details = row.get("分摊明细")
            if isinstance(details, list) and details and all(
                str(item.get("分摊依据", "")).startswith("旧口径") for item in details
            ):
                self._reallocate_entry(row, reason="分摊口径变更（分表用电量优先、合同比例兜底）")
        self._rescan_all()

    # ----------------------------- 读取 -----------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        site: str | None = None,
        month: str | None = None,
        abnormal: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("记录编号", ""))
                or keyword in str(row.get("所属站点", ""))
            ]
        if site:
            rows = [row for row in rows if site == str(row.get("所属站点", ""))]
        if month:
            rows = [row for row in rows if month == str(row.get("缴费月份", ""))]
        if status:
            rows = [row for row in rows if self._derive_status(row) == status]
        if abnormal is not None:
            rows = [row for row in rows if bool(row.get("异常标记")) == abnormal]
        rows.sort(key=lambda row: (str(row.get("缴费月份", "")), int(row.get("id", 0))), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def list_sites(self) -> list[dict[str, Any]]:
        return store.rows(SITE_MODULE)

    def get_setting(self) -> dict[str, Any]:
        rows = store.rows(SETTING_MODULE)
        if not rows:
            rows.append({"id": 1, "环比涨幅阈值": DEFAULT_THRESHOLD})
        return rows[0]

    def get_threshold(self) -> float:
        return float(self.get_setting().get("环比涨幅阈值", DEFAULT_THRESHOLD) or DEFAULT_THRESHOLD)

    # ----------------------------- 登记 / 修改 -----------------------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        amount = _to_float(values.get("电费金额"))
        if amount is None or amount < 0:
            return None, ["电费金额需为不小于 0 的数字"]
        if not _prev_month(str(values.get("缴费月份"))):
            return None, ["缴费月份需为 YYYY-MM 格式，例如 2026-09"]

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update(self._pick_fields(values))
        entry["记录编号"] = entry["记录编号"] or f"ELEC-{entry['id']:04d}"
        entry["票据编号"] = entry["票据编号"] or ""
        entry["已缴费"] = False
        entry["已核实"] = False
        entry["分摊明细"] = []
        entry["异常标记"] = False
        entry["异常说明"] = ""
        entry["历史账单"] = []
        rows.append(entry)

        self._reallocate_entry(entry, reason="账单登记")
        self._rescan_site(str(entry["所属站点"]))
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, [f"电费记录 {entry_id} 不存在或已归档"]
        if "电费金额" in values:
            amount = _to_float(values.get("电费金额"))
            if amount is None or amount < 0:
                return None, ["电费金额需为不小于 0 的数字"]
        if "缴费月份" in values and not _prev_month(str(values.get("缴费月份"))):
            return None, ["缴费月份需为 YYYY-MM 格式，例如 2026-09"]

        # 账单原值（总额）订正后，旧口径分摊快照先留档（旧金额+旧分摊只留一版），
        # 再按新值重算；订正过的账单需要重新核实。
        if entry.get("分摊明细"):
            entry.setdefault("历史账单", []).append(self._snapshot(entry, reason="账单明细订正"))
        entry.update(self._pick_fields(values, partial=True))
        entry["已核实"] = False
        entry["异常标记"] = False
        entry["异常说明"] = ""

        self._reallocate_entry(entry, reason="账单明细订正", archive=False)
        self._rescan_all()
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"电费记录 {entry_id} 不存在或已归档"
        if action == "缴纳电费":
            entry["已缴费"] = True
        elif action == "登记异常":
            # 人工补登：环比没抓到的异常也能挂账，挂账后需要重新核实。
            entry["异常标记"] = True
            if not entry.get("异常说明"):
                entry["异常说明"] = "人工登记异常，待现场核实"
            entry["已核实"] = False
        elif action == "核实确认":
            entry["已核实"] = True
            entry["异常标记"] = False
            entry["异常说明"] = ""
        else:
            return None, f"动作「{action}」不属于电费管理可执行范围"
        return entry, f"电费记录已{action}"

    # ----------------------------- 配置：阈值 / 共站 ------------------------

    def update_threshold(self, threshold: Any) -> tuple[dict[str, Any] | None, str]:
        value = _to_float(threshold)
        if value is None or value <= 0:
            return None, "环比涨幅阈值需为大于 0 的小数（0.5 表示 50%）"
        setting = self.get_setting()
        old = float(setting.get("环比涨幅阈值", DEFAULT_THRESHOLD))
        if abs(value - old) < 1e-9:
            return setting, "阈值未变化，无需重算"
        setting["环比涨幅阈值"] = value
        setting["更新时间"] = _now()
        # 阈值是判定口径，所有站点的存量账单都要按新阈值重新判定异常。
        self._rescan_all()
        return setting, f"阈值已调整为 {value * 100:.0f}%，存量账单已按新口径重新比对"

    def upsert_site(
        self,
        site_name: str,
        operators: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str]:
        """登记/更新共站分摊口径：运营商名单与合同约定比例。

        口径变更后，该站点所有存量账单按新口径重算分摊金额，旧分摊结果留档。
        """
        site_name = (site_name or "").strip()
        if not site_name:
            return None, "所属站点不能为空"
        clean = _normalize_operators(operators)
        if not clean:
            return None, "至少需要登记一家运营商"
        if any(float(item["合同比例"]) < 0 for item in clean):
            return None, "合同比例不能为负数；确无约定时全部填 0，系统按家数均摊"

        rows = store.rows(SITE_MODULE)
        site = next((row for row in rows if row.get("站点名称") == site_name), None)
        created = site is None
        if site is None:
            site = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            rows.append(site)

        old_operators = [str(item.get("运营商")) for item in site.get("运营商", [])] if site else []
        site["站点名称"] = site_name
        site["运营商"] = clean
        site["是否共站"] = len(clean) > 1
        site["更新时间"] = _now()

        affected = [row for row in store.rows(MODULE) if str(row.get("所属站点")) == site_name]
        names_changed = old_operators and old_operators != [str(item["运营商"]) for item in clean]
        for entry in affected:
            self._reallocate_entry(
                entry,
                reason="分摊口径变更（新增/退出运营商）" if names_changed else "分摊口径变更（合同比例调整）",
            )
        self._rescan_site(site_name)

        if created:
            return site, f"站点「{site_name}」分摊口径已登记，{len(affected)} 条存量账单已按该口径计算"
        return site, f"站点「{site_name}」分摊口径已更新，{len(affected)} 条存量账单已按新口径重算，旧分摊结果已留档"

    # ----------------------------- 异常待核实清单 ---------------------------

    def abnormal_list(self) -> list[dict[str, Any]]:
        """异常站点单独汇成的待核实清单：按站点聚合最新一条异常。"""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in store.rows(MODULE):
            if row.get("异常标记"):
                grouped.setdefault(str(row.get("所属站点", "")), []).append(row)

        result: list[dict[str, Any]] = []
        for site_name, rows in grouped.items():
            rows.sort(key=lambda row: str(row.get("缴费月份", "")), reverse=True)
            latest = rows[0]
            result.append({
                "所属站点": site_name,
                "异常账单数": len(rows),
                "最近异常月份": latest.get("缴费月份"),
                "最近环比涨幅": latest.get("环比涨幅"),
                "最近电费金额": latest.get("电费金额"),
                "异常说明": latest.get("异常说明"),
                "待核实账单": [
                    {
                        "id": row.get("id"),
                        "记录编号": row.get("记录编号"),
                        "缴费月份": row.get("缴费月份"),
                        "电费金额": row.get("电费金额"),
                        "环比涨幅": row.get("环比涨幅"),
                        "异常说明": row.get("异常说明"),
                        "status": self._derive_status(row),
                    }
                    for row in rows
                ],
            })
        result.sort(key=lambda item: (item["最近环比涨幅"] or 0), reverse=True)
        return result

    # ----------------------------- 核心：分摊 ------------------------------

    def _reallocate_entry(self, entry: dict[str, Any], *, reason: str, archive: bool = True) -> None:
        """按当前口径重算一条账单的分摊明细。

        共站站点：各家分表读数齐全时按用电量比例分摊；任一家缺读数时，
        整条账单回退到合同约定比例（合同比例全 0 则均摊）。
        独站/未登记口径：全部记在本站名下。

        archive=False 时只重算不留档（调用方已自行留档，如账单订正）。
        """
        total = _round_money(entry.get("电费金额"))
        site = self._find_site(str(entry.get("所属站点", "")))
        operators = site.get("运营商", []) if site else []

        if not operators:
            shares = [{"运营商": str(entry.get("所属站点", "")), "分表用电量": None,
                      "分摊依据": "独站全额", "合同比例": 1.0}]
        else:
            shares = self._build_shares(entry, operators)

        details = self._split_amount(total, shares)
        if archive and entry.get("分摊明细"):
            entry.setdefault("历史账单", []).append(self._snapshot(entry, reason=reason))
        entry["分摊明细"] = details
        entry["分摊依据"] = details[0]["分摊依据"] if len(details) == 1 else (
            "分表用电量" if all(item["分表用电量"] is not None for item in details) else "合同约定比例"
        )

    def _build_shares(
        self,
        entry: dict[str, Any],
        operators: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        readings = self._readings_of(entry)
        # 分表读数齐全（每家都有且总和为正）→ 按各家用电量分摊。
        measured = {
            str(item.get("运营商")): _to_float(item.get("分表用电量"))
            for item in readings
        }
        all_measured = all(
            measured.get(str(op["运营商"])) is not None and measured[str(op["运营商"])] > 0
            for op in operators
        )
        if all_measured:
            return [
                {
                    "运营商": str(op["运营商"]),
                    "分表用电量": measured[str(op["运营商"])],
                    "合同比例": float(op["合同比例"]),
                    "分摊依据": "分表用电量",
                }
                for op in operators
            ]

        # 缺分表读数 → 合同约定比例；合同比例全为 0 时按家数均摊。
        ratio_sum = sum(float(op["合同比例"]) for op in operators)
        equal = ratio_sum <= 0
        per = 1.0 / len(operators) if equal else 0.0
        return [
            {
                "运营商": str(op["运营商"]),
                "分表用电量": measured.get(str(op["运营商"])),
                "合同比例": per if equal else float(op["合同比例"]) / ratio_sum,
                "分摊依据": "合同约定比例" if not equal else "合同未约定，按家数均摊",
            }
            for op in operators
        ]

    @staticmethod
    def _readings_of(entry: dict[str, Any]) -> list[dict[str, Any]]:
        raw = entry.get("分表读数")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    @staticmethod
    def _split_amount(total: float, shares: list[dict[str, Any]]) -> list[dict[str, Any]]:
        weights: list[float] = []
        for share in shares:
            if share["分摊依据"] == "分表用电量":
                weights.append(float(share["分表用电量"] or 0))
            else:
                weights.append(float(share["合同比例"] or 0))
        weight_sum = sum(weights)

        details: list[dict[str, Any]] = []
        allocated = 0.0
        for index, share in enumerate(shares):
            is_last = index == len(shares) - 1
            amount = _round_money(total if weight_sum <= 0 or is_last else total * weights[index] / weight_sum)
            if is_last:
                amount = _round_money(total - allocated)
            allocated = _round_money(allocated + amount)
            details.append({
                "运营商": share["运营商"],
                "分表用电量": share.get("分表用电量"),
                "分摊比例": round((weights[index] / weight_sum) if weight_sum > 0 else 0.0, 4),
                "分摊金额": amount,
                "分摊依据": share["分摊依据"],
            })
        return details

    # ----------------------------- 核心：环比 ------------------------------

    def _rescan_site(self, site_name: str) -> None:
        self._rescan([row for row in store.rows(MODULE) if str(row.get("所属站点")) == site_name])

    def _rescan_all(self) -> None:
        self._rescan(store.rows(MODULE))

    def _rescan(self, rows: list[dict[str, Any]]) -> None:
        """重新判定一组账单的环比异常（内部按站点分组，基线不串站）。

        人工核实通过（已核实）的账单不再回挂异常；人工登记的异常保留，
        但环比字段会同步刷新。
        """
        threshold = self.get_threshold()
        by_site: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_site.setdefault(str(row.get("所属站点", "")), []).append(row)

        for site_rows in by_site.values():
            by_month: dict[str, dict[str, Any]] = {}
            for row in sorted(site_rows, key=lambda item: str(item.get("缴费月份", ""))):
                month = str(row.get("缴费月份", ""))
                row.setdefault("异常标记", False)
                row.setdefault("异常说明", "")
                manual = bool(row.get("异常标记")) and "人工登记" in str(row.get("异常说明", ""))
                if row.get("已核实"):
                    row["环比涨幅"] = None
                    if row.get("异常说明") == "人工登记异常，待现场核实":
                        row["异常标记"] = False
                        row["异常说明"] = ""
                    by_month[month] = row
                    continue

                prev_key = _prev_month(month)
                prev = by_month.get(str(prev_key)) if prev_key else None
                prev_amount = _to_float(prev.get("电费金额")) if prev else None
                amount = _to_float(row.get("电费金额")) or 0.0

                if prev is not None and prev_amount and prev_amount > 0:
                    rate = round((amount - prev_amount) / prev_amount, 4)
                    row["环比涨幅"] = rate
                    over = _round_money(amount - prev_amount)
                    if rate > threshold:
                        row["异常标记"] = True
                        row["异常说明"] = (
                            f"环比上涨 {rate * 100:.1f}%，超过 {threshold * 100:.0f}% 阈值"
                            f"（上月 {prev_amount:.2f} 元→本月 {amount:.2f} 元，多 {over:.2f} 元）"
                        )
                    elif not manual:
                        row["异常标记"] = False
                        row["异常说明"] = ""
                else:
                    row["环比涨幅"] = None
                    if not manual:
                        row["异常标记"] = False
                        row["异常说明"] = ""
                by_month[month] = row

    # ----------------------------- 状态联动 --------------------------------

    def _derive_status(self, row: dict[str, Any]) -> str:
        """缴费状态与异常标记跟着明细一起联动后的对外状态。"""
        if row.get("已核实"):
            return STATUS_VERIFIED
        if row.get("异常标记"):
            return STATUS_ABNORMAL
        return STATUS_PAID if row.get("已缴费") else STATUS_PENDING

    def decorate(self, row: dict[str, Any]) -> dict[str, Any]:
        status = self._derive_status(row)
        row["status"] = status
        row["缴费状态"] = status
        row["pending"] = not row.get("已核实")
        row["abnormal"] = bool(row.get("异常标记"))
        return row

    # ----------------------------- 其它内部 --------------------------------

    def _find_site(self, site_name: str) -> dict[str, Any] | None:
        for row in store.rows(SITE_MODULE):
            if str(row.get("站点名称", "")) == site_name:
                return row
        return None

    @staticmethod
    def _pick_fields(values: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
        fields = ["记录编号", "所属站点", "电表读数", "用电量", "电费金额", "缴费月份", "票据编号"]
        picked: dict[str, Any] = {}
        for field in fields:
            if field in values or not partial:
                picked[field] = values.get(field, "")
        if "电费金额" in picked:
            picked["电费金额"] = _round_money(picked["电费金额"])
        if "分表读数" in values:
            picked["分表读数"] = values.get("分表读数") or []
        return picked

    @staticmethod
    def _snapshot(entry: dict[str, Any], *, reason: str) -> dict[str, Any]:
        """历史账单：账单原值（总额）不变，留档的是当时的分摊结果。"""
        return {
            "留档时间": _now(),
            "变更原因": reason,
            "电费金额": entry.get("电费金额"),
            "分摊依据": entry.get("分摊依据", ""),
            "分摊明细": [dict(item) for item in entry.get("分摊明细", [])],
        }


service = ElectricbillService()
