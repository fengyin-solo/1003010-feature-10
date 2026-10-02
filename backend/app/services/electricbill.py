"""电费管理业务规则。

围绕站点做两件事：
1. 比对：按「站点 + 缴费月份」排序计算账单环比，涨幅越过阈值的标记异常，并算清超出多少；
2. 分摊：共站站点优先按各家分表用电量分摊，缺读数的月份按合同约定比例兜底；
   分摊口径变更后，存量账单按新口径重算，重算前的金额快照进留档表，历史账单原值可查。

状态口径：
- 缴费状态（paid / verified）是人工动作结果，只被「缴纳电费」「核实确认」「撤销核实」改变；
- status 是可展示的综合状态：已核实 > 电费异常（环比越线未核实）> 已缴费 > 待缴费；
- abnormal 只看环比是否越线且未核实，和缴没缴费无关。
"""
from __future__ import annotations

import copy
from typing import Any

from app.store import store

MODULE = "electricbill"
READING_MODULE = "electricbill_reading"
SHARING_MODULE = "electricbill_sharing"
ARCHIVE_MODULE = "electricbill_archive"

REQUIRED_FIELDS = ["记录编号", "所属站点", "用电量", "电费金额", "缴费月份"]

STATUS_PENDING = "待缴费"
STATUS_PAID = "已缴费"
STATUS_ABNORMAL = "电费异常"
STATUS_VERIFIED = "已核实"
ACTION_RULES = {"缴纳电费": "paid", "核实确认": "verified", "撤销核实": "unverify"}

# 环比涨幅默认阈值：50%。可通过 GET/PUT /api/electricbill/config 调整
DEFAULT_THRESHOLD = 0.5

SHARING_MODE_METER = "分表"
SHARING_MODE_RATIO = "合同比例"
SHARING_MODES = (SHARING_MODE_METER, SHARING_MODE_RATIO)


