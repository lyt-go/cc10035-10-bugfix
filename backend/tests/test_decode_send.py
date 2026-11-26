"""报文解码送测链路测试：有符号/无符号解释、版本冲突、幂等送测与路由回执。

对应业务场景：小端报文 FEFFFFFF 预览必须是 -2；规则在另一窗口改成 uint32
之后，已确认的申请保留，界面能看出仍可用的合法解释；一份凭据最多一笔路由
流水，校验失败时申请留在原页；路由回执必须证明实际发送的是数值而不是hex文本。
"""
from __future__ import annotations

import pytest

from app.services.msg_decode import MsgDecodeService
from app.services.msg_router import MsgRouterService
from app.store import store

RULE = "MSG_-0003"
TOPIC = "iot/telemetry/temp"
RAW = "FEFFFFFF"


@pytest.fixture(autouse=True)
def clean_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def service():
    return MsgDecodeService()


def test_little_endian_int32_preview_is_negative_two(service):
    preview = service.preview(RULE, RAW)
    assert preview["ok"] is True
    assert preview["parsed_value"] == -2
    assert preview["value_text"] == "-2"
    assert preview["data_type"] == "int32"
    labels = {item["data_type"]: item["value"] for item in preview["interpretations"]}
    assert labels == {"int32": -2, "uint32": 4294967294}


def test_big_endian_and_little_endian_disagree_on_same_bytes(service):
    # FFFFFFFE：大端有符号 -2；小端有符号 -16777217；uint32 各自无符号解释。
    rule = service.rule_snapshot(RULE)
    assert rule is not None
    service.update_rule(RULE, {"字节序": "big"})
    big = service.preview(RULE, "FFFFFFFE")
    assert big["parsed_value"] == -2
    assert big["interpretations"][1]["value"] == 4294967294

    service.update_rule(RULE, {"字节序": "little"})
    little = service.preview(RULE, "FFFFFFFE")
    assert little["parsed_value"] == -16777217
    assert little["interpretations"][1]["value"] == 4278190079


def test_uint32_keeps_unsigned_meaning_after_rule_change(service):
    service.update_rule(RULE, {"数据类型": "uint32"})
    preview = service.preview(RULE, RAW)
    assert preview["parsed_value"] == 4294967294
    assert preview["value_text"] == "4294967294"
    # 同一段字节依旧能给出有符号解释，供操作人重新确认。
    int32 = next(i for i in preview["interpretations"] if i["data_type"] == "int32")
    assert int32["value"] == -2


def test_confirm_only_uses_operator_selected_interpretation(service):
    submission, message = service.confirm_submission(
        RULE, RAW, data_type="uint32", topic=TOPIC)
    assert submission is not None, message
    assert submission["confirmed_type"] == "uint32"
    assert submission["confirmed_value"] == 4294967294
    # 操作人没确认的类型不能顺手写进凭据。
    assert submission["current_interpretation"]["data_type"] == "int32"
    assert submission["current_interpretation"]["value"] == -2


def test_confirm_rejects_hex_payload(service):
    submission, message = service.confirm_submission(
        RULE, RAW, data_type="hex", topic=TOPIC)
    assert submission is None
    assert "十六进制" in message


def test_confirm_rejects_invalid_hex(service):
    submission, message = service.confirm_submission(RULE, "FEFFFFF", topic=TOPIC)
    assert submission is None
    assert "偶数" in message


def test_conflict_keeps_application_and_lists_legal_interpretations(service):
    original, _ = service.confirm_submission(RULE, RAW, data_type="int32", topic=TOPIC)
    assert original is not None
    version_before = original["confirmed_version"]

    # 另一窗口改成 uint32。
    _, message = service.update_rule(RULE, {"数据类型": "uint32"})
    assert "已更新" in message

    conflicted, message = service.confirm_submission(
        RULE, RAW, data_type="int32", topic=TOPIC, expected_version=version_before)
    assert conflicted is not None
    assert conflicted["凭据编号"] == original["凭据编号"]  # 申请保留
    assert conflicted["conflict"] is True
    assert conflicted["status"] == "版本冲突"
    assert conflicted["confirmed"]["value"] == -2
    assert conflicted["current_interpretation"]["value"] == 4294967294
    available = {item["label"] for item in conflicted["available_interpretations"]}
    assert available == {"int32(-2)", "uint32(4294967294)"}


