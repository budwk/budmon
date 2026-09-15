# API 契约

根路径 `/api`，JSON 请求。除健康检查、初始化状态、初始化、注册、登录和刷新外，均需 `Authorization: Bearer <token>`。完整交互文档可访问后端 `/docs`（直接后端地址；生产代理可按需限制）。

| 接口 | 用途 |
| --- | --- |
| `POST /install` | 首次管理员初始化，`username/password` |
| `POST /auth/register` | 普通用户注册，`username/password` |
| `POST /auth/login` | 登录，返回 `token/refresh_token/expires_in` |
| `POST /auth/refresh` | `refresh_token`，一次性轮换并撤销旧会话 |
| `POST /auth/logout` | 撤销当前会话；客户端退出前调用 DELETE /devices |
| `POST /auth/reset-password` | `old_password/new_password`，撤销所有会话与设备 |
| `GET /me` | 用户角色、有效套餐、配额、通知偏好、APNs 配置状态 |
| `PUT /me/preferences` | `push_enabled: bool` |
| `DELETE /me` | 普通用户删除自身与关联监测数据；管理员禁止自删 |
| `GET /dashboard` | 当前用户 total/enabled/down 与最近 20 条日志 |
| `GET/POST /targets` | 目标列表/创建，`name/url/enabled` |
| `GET/PUT/DELETE /targets/{id}` | 目标详情/更新/删除，越权返回 404 |
| `GET /targets/{id}/logs?before=&limit=20&result=all` | 历史游标分页，默认 20 / 最多 100 条，按 id 倒序；result=abnormal 仅异常 |
| `POST /monitor/run` | 仅检查当前用户目标；每用户每分钟一次，正在检测返回 409 |
| `DELETE /monitor/logs` | 仅清空当前用户目标的日志 |
| `GET /notifications?before=&limit=50` | 自己的告警通知，最多 100 条 |
| `POST /notifications/{id}/read` | 标为已读 |
| `POST/DELETE /devices` | 绑定/解绑本机，`token/environment`，环境 sandbox 或 production |
| `GET /plans` | 套餐目录，当前 purchase_enabled=false |
| `GET /admin/users?offset=0&limit=100` | 管理员查看用户与配额，最大页长 200 |
| `PUT /admin/users/{id}` | 管理员更新权益，详见下例 |
| `GET /settings` | 管理员读取监测与通知渠道配置 |
| `PUT /settings/monitor` | 管理员设置间隔、重试、SSL 阈值与传统通知 |
| `PUT /settings/sms`、`PUT /settings/email` | 管理员渠道配置 |

权益更新请求（完整替换这些字段）：

```json
{
  "plan_id": "pro",
  "quota_override": null,
  "plan_expires_at": "2027-01-01T00:00:00+08:00",
  "disabled": false
}
```

`quota_override` 为 null 时使用有效套餐限制，为数字时使用管理员人工授权。到期不删除既有目标；新建始终在事务内校验数量。客户端不得直接访问或更新支付事件与审计表。

鉴权失败 401，角色不足/超过配额 403，账号重名/重复初始化/任务繁忙 409，格式错误 422，限流 429。注册、登录、刷新共享每来源地址每分钟 20 次限制；生产可信代理配置见部署指南。

APNs payload 包含 `aps.alert`、`sound`、`thread-id`、`target_id`、`notification_id`。点击后客户端通过当前用户鉴权重新读取目标，不信任 payload 作为访问授权。令牌注册到其他账号时取消旧账号排队消息；重复注册同一账号不会丢弃待发告警。
