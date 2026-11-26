"""消息路由领域服务：IoT消息Topic匹配与转发规则管理。

提供路由匹配、优先级排序、消息过滤等真实业务能力。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


class MsgRouterService(BaseModuleService):
    MODULE = "msg_router"
    ENTITY = "路由规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则编号", "匹配Topic", "目标Topic", "路由条件", "转发方式", "过滤表达式", "优先级", "启用状态"]
    REQUIRED_FIELDS = ["规则编号", "匹配Topic", "目标Topic"]
    STATUS_ORDER = ["草稿", "待验证", "已启用", "已停用"]
    ACTION_RULES = {"提交验证": "待验证", "启用规则": "已启用", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def match_topics(self, source_topic: str) -> list[dict[str, Any]]:
        """根据源Topic匹配所有已启用的路由规则，按优先级排序。

        业务流程：
        1. 查找已启用的路由规则
        2. 按Topic匹配（支持通配符 # 和 +）
        3. 按优先级排序
        4. 返回匹配结果
        """
        rows = store.rows(self.MODULE)
        matched = []

        for row in rows:
            if row.get("status") != "已启用":
                continue
            match_topic = row.get("匹配Topic", "")
            if self._topic_match(source_topic, match_topic):
                matched.append(row)

        # 按优先级排序（高优先级先）
        def _priority(row):
            p = row.get("优先级", "0")
            try:
                return int(p)
            except (ValueError, TypeError):
                return 0

        matched.sort(key=_priority, reverse=True)

        return [{
            "规则编号": r.get("规则编号"),
            "匹配Topic": r.get("匹配Topic"),
            "目标Topic": r.get("目标Topic"),
            "转发方式": r.get("转发方式"),
            "优先级": r.get("优先级"),
            "过滤表达式": r.get("过滤表达式"),
        } for r in matched]

    @staticmethod
    def _topic_match(source: str, pattern: str) -> bool:
        """MQTT Topic 通配符匹配。

        支持：
        - # 匹配多级（只能在末尾）
        - + 匹配单级
        """
        source_parts = source.split("/")
        pattern_parts = pattern.split("/")

        for i, pp in enumerate(pattern_parts):
            if pp == "#":
                return i <= len(source_parts)
            if i >= len(source_parts):
                return False
            if pp == "+":
                continue
            if pp != source_parts[i]:
                return False
        return len(source_parts) == len(pattern_parts)

    def route_decoded(self, source_topic: str, decoded: dict[str, Any]) -> dict[str, Any]:
        """把已经解码好的字段交给路由规则，返回实际转发计划和目标Topic。

        解码页确认过的解析结果原样传进来，路由层不再自己解析报文，
        也不允许把字段重新拼回十六进制文本。
        """
        matches = self.match_topics(source_topic)
        if not matches:
            return {"ok": False, "message": f"Topic {source_topic} 无匹配路由规则"}

        payload = {
            "value": decoded.get("value"),
            "data_type": decoded.get("data_type"),
            "byte_order": decoded.get("byte_order"),
            "origin": "confirmed_decode",
        }
        plans = []
        for rule in matches:
            filter_expr = rule.get("过滤表达式", "")
            passed = True
            if filter_expr:
                try:
                    passed = bool(eval(filter_expr, {"__builtins__": {}}, {"payload": payload}))
                except Exception:
                    passed = True
            if passed:
                plans.append({
                    "规则编号": rule["规则编号"],
                    "目标Topic": rule["目标Topic"],
                    "转发方式": rule["转发方式"],
                    "优先级": rule.get("优先级"),
                })

        if not plans:
            return {
                "ok": False,
                "message": f"Topic {source_topic} 匹配到路由规则但均被过滤条件拦截",
                "matched": matches,
            }

        return {
            "ok": True,
            "message": f"消息已按解码结果路由到 {plans[0]['目标Topic']}",
            "source_topic": source_topic,
            "payload": payload,
            "plans": plans,
            "chosen": plans[0],
            "route_time": _now(),
        }

    def route_message(self, source_topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        """对消息进行路由分发：匹配所有规则并输出转发计划。

        业务流程：
        1. 匹配路由规则
        2. 应用过滤表达式
        3. 生成转发目标计划
        4. 返回路由结果
        """
        matches = self.match_topics(source_topic)
        if not matches:
            return {"ok": False, "message": f"Topic {source_topic} 无匹配路由规则"}

        results = []
        for rule in matches:
            filter_expr = rule.get("过滤表达式", "")
            if filter_expr:
                try:
                    passed = eval(filter_expr, {"__builtins__": {}}, {"payload": payload})
                except Exception:
                    passed = True
            else:
                passed = True

            if passed:
                results.append({
                    "规则编号": rule["规则编号"],
                    "目标Topic": rule["目标Topic"],
                    "转发方式": rule["转发方式"],
                    "filter_passed": passed,
                })

        return {
            "ok": True,
            "message": f"消息已路由，匹配 {len(results)} 条规则",
            "source_topic": source_topic,
            "routing_results": results,
            "route_time": _now(),
        }

    def routing_statistics(self) -> dict[str, Any]:
        """路由规则统计：各状态分布、转发方式分布等。"""
        rows = store.rows(self.MODULE)
        total = len(rows)
        by_status = {}
        by_method = {}
        for row in rows:
            s = row.get("status", "未知")
            by_status[s] = by_status.get(s, 0) + 1
            m = row.get("转发方式", "未知")
            by_method[m] = by_method.get(m, 0) + 1

        return {
            "total_rules": total,
            "by_status": by_status,
            "by_forward_method": by_method,
            "enabled_count": by_status.get("已启用", 0),
        }