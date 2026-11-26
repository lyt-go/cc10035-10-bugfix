"""报文解码领域服务：IoT二进制/文本报文的解析与校验。

提供报文解析、字段提取、校验计算等真实业务能力。
"""
from __future__ import annotations

import hashlib
import struct
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))

# 一张送测申请在同一个版本上只允许产生一笔路由流水，
# 重复确认同一份凭据时直接返回这笔已有记录。
SUBMISSION = "送测申请"
ROUTE_RECEIPT = "路由回执"
INT_TYPES = ("uint32", "int32")
SUPPORTED_TYPES = ("uint32", "int32", "float64", "hex")
BYTE_ORDERS = ("big", "little")


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def normalize_hex(raw_hex: str) -> str:
    """去掉空白和常见的 0x 前缀后统一成大写 hex 字符串。"""
    text = "".join(str(raw_hex or "").split())
    if text[:2].lower() == "0x":
        text = text[2:]
    return text.upper()


def _rule_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """从一条解码规则里抽出真正参与解析的四个字段，作为送测凭据的基身。"""
    return {
        "规则编号": entry.get("规则编号"),
        "协议类型": entry.get("协议类型"),
        "报文标识": entry.get("报文标识"),
        "字段偏移": str(entry.get("字段偏移", "0")),
        "字段长度": str(entry.get("字段长度", "8")),
        "数据类型": str(entry.get("数据类型", "hex")),
        "字节序": str(entry.get("字节序", "big")),
    }


def _normalize_data_type(value: Any) -> str:
    data_type = str(value or "").strip().lower()
    return data_type or "hex"


def _normalize_byte_order(value: Any) -> str:
    order = str(value or "").strip().lower()
    return order if order in BYTE_ORDERS else "big"


def _value_type_name(data_type: str) -> str:
    if data_type == "int32":
        return "有符号32位整数"
    if data_type == "uint32":
        return "无符号32位整数"
    if data_type == "float64":
        return "64位浮点数"
    return "十六进制字节"


def value_type_label(value: Any, data_type: str) -> str:
    """给一个解析结果配上可读的类型说明，界面和回执共用。"""
    label = _value_type_name(_normalize_data_type(data_type))
    if isinstance(value, int):
        return "int" if data_type == "int32" else ("uint" if data_type == "uint32" else label)
    return label


