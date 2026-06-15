from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from urllib import request as urlrequest

from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest

logger = logging.getLogger("budmon.sms")


def _mask_phone(phone: str) -> str:
    if len(phone) <= 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


def _template(sms_settings: dict, event_type: str) -> dict:
    templates = sms_settings.get("templates", {})
    template = templates.get(event_type, {})
    if not template.get("code"):
        raise RuntimeError("短信模板未配置")
    return template


def send_sms(sms_settings: dict, phone: str, event_type: str, params: dict[str, str | int]) -> None:
    provider = sms_settings.get("provider", "aliyun")
    template = _template(sms_settings, event_type)
    logger.info(
        "准备发送短信 provider=%s event=%s phone=%s template=%s params=%s",
        provider,
        event_type,
        _mask_phone(phone),
        template.get("code"),
        params,
    )
    if provider == "aliyun":
        send_aliyun_sms(sms_settings.get("aliyun") or sms_settings.get("config", {}), template, phone, params)
        logger.info("阿里云短信发送完成 phone=%s event=%s", _mask_phone(phone), event_type)
        return
    if provider == "tencent":
        send_tencent_sms(sms_settings.get("tencent", {}), template, phone, params)
        logger.info("腾讯云短信发送完成 phone=%s event=%s", _mask_phone(phone), event_type)
        return
    raise RuntimeError("不支持的短信渠道")


def send_aliyun_sms(config: dict, template: dict, phone: str, params: dict[str, str | int]) -> None:
    access_key = config.get("accessKeyId", "")
    access_secret = config.get("accessKeySecret", "")
    region_id = config.get("regionId", "cn-hangzhou")
    sign_name = config.get("signName", "")
    template_code = template.get("code", "")
    if not all([access_key, access_secret, region_id, sign_name, template_code]):
        raise RuntimeError("阿里云短信配置不完整")

    client = AcsClient(access_key, access_secret, region_id)
    request = CommonRequest()
    request.set_accept_format("json")
    request.set_domain("dysmsapi.aliyuncs.com")
    request.set_method("POST")
    request.set_protocol_type("https")
    request.set_version("2017-05-25")
    request.set_action_name("SendSms")
    request.add_query_param("RegionId", region_id)
    request.add_query_param("PhoneNumbers", phone)
    request.add_query_param("SignName", sign_name)
    request.add_query_param("TemplateCode", template_code)
    request.add_query_param("TemplateParam", json.dumps(params, ensure_ascii=False))
    response = client.do_action_with_exception(request)
    parsed = json.loads(response.decode("utf-8"))
    logger.info(
        "阿里云短信响应 phone=%s code=%s message=%s requestId=%s",
        _mask_phone(phone),
        parsed.get("Code"),
        parsed.get("Message"),
        parsed.get("RequestId"),
    )
    if parsed.get("Code") != "OK":
        raise RuntimeError(parsed.get("Message", "短信发送失败"))


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def send_tencent_sms(config: dict, template: dict, phone: str, params: dict[str, str | int]) -> None:
    secret_id = config.get("secretId", "")
    secret_key = config.get("secretKey", "")
    region = config.get("region", "ap-guangzhou")
    app_id = config.get("smsSdkAppId", "")
    sign_name = config.get("signName", "")
    template_id = template.get("code", "")
    if not all([secret_id, secret_key, region, app_id, sign_name, template_id]):
        raise RuntimeError("腾讯云短信配置不完整")

    ordered_values = [str(params.get(name, "")) for name in template.get("params", [])]
    phone_number = phone if phone.startswith("+") else f"+86{phone}"
    payload = {
        "PhoneNumberSet": [phone_number],
        "SmsSdkAppId": app_id,
        "SignName": sign_name,
        "TemplateId": template_id,
        "TemplateParamSet": ordered_values,
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    service = "sms"
    host = "sms.tencentcloudapi.com"
    action = "SendSms"
    version = "2021-01-11"
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")

    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_request_payload = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = "\n".join(
        ["POST", "/", "", canonical_headers, signed_headers, hashed_request_payload]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join([algorithm, str(timestamp), credential_scope, hashed_canonical_request])
    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _sign(secret_date, service)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    req = urlrequest.Request(
        f"https://{host}",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": version,
            "X-TC-Region": region,
        },
    )
    with urlrequest.urlopen(req, timeout=15) as resp:
        parsed = json.loads(resp.read().decode("utf-8"))
    response = parsed.get("Response", {})
    logger.info(
        "腾讯云短信响应 phone=%s requestId=%s error=%s",
        _mask_phone(phone),
        response.get("RequestId"),
        response.get("Error"),
    )
    if response.get("Error"):
        raise RuntimeError(response["Error"].get("Message", "腾讯云短信发送失败"))
