# order-center

独立订单中心：领星 ZIP/Excel/JSON 解析、刀锋鞋订单标准化、买家定制附件、素材平台契约匹配、人工审核和销量归因。

- 后端：FastAPI + SQLAlchemy + Alembic + MySQL，默认端口 `8010`
- 前端：React + Vite，订单页 `/orders`
- 素材数据：只通过 `MaterialPlatformClient` 读取，不包含素材域 ORM/表

详细边界、启动、契约和测试说明见 [backend/README.md](backend/README.md)。