def _coerce_rule_entry(values: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    """把 create/update 传入的值叠加到一条规则上，返回可用于解码的规则快照。"""
    merged = _rule_payload(fallback)
    if not values:
        return merged
    for field in ("规则编号", "字段偏移", "字段长度", "数据类型", "字节序"):
        if values.get(field) is not None:
            merged[field] = str(values.get(field))
    merged["数据类型"] = _normalize_data_type(merged["数据类型"])
    merged["字节序"] = _normalize_byte_order(merged["字节序"])
    return merged


def decode_field(payload: bytes, rule: dict[str, Any]) -> dict[str, Any]:
    """按规则里的偏移/长度/数据类型/字节序解析一段报文字节。

    这里是整条链路唯一的解析实现：解码页预览、送测申请、路由服务
    消费的都是同一份结果，避免预览值与实际发送值各算一套。
    """
    data_type = _normalize_data_type(rule.get("数据类型"))
    byte_order = _normalize_byte_order(rule.get("字节序"))
    try:
        offset = int(str(rule.get("字段偏移", "0")).strip() or "0")
    except (TypeError, ValueError):
        offset = 0
    try:
        length = int(str(rule.get("字段长度", "8")).strip() or "8")
    except (TypeError, ValueError):
        length = 8
    offset = max(offset, 0)
    length = max(length, 0)

    field_data = payload[offset:offset + length] if length else b""
    available = len(field_data)

    parsed_value: Any = None
    hex_text = field_data.hex().upper()
    error = ""

    if data_type in INT_TYPES:
        if available < 4:
            error = f"字段长度不足：{data_type} 需要至少 4 字节，实际 {available} 字节"
        else:
            endian = ">" if byte_order == "big" else "<"
            fmt = f"{endian}i" if data_type == "int32" else f"{endian}I"
            parsed_value = struct.unpack(fmt, field_data[:4])[0]
            hex_text = field_data[:4].hex().upper()
    elif data_type == "float64":
        if available < 8:
            error = f"字段长度不足：float64 需要至少 8 字节，实际 {available} 字节"
        else:
            endian = ">" if byte_order == "big" else "<"
            parsed_value = struct.unpack(f"{endian}d", field_data[:8])[0]
            hex_text = field_data[:8].hex().upper()
    else:
        if available == 0:
            error = f"字段偏移 {offset} 已越过报文末尾，没有可解析的字节"
        else:
            parsed_value = hex_text

    return {
        "ok": not error,
        "error": error,
        "offset": offset,
        "length": length,
        "data_type": data_type,
        "byte_order": byte_order,
        "field_hex": hex_text,
        "parsed_value": parsed_value,
    }


class MsgDecodeService(BaseModuleService):
    MODULE = "msg_decode"
    ENTITY = "解码规则"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["规则编号", "协议类型", "报文标识", "字段偏移", "字段长度", "数据类型", "字节序", "校验方式"]
    REQUIRED_FIELDS = ["规则编号", "协议类型", "报文标识"]
    STATUS_ORDER = ["草稿", "待验证", "已启用", "已停用"]
    ACTION_RULES = {"提交验证": "待验证", "启用规则": "已启用", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    # ==================== 规则版本 ====================

    def rule_version(self, entry: dict[str, Any]) -> str:
        """规则的解析版本：偏移/长度/类型/字节序任一变化都会得到新版本号。"""
        payload = _rule_payload(entry)
        base = "|".join(str(payload[key]) for key in ("字段偏移", "字段长度", "数据类型", "字节序"))
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:6]
        return f"v{entry.get('规则版本', 1)}-{digest}"

    def _validate_rule_values(self, values: dict[str, Any]) -> str:
        """校验一次规则修改的取值；返回空串表示合法。"""
        candidate = {}
        for field in ("数据类型", "字节序", "字段偏移", "字段长度"):
            if field in values and values[field] is not None:
                candidate[field] = str(values[field]).strip()
        if "数据类型" in candidate and (
            _normalize_data_type(candidate["数据类型"]) not in SUPPORTED_TYPES
        ):
            return f"不支持的数据类型：{candidate['数据类型']}"
        if "字节序" in candidate and candidate["字节序"].lower() not in BYTE_ORDERS:
            return f"不支持的字节序：{candidate['字节序']}"
        for field in ("字段偏移", "字段长度"):
            if field in candidate:
                try:
                    if int(candidate[field]) < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    return f"{field}必须是非负整数"
        return ""

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """修改解码规则的解析参数，并把版本号加一。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        error = self._validate_rule_values(values)
        if error:
            return None, error
        changed = False
        for field in self.LIST_FIELDS:
            if field in values:
                new_value = values.get(field)
                if new_value is not None and str(entry.get(field)) != str(new_value):
                    entry[field] = new_value
                    changed = True
        if changed:
            entry["规则版本"] = int(entry.get("规则版本", 1)) + 1
            entry["更新时间"] = _now()
        return entry, f"{self.ENTITY} {entry.get('规则编号')} 已更新"

    def rule_snapshot(self, rule_id: str) -> dict[str, Any] | None:
        """按规则编号取当前规则快照，供送测和路由核对版本。"""
        for row in store.rows(self.MODULE):
            if str(row.get("规则编号")) == str(rule_id):
                return row
        return None

    # ==================== 解析 ====================

    def parse_entry(self, entry: dict[str, Any], raw_hex: str) -> dict[str, Any]:
        """以一条规则为准解析报文，返回可供界面和路由共同引用的完整解析结果。"""
        normalized = normalize_hex(raw_hex)
        result: dict[str, Any] = {
            "ok": False,
            "message": "",
            "decode_rule": entry.get("规则编号"),
            "protocol": entry.get("协议类型"),
            "raw_hex": normalized,
            "rule_version": self.rule_version(entry),
            "data_type": _normalize_data_type(entry.get("数据类型")),
            "byte_order": _normalize_byte_order(entry.get("字节序")),
            "field_offset": 0,
            "field_length": 0,
            "field_hex": "",
            "parsed_value": None,
            "checksum_type": entry.get("校验方式", "none"),
            "checksum_valid": True,
        }

        if not normalized:
            result["message"] = "报文不能为空"
            return result
        if len(normalized) % 2 != 0:
            result["message"] = "报文hex字符串长度必须为偶数"
            return result
        try:
            binary_data = bytes.fromhex(normalized)
        except ValueError:
            result["message"] = "报文不是合法的hex字符串"
            return result

        # 按报文标识切分：标识之后的字节才是字段载荷。
        identifier = str(entry.get("报文标识", "") or "")
        if identifier:
            ident_bytes = identifier.encode()
            if ident_bytes in binary_data:
                payload = binary_data[binary_data.index(ident_bytes) + len(ident_bytes):]
            else:
                payload = binary_data
        else:
            payload = binary_data

        decoded = decode_field(payload, entry)
        result.update({
            "field_offset": decoded["offset"],
            "field_length": decoded["length"],
            "field_hex": decoded["field_hex"],
            "parsed_value": decoded["parsed_value"],
            "value_type": _value_type_name(decoded["data_type"]),
        })
        if not decoded["ok"]:
            result["message"] = decoded["error"]
            return result

        checksum_type = str(entry.get("校验方式", "none"))
        checksum_valid = True
        if checksum_type == "xor8":
            xor_sum = 0
            for b in payload:
                xor_sum ^= b
            checksum_valid = xor_sum == 0
        elif checksum_type == "crc16":
            checksum_valid = sum(payload) % 65535 == 0
        result["checksum_valid"] = checksum_valid

        result["ok"] = True
        result["message"] = "报文解析完成"
        return result

    def parse_message(self, raw_hex: str, protocol_type: str) -> dict[str, Any]:
        """解析原始报文：根据协议类型查找已启用解码规则并解析hex报文。

        业务流程：
        1. 查找该协议已启用的解码规则
        2. 按报文标识切分报文
        3. 按字段偏移/长度/类型/字节序提取字段（int32 按有符号解释）
        4. 进行校验计算
        """
        rows = store.rows(self.MODULE)
        rules = [
            row for row in rows
            if row.get("协议类型") == protocol_type
            and row.get("status") == "已启用"
        ]
        if not rules:
            return {"ok": False, "message": f"未找到协议 {protocol_type} 的已启用解码规则"}

        return self.parse_entry(rules[0], raw_hex)

    # ==================== 送测申请 ====================

    def _payload_bytes(self, rule: dict[str, Any], raw_hex: str) -> tuple[bytes | None, str]:
        """把原始hex按规则里的报文标识切出字段载荷；失败时返回可读原因。"""
        normalized = normalize_hex(raw_hex)
        if not normalized:
            return None, "报文不能为空"
        if len(normalized) % 2 != 0:
            return None, "报文hex字符串长度必须为偶数"
        try:
            binary_data = bytes.fromhex(normalized)
        except ValueError:
            return None, "报文不是合法的hex字符串"
        identifier = str(rule.get("报文标识", "") or "")
        if identifier:
            ident_bytes = identifier.encode()
            if ident_bytes in binary_data:
                binary_data = binary_data[binary_data.index(ident_bytes) + len(ident_bytes):]
        return binary_data, ""

    def _interpretation(
        self, payload: bytes, rule: dict[str, Any], data_type: str,
    ) -> dict[str, Any] | None:
        decoded = decode_field(payload, {**rule, "数据类型": data_type})
        if not decoded["ok"]:
            return None
        value = decoded["parsed_value"]
        if isinstance(value, float):
            value_text = repr(value)
        else:
            value_text = str(value)
        return {
            "data_type": data_type,
            "value": value,
            "value_text": value_text,
            "value_type": _value_type_name(data_type),
            "byte_order": decoded["byte_order"],
            "field_offset": decoded["offset"],
            "field_length": decoded["length"],
            "field_hex": decoded["field_hex"],
            "label": f"{data_type}({value_text})",
        }

    def interpretations(self, rule: dict[str, Any], raw_hex: str) -> tuple[list[dict[str, Any]], str]:
        """列出同一段字节所有合法的解释（int32 / uint32，长度足够时再加 float64）。

        这是“界面要看出还能用哪份合法解释”的数据来源：规则改了类型，
        旧解释仍然留在列表里，新的当前解释也出现。
        """
        payload, error = self._payload_bytes(rule, raw_hex)
        if payload is None:
            return [], error
        types = ["int32", "uint32"]
        if len(payload) >= 8:
            types.append("float64")
        results = []
        for data_type in types:
            item = self._interpretation(payload, rule, data_type)
            if item is not None:
                results.append(item)
        if not results:
            return [], "报文字段长度不足以解释为 32 位数值（至少需要 4 字节）"
        return results, ""

    def preview(self, rule_id: str, raw_hex: str) -> dict[str, Any]:
        """解码页预览：返回按当前规则解析出的值，以及同一段字节的所有合法解释。"""
        rule = self.rule_snapshot(rule_id)
        if rule is None:
            return {"ok": False, "message": f"解码规则 {rule_id} 不存在"}
        parsed = self.parse_entry(rule, raw_hex)
        interpretations, error = self.interpretations(rule, raw_hex)
        if not interpretations:
            return {
                "ok": False,
                "message": parsed.get("message") or error,
                "rule_id": rule_id,
                "raw_hex": normalize_hex(raw_hex),
            }
        rule_type = _normalize_data_type(rule.get("数据类型"))
        chosen = next((item for item in interpretations if item["data_type"] == rule_type), None)
        if chosen is not None:
            value_text = chosen["value_text"]
        else:
            raw_value = parsed.get("parsed_value")
            value_text = repr(raw_value) if isinstance(raw_value, float) else str(raw_value or "")
        return {
            "ok": parsed["ok"],
            "message": parsed["message"] if parsed["ok"] else (parsed["message"] or error),
            "rule_id": rule.get("规则编号"),
            "protocol": rule.get("协议类型"),
            "rule_version": self.rule_version(rule),
            "data_type": rule_type,
            "byte_order": parsed["byte_order"],
            "raw_hex": parsed["raw_hex"],
            "field_hex": parsed["field_hex"],
            "parsed_value": parsed["parsed_value"],
            "value_text": value_text,
            "value_type": _value_type_name(rule_type),
            "interpretations": interpretations,
            "checksum_valid": parsed["checksum_valid"],
        }

    def update_rule(self, rule_id: str, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """按规则编号修改解析参数（另一窗口把 int32 改成 uint32 走这里）。"""
        rule = self.rule_snapshot(rule_id)
        if rule is None:
            return None, f"解码规则 {rule_id} 不存在"
        return self.update_entry(int(rule.get("id", 0)), values or {})

    def _find_submission(self, credential: str) -> dict[str, Any] | None:
        for row in store.records(SUBMISSION):
            if row.get("凭据编号") == credential:
                return row
        return None

    def _find_application(
        self, rule_id: str, raw_hex: str, topic: str,
    ) -> dict[str, Any] | None:
        """同一规则、同一报文、同一Topic只保留一张送测申请，重复确认落在它上面。"""
        for row in store.records(SUBMISSION):
            if (
                str(row.get("规则编号")) == str(rule_id)
                and row.get("raw_hex") == raw_hex
                and row.get("topic") == topic
            ):
                return row
        return None

    def confirm_submission(
        self,
        rule_id: str,
        raw_hex: str,
        data_type: str | None = None,
        topic: str = "",
        expected_version: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """确认一份解码预览并生成/更新送测凭据。

        只有操作人确认过的解释会写进凭据；确认时如果规则已经被别人改过，
        当前申请照样保留，并把冲突和可用解释一起带回去。
        """
        rule = self.rule_snapshot(rule_id)
        if rule is None:
            return None, f"解码规则 {rule_id} 不存在"

        normalized = normalize_hex(raw_hex)
        interpretations, error = self.interpretations(rule, normalized)
        if not interpretations:
            return None, error

        rule_type = _normalize_data_type(rule.get("数据类型"))
        chosen_type = _normalize_data_type(data_type) if data_type else rule_type
        if chosen_type == "hex":
            return None, "送测不允许发送十六进制文本，请确认 int32 或 uint32 等数值解释"
        confirmed = next(
            (item for item in interpretations if item["data_type"] == chosen_type), None)
        if confirmed is None:
            return None, f"字段字节无法解释为 {chosen_type}，可用解释：{'、'.join(i['label'] for i in interpretations)}"

        current_version = self.rule_version(rule)
        confirmed_version = expected_version or current_version
        conflict = expected_version is not None and expected_version != current_version
        current_interpretation = next(
            (item for item in interpretations if item["data_type"] == rule_type), None)

        # 同一规则、同一报文、同一Topic 只有一张凭据；重复确认更新它，不新开流水。
        existing = self._find_application(str(rule.get("规则编号")), normalized, topic)
        if existing is not None:
            # 已产生路由流水的凭据不再改解释，避免回执与实际发送值对不上。
            if existing.get("route_receipt"):
                existing["available_interpretations"] = interpretations
                existing["current_interpretation"] = current_interpretation
                existing["current_version"] = current_version
                if existing.get("confirmed_type") != confirmed["data_type"]:
                    return existing, (
                        f"凭据 {existing.get('凭据编号')} 已按 "
                        f"{existing.get('confirmed', {}).get('label')} 送测，不能改发 "
                        f"{confirmed['label']}"
                    )
                return existing, f"该确认已存在，沿用原送测凭据 {existing.get('凭据编号')}"
            existing.update({
                "confirmed": confirmed,
                "confirmed_type": confirmed["data_type"],
                "confirmed_value": confirmed["value"],
                "confirmed_version": confirmed_version,
                "current_version": current_version,
                "current_interpretation": current_interpretation,
                "available_interpretations": interpretations,
                "conflict": conflict,
                "conflict_reason": (
                    f"解码规则在确认期间从 {confirmed_version} 变为 {current_version}"
                    if conflict else ""
                ),
                "status": "版本冲突" if conflict else "待送测",
                "updated_at": _now(),
            })
            message = (
                f"规则已变化，保留本次申请（确认 {confirmed['label']}），"
                f"当前规则解释 {current_interpretation['label'] if current_interpretation else '不可用'}"
                if conflict else f"已按 {confirmed['label']} 更新送测凭据 {existing.get('凭据编号')}"
            )
            return existing, message

        rows = store.records(SUBMISSION)
        entry = {
            "id": _next_id(rows),
            "凭据编号": f"DEC-{len(rows) + 1:04d}",
            "规则编号": rule.get("规则编号"),
            "协议类型": rule.get("协议类型"),
            "raw_hex": normalized,
            "topic": topic,
            "confirmed": confirmed,
            "confirmed_type": confirmed["data_type"],
            "confirmed_value": confirmed["value"],
            "confirmed_version": confirmed_version,
            "current_version": current_version,
            "current_interpretation": current_interpretation,
            "available_interpretations": interpretations,
            "conflict": conflict,
            "conflict_reason": (
                f"解码规则在确认期间从 {confirmed_version} 变为 {current_version}"
                if conflict else ""
            ),
            "status": "版本冲突" if conflict else "待送测",
            "issues": [],
            "route_receipt": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        rows.append(entry)
        message = (
            f"规则已变化，保留本次申请（确认 {confirmed['label']}），"
            f"当前规则解释 {current_interpretation['label'] if current_interpretation else '不可用'}"
            if conflict else f"送测凭据已生成：{confirmed['label']}"
        )
        return entry, message

    def list_submissions(self) -> list[dict[str, Any]]:
        return list(reversed(store.records(SUBMISSION)))

    def get_submission(self, credential: str) -> dict[str, Any] | None:
        return self._find_submission(credential)

    # ==================== 送测执行与路由回执 ====================

    def _find_receipt(self, credential: str) -> dict[str, Any] | None:
        for row in store.records(ROUTE_RECEIPT):
            if row.get("凭据编号") == credential:
                return row
        return None

    def list_receipts(self) -> list[dict[str, Any]]:
        return list(reversed(store.records(ROUTE_RECEIPT)))

    def get_receipt(self, credential: str) -> dict[str, Any] | None:
        return self._find_receipt(credential)

    def send_submission(
        self, credential: str, data_type: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """把凭据里的合法解析结果交给路由服务，只产生一笔路由流水。

        重复送测同一张凭据直接返回已有回执，不再新增流水。
        """
        submission = self._find_submission(credential)
        if submission is None:
            return None, f"送测凭据 {credential} 不存在"

        existing = self._find_receipt(credential)
        if existing is not None:
            if data_type and _normalize_data_type(data_type) != existing.get("sent_type"):
                return None, (
                    f"该凭据已按 {existing.get('sent_type')}"
                    f"({existing.get('sent_value_text')}) 送测，不能改发 "
                    f"{_normalize_data_type(data_type)}；请重新预览并确认后生成新凭据"
                )
            return existing, f"该凭据已有路由流水 {existing.get('流水号')}，未新增"
        if submission.get("status") == "已送测":
            return None, "凭据状态异常：已标记送测但没有回执"

        rule = self.rule_snapshot(str(submission.get("规则编号")))
        if rule is None:
            return None, f"解码规则 {submission.get('规则编号')} 已不存在，无法送测"

        from app.services.msg_router import MsgRouterService

        confirmed = dict(submission.get("confirmed") or {})
        if data_type:
            new_type = _normalize_data_type(data_type)
            if new_type == "hex":
                return None, "送测不允许发送十六进制文本"
            interpretations, error = self.interpretations(rule, submission.get("raw_hex", ""))
            if not interpretations:
                return None, error
            chosen = next(
                (item for item in interpretations if item["data_type"] == new_type), None)
            if chosen is None:
                return None, f"字段字节无法解释为 {new_type}"
            # 以操作人这次重新确认的解释为准，即使当前规则给出的是另一种类型。
            confirmed = chosen
            update = {
                "confirmed": chosen,
                "confirmed_type": chosen["data_type"],
                "confirmed_value": chosen["value"],
                "confirmed_version": self.rule_version(rule),
                "current_version": self.rule_version(rule),
                "current_interpretation": next(
                    (item for item in interpretations
                     if item["data_type"] == _normalize_data_type(rule.get("数据类型"))), None),
                "available_interpretations": interpretations,
                "conflict": False,
                "conflict_reason": "",
                "status": "待送测",
                "updated_at": _now(),
            }
        else:
            update = {}
            current, _ = self.interpretations(rule, submission.get("raw_hex", ""))
            reusable = any(
                item["data_type"] == confirmed.get("data_type")
                and item["field_hex"] == confirmed.get("field_hex")
                and item["field_offset"] == confirmed.get("field_offset")
                and item["field_length"] == confirmed.get("field_length")
                for item in current
            )
            if not reusable:
                reason = "解码规则的偏移或长度已变化，已确认的字段字节无法复用，请重新预览并确认"
                submission["status"] = "待送测"
                submission["issues"] = [reason]
                submission["updated_at"] = _now()
                return None, reason

        topic = str(submission.get("topic") or "")
        payload = {
            "value": confirmed["value"],
            "data_type": confirmed["data_type"],
            "byte_order": confirmed["byte_order"],
            "value_text": confirmed["value_text"],
            "decode_rule": submission.get("规则编号"),
            "decode_version": submission.get("confirmed_version"),
            "credential": credential,
        }
        route = MsgRouterService().route_decoded(topic, payload)
        if not route.get("ok"):
            # 校验失败：报文留在原页可继续核对，申请状态回退成「待送测」，
            # 修正 Topic 或规则后可以用同一张凭据重试。
            submission["status"] = "待送测"
            submission["issues"] = [route.get("message", "路由失败")]
            submission["updated_at"] = _now()
            return None, route.get("message", "路由失败")

        # 只有真正路由成功后才把重新确认的解释落到凭据上。
        if update:
            submission.update(update)

        chosen_plan = route["chosen"]
        rows = store.records(ROUTE_RECEIPT)
        receipt = {
            "id": _next_id(rows),
            "回执编号": f"RCPT-{len(rows) + 1:04d}",
            "流水号": f"ROUTE-{len(rows) + 1:04d}",
            "凭据编号": credential,
            "decode_rule": submission.get("规则编号"),
            "decode_version": submission.get("confirmed_version"),
            "raw_hex": submission.get("raw_hex"),
            "source_topic": topic,
            "target_topic": chosen_plan.get("目标Topic"),
            "router_rule": chosen_plan.get("规则编号"),
            "forward_method": chosen_plan.get("转发方式"),
            "sent_type": confirmed["data_type"],
            "sent_value": confirmed["value"],
            "sent_value_text": confirmed["value_text"],
            "sent_value_type": confirmed["value_type"],
            "sent_byte_order": confirmed["byte_order"],
            "sent_field_hex": confirmed["field_hex"],
            "payload_kind": "number",
            "contains_hex_text": False,
            "sent_as_hex_text": False,
            "server_receipt": True,
            "route_count": 1,
            "routed_at": _now(),
        }
        rows.append(receipt)
        submission["route_receipt"] = receipt
        submission["status"] = "已送测"
        submission["issues"] = []
        submission["updated_at"] = _now()
        return receipt, f"已按确认结果发送 {confirmed['label']}，流水 {receipt['流水号']}"

    def detect_protocol(self, raw_hex: str) -> dict[str, Any]:
        """自动检测报文协议类型：尝试所有已启用的解码规则。

        返回匹配结果和建议协议类型。
        """
        rows = store.rows(self.MODULE)
        enabled = [row for row in rows if row.get("status") == "已启用"]

        results = []
        raw_bytes = raw_hex.replace(" ", "")
        if len(raw_bytes) % 2 != 0:
            return {"ok": False, "message": "报文hex长度必须为偶数"}

        binary_data = bytes.fromhex(raw_bytes)

        for rule in enabled:
            identifier = rule.get("报文标识", "")
            if identifier:
                ident_bytes = identifier.encode()
                match = ident_bytes in binary_data
            else:
                match = True  # 无标识的规则默认匹配

            results.append({
                "规则编号": rule.get("规则编号"),
                "协议类型": rule.get("协议类型"),
                "报文标识": identifier,
                "匹配": match,
            })

        matched = [r for r in results if r["匹配"]]
        return {
            "ok": True,
            "total_rules_checked": len(results),
            "matched_count": len(matched),
            "suggested_protocol": matched[0]["协议类型"] if matched else "未知",
            "details": results,
        }