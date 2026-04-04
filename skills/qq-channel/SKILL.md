---
name: openakita/skills@qq-channel
description: "QQ Channel (Tencent Channel) bot management skill. Manage channels, sub-channels, members, messages, announcements, and schedules via QQ Bot API. Use when user wants to operate QQ channels, send messages to channels, or manage channel members."
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# QQ 频道管理

通过 QQ 机器人 API 管理腾讯频道/QQ 频道的消息、成员和内容。

## 前置条件

- 在 QQ 机器人开放平台 https://bot.q.qq.com 注册并创建机器人
- 获取 AppID 和 Token
- 设置 QQBot 鉴权头信息

## 核心能力

| 功能 | 说明 |
|------|------|
| 频道管理 | 获取频道列表、频道详情 |
| 子频道管理 | 创建/修改/删除子频道 |
| 消息发送 | 发送文本/图片/Markdown 消息 |
| 成员管理 | 成员列表、身份组权限 |
| 公告管理 | 创建/删除公告 |
| 日程管理 | 创建/查询日程 |

## API 鉴权

使用 getAppAccessToken 获取 Token，请求头携带 Authorization: QQBot {token}。