def test_send_after_conflict_uses_confirmed_signed_value(service):
    version = service.preview(RULE, RAW)["rule_version"]
    submission, _ = service.confirm_submission(
        RULE, RAW, data_type="int32", topic=TOPIC, expected_version=version)
    service.update_rule(RULE, {"数据类型": "uint32"})
    service.confirm_submission(
        RULE, RAW, data_type="int32", topic=TOPIC, expected_version=version)

    receipt, message = service.send_submission(submission["凭据编号"])
    assert receipt is not None, message
    assert receipt["sent_type"] == "int32"
    assert receipt["sent_value"] == -2
    assert receipt["sent_value_text"] == "-2"
    assert receipt["contains_hex_text"] is False
    assert receipt["sent_as_hex_text"] is False
    assert receipt["payload_kind"] == "number"
    assert receipt["route_count"] == 1


def test_resend_same_credential_yields_single_route_record(service):
    submission, _ = service.confirm_submission(RULE, RAW, data_type="int32", topic=TOPIC)
    first, _ = service.send_submission(submission["凭据编号"])
    second, message = service.send_submission(submission["凭据编号"])
    assert first is not None and second is not None
    assert first["流水号"] == second["流水号"]
    assert "未新增" in message
    assert len(store.records("路由回执")) == 1
    assert len(service.list_receipts()) == 1


def test_reconfirm_same_credential_does_not_create_extra_application(service):
    first, _ = service.confirm_submission(RULE, RAW, data_type="int32", topic=TOPIC)
    again, _ = service.confirm_submission(RULE, RAW, data_type="int32", topic=TOPIC)
    assert first["凭据编号"] == again["凭据编号"]
    assert len(service.list_submissions()) == 1


def test_send_after_reselecting_uint32_sends_unsigned(service):
    submission, _ = service.confirm_submission(RULE, RAW, data_type="int32", topic=TOPIC)
    version = service.preview(RULE, RAW)["rule_version"]
    service.update_rule(RULE, {"数据类型": "uint32"})
    conflicted, _ = service.confirm_submission(
        RULE, RAW, data_type="int32", topic=TOPIC, expected_version=version)
    assert conflicted["conflict"] is True

    receipt, message = service.send_submission(conflicted["凭据编号"], data_type="uint32")
    assert receipt is not None, message
    assert receipt["sent_type"] == "uint32"
    assert receipt["sent_value"] == 4294967294
    assert receipt["contains_hex_text"] is False
    assert len(store.records("路由回执")) == 1


def test_validation_failure_keeps_application_for_retry(service):
    submission, _ = service.confirm_submission(
        RULE, RAW, data_type="int32", topic="no/such/topic")
    receipt, message = service.send_submission(submission["凭据编号"])
    assert receipt is None
    assert "无匹配路由规则" in message
    kept = service.get_submission(submission["凭据编号"])
    assert kept is not None
    assert kept["status"] == "待送测"
    assert kept["issues"]
    assert store.records("路由回执") == []

    # 修正 Topic 后同一张凭据可以成功送测。
    kept["topic"] = TOPIC
    receipt, message = service.send_submission(submission["凭据编号"])
    assert receipt is not None, message
    assert receipt["sent_value"] == -2


def test_send_requires_numeric_interpretation(service):
    submission, _ = service.confirm_submission(RULE, RAW, data_type="int32", topic=TOPIC)
    receipt, message = service.send_submission(submission["凭据编号"], data_type="hex")
    assert receipt is None
    assert "十六进制" in message
    assert store.records("路由回执") == []


def test_router_consumes_decoded_payload_not_hex_text(service):
    submission, _ = service.confirm_submission(RULE, RAW, data_type="int32", topic=TOPIC)
    receipt, _ = service.send_submission(submission["凭据编号"])
    routing = MsgRouterService().route_decoded(
        TOPIC, {"value": receipt["sent_value"], "data_type": "int32", "byte_order": "little"})
    assert routing["ok"] is True
    assert routing["payload"]["value"] == -2
    assert routing["payload"]["data_type"] == "int32"


def test_rule_version_changes_with_type_and_byte_order(service):
    v1 = service.preview(RULE, RAW)["rule_version"]
    service.update_rule(RULE, {"数据类型": "uint32"})
    v2 = service.preview(RULE, RAW)["rule_version"]
    service.update_rule(RULE, {"字节序": "big"})
    v3 = service.preview(RULE, RAW)["rule_version"]
    assert len({v1, v2, v3}) == 3


def test_operation_receipt_survives_repeated_reads(service):
    submission, _ = service.confirm_submission(RULE, RAW, data_type="int32", topic=TOPIC)
    receipt, _ = service.send_submission(submission["凭据编号"])
    for _ in range(3):
        again = service.get_receipt(submission["凭据编号"])
        assert again is not None
        assert again["流水号"] == receipt["流水号"]
        assert again["sent_value"] == -2