def _to_float(value: Any) -> float | None:
    """把登记值转成数值；空串、非数字一律视为缺失，不让脏数据炸掉整月分摊。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _round2(value: float) -> float:
    return round(value + 1e-9, 2)


class ElectricbillService:
    # ---------- 配置 ----------

    def get_config(self) -> dict[str, Any]:
        rows = store.rows("electricbill_config")
        threshold = DEFAULT_THRESHOLD
        if rows:
            saved = _to_float(rows[0].get("环比异常阈值"))
            if saved is not None:
                threshold = saved
        return {"环比异常阈值": threshold}

    def update_config(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        threshold = _to_float(values.get("环比异常阈值"))
        if threshold is None:
            return None, "环比异常阈值必须是非负数字"
        rows = store.rows("electricbill_config")
        if rows:
            rows[0]["环比异常阈值"] = threshold
        else:
            rows.append({"id": 1, "环比异常阈值": threshold})
        # 阈值只影响异常标记，重算即可，不动金额、不留档
        self._recompute_all()
        return self.get_config(), f"环比异常阈值已更新为 {threshold:g}（按比例填写，0.5 表示 50%），全部账单已重新比对"

    # ---------- 账单查询 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        site: str | None = None,
        month: str | None = None,
        status: str | None = None,
        abnormal: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._decorate(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if site:
            rows = [row for row in rows if site in str(row.get("所属站点", ""))]
        if month:
            rows = [row for row in rows if str(row.get("缴费月份", "")) == month]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if abnormal is not None:
            rows = [row for row in rows if bool(row.get("abnormal")) == abnormal]
        rows.sort(key=lambda row: (str(row.get("所属站点", "")), str(row.get("缴费月份", ""))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._decorate(row) if row else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if _to_float(values.get(f)) is None and not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ("记录编号", "所属站点", "缴费月份", "票据编号"):
            entry[field] = str(values.get(field) or "").strip()
        entry["电表读数"] = _to_float(values.get("电表读数"))
        entry["用电量"] = _to_float(values.get("用电量"))
        entry["电费金额"] = _to_float(values.get("电费金额"))
        entry["paid"] = False
        entry["verified"] = False
        rows.append(entry)
        self._recompute_site(entry["所属站点"])
        return self._decorate(entry), []

    # ---------- 人工动作：缴费 / 核实 ----------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"电费记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于电费管理可执行范围"
        effect = ACTION_RULES[action]
        if effect == "paid":
            entry["paid"] = True
            msg = "电费已缴纳，缴费状态已随明细更新"
        elif effect == "verified":
            entry["verified"] = True
            msg = "异常已核实确认，该账单退出待核实清单"
        else:
            entry["verified"] = False
            msg = "已撤销核实，账单回到待核实清单"
        return self._decorate(entry), msg

    # ---------- 分表读数 ----------

    def list_readings(self, site: str | None = None, month: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(READING_MODULE)
        if site:
            rows = [row for row in rows if site in str(row.get("所属站点", ""))]
        if month:
            rows = [row for row in rows if str(row.get("缴费月份", "")) == month]
        return rows

    def upsert_reading(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        site = str(values.get("所属站点") or "").strip()
        month = str(values.get("缴费月份") or "").strip()
        carrier = str(values.get("运营商") or "").strip()
        usage = _to_float(values.get("用电量"))
        if not site or not month or not carrier:
            return None, "分表读数必须带齐 所属站点、缴费月份、运营商"
        if usage is None:
            return None, "分表用电量缺失或不是数字，无法据此分摊"
        rows = store.rows(READING_MODULE)
        existing = next(
            (row for row in rows
             if row.get("所属站点") == site and row.get("缴费月份") == month and row.get("运营商") == carrier),
            None,
        )
        if existing is not None:
            existing["用电量"] = usage
            existing["分表读数"] = _to_float(values.get("分表读数")) if values.get("分表读数") is not None else existing.get("分表读数")
            target = existing
            msg = f"{site} {month} {carrier} 分表读数已更新，相关账单按新读数重算分摊"
        else:
            target = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "status": "已登记", "pending": False, "abnormal": False,
                "所属站点": site, "缴费月份": month, "运营商": carrier,
                "分表读数": _to_float(values.get("分表读数")),
                "用电量": usage,
            }
            rows.append(target)
            msg = f"{site} {month} {carrier} 分表读数已登记，相关账单按分表用电量重算分摊"
        self._recompute_site(site)
        return target, msg

    # ---------- 分摊口径 / 合同比例 ----------

    def list_sharing(self, site: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(SHARING_MODULE)
        if site:
            rows = [row for row in rows if site in str(row.get("所属站点", ""))]
        return rows

    def update_basis(
        self, site: str, mode: str, ratios: dict[str, float] | None = None
    ) -> tuple[list[dict[str, Any]] | None, str]:
        """改站点分摊口径（或顺带改合同比例），存量账单按新口径重算，重算前留档。"""
        site = str(site or "").strip()
        if not site:
            return None, "未指定站点"
        if mode not in SHARING_MODES:
            return None, f"分摊口径只支持：{'、'.join(SHARING_MODES)}"
        rows = store.rows(SHARING_MODULE)
        site_rows = [row for row in rows if row.get("所属站点") == site]
        if not site_rows:
            return None, f"站点 {site} 还没有分摊配置，请先登记各家运营商的合同比例"

        if ratios:
            for row in site_rows:
                if row.get("运营商") in ratios:
                    value = _to_float(ratios[str(row["运营商"])])
                    if value is not None:
                        row["合同分摊比例"] = value
        total_ratio = sum(_to_float(row.get("合同分摊比例")) or 0 for row in site_rows)
        if abs(total_ratio - 1.0) > 0.001:
            return None, f"合同分摊比例之和必须等于 1，当前为 {total_ratio:g}"

        old_mode = str(site_rows[0].get("分摊口径") or "")
        # 重算前，先把该站点全部存量账单的分摊结果按原口径快照留档
        self._archive_site(site, reason=f"分摊口径变更：{old_mode} → {mode}")
        for row in site_rows:
            row["分摊口径"] = mode
        affected = sum(1 for row in store.rows(MODULE) if row.get("所属站点") == site)
        self._recompute_site(site)
        return site_rows, f"{site} 分摊口径已切为「{mode}」，{affected} 条存量账单已按新口径重算，原分摊金额已留档"

    def update_ratio(self, site: str, carrier: str, ratio: float) -> tuple[dict[str, Any] | None, str]:
        """单改某家合同比例：该站点无读数、按比例兜底的月份要重算，重算前留档。"""
        value = _to_float(ratio)
        if value is None:
            return None, "合同分摊比例必须是非负数字"
        rows = store.rows(SHARING_MODULE)
        target = next(
            (row for row in rows if row.get("所属站点") == site and row.get("运营商") == carrier),
            None,
        )
        if target is None:
            return None, f"{site} 的 {carrier} 还没有分摊配置"
        siblings = [row for row in rows if row.get("所属站点") == site]
        total_ratio = sum((_to_float(r.get("合同分摊比例")) or 0) for r in siblings) - (_to_float(target.get("合同分摊比例")) or 0) + value
        if abs(total_ratio - 1.0) > 0.001:
            return None, f"调整后该站点合同比例之和为 {total_ratio:g}，必须等于 1"
        self._archive_site(site, reason=f"合同比例变更：{carrier} → {value:g}")
        target["合同分摊比例"] = value
        self._recompute_site(site)
        return target, f"{site} {carrier} 合同比例已改为 {value:g}，按比例兜底的存量账单已重算，原值已留档"

    def list_archive(self, site: str | None = None, bill_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(ARCHIVE_MODULE)
        if site:
            rows = [row for row in rows if site in str(row.get("所属站点", ""))]
        if bill_id is not None:
            rows = [row for row in rows if int(row.get("账单id", 0)) == bill_id]
        rows.sort(key=lambda row: (str(row.get("所属站点", "")), str(row.get("缴费月份", "")), -int(row.get("id", 0))))
        return rows

    # ---------- 异常待核实清单 ----------

    def list_abnormal_sites(self, only_unverified: bool = True) -> list[dict[str, Any]]:
        """按站点汇总环比越线的账单，生成待核实清单。"""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in store.rows(MODULE):
            bill = self._decorate(raw)
            if not bill.get("环比异常"):
                continue
            if only_unverified and bill.get("verified"):
                continue
            grouped.setdefault(str(bill["所属站点"]), []).append(bill)

        result: list[dict[str, Any]] = []
        for site, bills in grouped.items():
            bills.sort(key=lambda b: str(b.get("缴费月份", "")))
            threshold = self.get_config()["环比异常阈值"]
            result.append({
                "所属站点": site,
                "异常账单数": len(bills),
                "环比阈值": threshold,
                "最新异常月份": bills[-1]["缴费月份"],
                "最新账单金额": bills[-1]["电费金额"],
                "上期账单金额": bills[-1].get("上期电费金额"),
                "最新环比涨幅": bills[-1].get("环比涨幅"),
                "超阈值幅度": bills[-1].get("超阈值幅度"),
                "异常说明": self._abnormal_text(bills[-1]),
                "待核实账单": [
                    {
                        "账单id": b["id"],
                        "记录编号": b["记录编号"],
                        "缴费月份": b["缴费月份"],
                        "电费金额": b["电费金额"],
                        "上期电费金额": b.get("上期电费金额"),
                        "环比涨幅": b.get("环比涨幅"),
                        "超阈值幅度": b.get("超阈值幅度"),
                        "异常说明": self._abnormal_text(b),
                    }
                    for b in bills
                ],
            })
        result.sort(key=lambda item: -float(item["最新环比涨幅"] or 0))
        return result

    # ---------- 核心：分摊 ----------

    def _site_sharing(self, site: str) -> list[dict[str, Any]]:
        return [row for row in store.rows(SHARING_MODULE) if row.get("所属站点") == site]

    def _month_readings(self, site: str, month: str) -> dict[str, float]:
        result: dict[str, float] = {}
        for row in store.rows(READING_MODULE):
            if row.get("所属站点") != site or row.get("缴费月份") != month:
                continue
            usage = _to_float(row.get("用电量"))
            if usage is not None:
                result[str(row["运营商"])] = usage
        return result

    def _allocate(self, bill: dict[str, Any]) -> dict[str, Any]:
        """算一张账单的分摊明细与分摊口径说明。

        站点口径是强约束（口径变更要让存量账单真正重算出新金额）：
        - 口径=合同比例：一律按合同约定比例分摊，不看当月有没有分表读数；
        - 口径=分表：各家读数齐全按分表用电量占比分摊；全都没读数时按合同比例兜底；
          部分有读数时，有读数的按主表平均单价（金额/总用电量）× 自家用量锁定，
          剩余金额由没读数的各家按其合同比例的相对占比分摊；
          主表总用电量缺失时，有读数一组只在合同比例池内按用量分，没读数一组在剩余比例池内按比例分。
        - 独占站点：全部归该运营商。
        """
        site = str(bill.get("所属站点", ""))
        month = str(bill.get("缴费月份", ""))
        amount = _to_float(bill.get("电费金额")) or 0.0
        total_usage = _to_float(bill.get("用电量"))
        sharing = self._site_sharing(site)
        readings = self._month_readings(site, month)

        if not sharing:
            return {"分摊明细": [], "分摊依据": "未配置", "分摊说明": "该站点未登记运营商分摊配置，无法分摊"}

        configured_mode = str(sharing[0].get("分摊口径") or SHARING_MODE_RATIO)
        ratios = {str(r["运营商"]): (_to_float(r.get("合同分摊比例")) or 0.0) for r in sharing}
        carriers = list(ratios)
        measured = [c for c in carriers if c in readings and readings[c] > 0]
        unmeasured = [c for c in carriers if c not in measured]
        warnings: list[str] = []

        shares: dict[str, float] = {}

        if len(carriers) == 1:
            shares[carriers[0]] = amount
            basis = "独占站点"
            note = f"{carriers[0]} 独占，电费 {amount:.2f} 全部由其承担"
        elif configured_mode == SHARING_MODE_RATIO:
            # 口径就是合同比例：即使有分表读数也按约定比例摊
            shares = self._share_by_ratio(amount, carriers, ratios)
            basis = SHARING_MODE_RATIO
            if measured:
                warnings.append("站点口径为合同比例，当月虽有分表读数，仍按合同约定比例分摊")
            note = "按合同约定比例分摊" + ("（当月分表读数仅作核对，不参与分摊）" if measured else "")
        elif not measured:
            shares = self._share_by_ratio(amount, carriers, ratios)
            basis = SHARING_MODE_RATIO
            warnings.append("站点口径为分表，但该月所有运营商均无分表读数，按合同约定比例兜底分摊")
            note = "该月无分表读数，按合同约定比例兜底分摊"
        elif not unmeasured:
            shares = self._share_by_usage(amount, carriers, readings)
            basis = SHARING_MODE_METER
            note = "各家分表读数齐全，按分表用电量占比分摊"
        else:
            # 部分读数：混合分摊
            if total_usage:
                unit_price = amount / total_usage
                measured_total = 0.0
                for carrier in measured:
                    measured_total += _round2(unit_price * readings[carrier])
                measured_total = _round2(measured_total)
                remaining = _round2(amount - measured_total)
                if remaining < 0:
                    # 分表用量之和超过主表用量，单价法会算出负数：退化成按合同比例池划分
                    warnings.append("分表用量合计超过主表总用电量，改按合同比例池划分两组")
                    ratio_sum = sum(ratios.values()) or 1.0
                    measured_pool = _round2(amount * sum(ratios[c] for c in measured) / ratio_sum)
                    measured_shares = self._share_by_usage(measured_pool, measured, readings)
                    unmeasured_pool = _round2(amount - measured_pool)
                    unmeasured_shares = self._share_relative(
                        unmeasured_pool, unmeasured, {c: ratios[c] for c in unmeasured}
                    )
                else:
                    measured_shares = {}
                    running = 0.0
                    for index, carrier in enumerate(measured):
                        value = _round2(unit_price * readings[carrier])
                        if index == len(measured) - 1:
                            value = _round2(measured_total - running)
                        measured_shares[carrier] = value
                        running = _round2(running + value)
                    unmeasured_shares = self._share_relative(
                        remaining, unmeasured, {c: ratios[c] for c in unmeasured}
                    )
            else:
                warnings.append("主表总用电量缺失，有读数一组在合同比例池内按用量分，其余按比例分")
                ratio_sum = sum(ratios.values()) or 1.0
                measured_pool = _round2(amount * sum(ratios[c] for c in measured) / ratio_sum)
                measured_shares = self._share_by_usage(measured_pool, measured, readings)
                unmeasured_pool = _round2(amount - measured_pool)
                unmeasured_shares = self._share_relative(
                    unmeasured_pool, unmeasured, {c: ratios[c] for c in unmeasured}
                )
            shares.update(measured_shares)
            shares.update(unmeasured_shares)
            basis = "分表+合同比例"
            warnings.append(
                f"{ '、'.join(unmeasured) } 无分表读数：有读数的按用量锁定，"
                f"{ '、'.join(unmeasured) } 按合同比例兜底分摊剩余金额"
            )
            note = "；".join(warnings)

        details = []
        for carrier in carriers:
            if len(carriers) == 1:
                source = "独占"
            elif basis == SHARING_MODE_RATIO:
                source = "合同比例"
            elif carrier in measured:
                source = "分表用电量"
            else:
                source = "合同比例兜底"
            details.append({
                "运营商": carrier,
                "分表用电量": readings.get(carrier),
                "合同分摊比例": ratios.get(carrier, 0.0),
                "分摊金额": _round2(shares.get(carrier, 0.0)),
                "分摊来源": source,
            })
        # 尾差校正：合计与账单金额的几分钱差额补给第一家
        diff = _round2(amount - sum(item["分摊金额"] for item in details))
        if details and abs(diff) >= 0.01:
            details[0]["分摊金额"] = _round2(details[0]["分摊金额"] + diff)
        return {"分摊明细": details, "分摊依据": basis, "分摊说明": note}

    @staticmethod
    def _share_by_ratio(amount: float, carriers: list[str], ratios: dict[str, float]) -> dict[str, float]:
        return ElectricbillService._share_relative(amount, carriers, ratios)

    @staticmethod
    def _share_by_usage(amount: float, carriers: list[str], readings: dict[str, float]) -> dict[str, float]:
        return ElectricbillService._share_relative(amount, carriers, readings)

    @staticmethod
    def _share_relative(amount: float, carriers: list[str], weights: dict[str, float]) -> dict[str, float]:
        """按权重分一笔钱，最后一家承担舍入尾差，保证合计严格等于金额。"""
        total_weight = sum(weights.get(c, 0.0) for c in carriers)
        if total_weight <= 0:  # 权重全丢了就均分，别让账单分不下去
            per = _round2(amount / len(carriers)) if carriers else 0.0
            return {c: per for c in carriers}
        result: dict[str, float] = {}
        running = 0.0
        for index, carrier in enumerate(carriers):
            if index == len(carriers) - 1:
                value = _round2(amount - running)
            else:
                value = _round2(amount * weights.get(carrier, 0.0) / total_weight)
            result[carrier] = value
            running = _round2(running + value)
        return result

    # ---------- 核心：环比 ----------

    def _mom_map(self, site: str) -> dict[str, dict[str, float | None]]:
        """同一站点按缴费月份排序，逐月找紧邻的上一个有金额的月份算环比。"""
        bills = sorted(
            (row for row in store.rows(MODULE) if row.get("所属站点") == site),
            key=lambda row: str(row.get("缴费月份", "")),
        )
        result: dict[str, dict[str, float | None]] = {}
        previous: dict[str, Any] | None = None
        for bill in bills:
            month = str(bill.get("缴费月份", ""))
            current = _to_float(bill.get("电费金额"))
            prev_amount = _to_float(previous.get("电费金额")) if previous else None
            if current is not None and prev_amount and prev_amount > 0:
                rate = (current - prev_amount) / prev_amount
                result[month] = {
                    "上期账单id": previous.get("id"),
                    "上期缴费月份": str(previous.get("缴费月份", "")),
                    "上期电费金额": prev_amount,
                    "环比涨幅": rate,
                    "超阈值幅度": _round2(max(0.0, rate - self.get_config()["环比异常阈值"]) * 100) / 100,
                }
            else:
                result[month] = {
                    "上期账单id": previous.get("id") if previous else None,
                    "上期缴费月份": str(previous.get("缴费月份", "")) if previous else None,
                    "上期电费金额": prev_amount,
                    "环比涨幅": None,
                    "超阈值幅度": None,
                }
            if current is not None:
                previous = bill
        return result

    def _abnormal_text(self, bill: dict[str, Any]) -> str:
        rate = bill.get("环比涨幅")
        over = bill.get("超阈值幅度")
        threshold = self.get_config()["环比异常阈值"]
        if rate is None:
            return "缺少可比对的上月账单，无法判定环比"
        if not bill.get("环比异常"):
            return f"环比 {rate * 100:.1f}%，未越过 {threshold * 100:.0f}% 阈值"
        return (
            f"{bill.get('上期缴费月份')} {bill.get('上期电费金额')} 元 → "
            f"{bill.get('缴费月份')} {bill.get('电费金额')} 元，"
            f"环比上涨 {rate * 100:.1f}%，越过 {threshold * 100:.0f}% 阈值 "
            f"{(over or 0) * 100:.1f} 个百分点"
        )

    # ---------- 组装 / 重算 ----------

    def _decorate(self, bill: dict[str, Any]) -> dict[str, Any]:
        """把存储行补充成接口明细：综合状态、环比、分摊，缴费状态随明细一起给出。"""
        result = dict(bill)
        mom = self._mom_map(str(bill.get("所属站点", ""))).get(str(bill.get("缴费月份", "")), {})
        threshold = self.get_config()["环比异常阈值"]
        rate = mom.get("环比涨幅")
        is_abnormal = rate is not None and rate > threshold and not bool(bill.get("verified"))

        result["上期缴费月份"] = mom.get("上期缴费月份")
        result["上期电费金额"] = mom.get("上期电费金额")
        result["环比涨幅"] = rate
        result["环比阈值"] = threshold
        result["超阈值幅度"] = mom.get("超阈值幅度")
        result["环比异常"] = is_abnormal
        result["异常说明"] = self._abnormal_text({**result})
        result.update(self._allocate(bill))

        result["缴费状态"] = "已缴费" if bill.get("paid") else "待缴费"
        result["核实状态"] = "已核实" if bill.get("verified") else "未核实"
        result["abnormal"] = is_abnormal
        result["pending"] = not bool(bill.get("paid")) or is_abnormal
        if bill.get("verified"):
            result["status"] = STATUS_VERIFIED
        elif is_abnormal:
            result["status"] = STATUS_ABNORMAL
        elif bill.get("paid"):
            result["status"] = STATUS_PAID
        else:
            result["status"] = STATUS_PENDING
        return result

    def _recompute_site(self, site: str) -> None:
        """读数、口径、金额变化后重算该站点：分摊结果写回账单，异常标记随明细刷新。"""
        for raw in store.rows(MODULE):
            if raw.get("所属站点") != site:
                continue
            decorated = self._decorate(raw)
            raw["abnormal"] = decorated["abnormal"]
            raw["pending"] = decorated["pending"]
            raw["分摊依据"] = decorated["分摊依据"]
            raw["分摊明细"] = decorated["分摊明细"]
            raw["分摊说明"] = decorated["分摊说明"]
            raw["环比涨幅"] = decorated["环比涨幅"]
            raw["超阈值幅度"] = decorated["超阈值幅度"]
            raw["异常说明"] = decorated["异常说明"]

    def _recompute_all(self) -> None:
        for site in {row.get("所属站点") for row in store.rows(MODULE)}:
            self._recompute_site(str(site))

    def _archive_site(self, site: str, *, reason: str) -> None:
        """重算前把该站点存量账单当前的分摊结果快照进留档表（历史账单原值留档）。"""
        archive_rows = store.rows(ARCHIVE_MODULE)
        next_id = max((int(row.get("id", 0)) for row in archive_rows), default=0) + 1
        for raw in store.rows(MODULE):
            if raw.get("所属站点") != site:
                continue
            decorated = self._decorate(raw)
            snapshot = {
                "id": next_id,
                "status": "已留档", "pending": False, "abnormal": False,
                "账单id": raw.get("id"),
                "记录编号": raw.get("记录编号"),
                "所属站点": site,
                "缴费月份": raw.get("缴费月份"),
                "账单金额": _to_float(raw.get("电费金额")),
                "原分摊口径": decorated.get("分摊依据"),
                "原分摊明细": copy.deepcopy(decorated.get("分摊明细", [])),
                "留档原因": reason,
            }
            archive_rows.append(snapshot)
            next_id += 1


service = ElectricbillService()
