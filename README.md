# IoT设备数据接入与协议适配平台

面向IoT设备注册、多协议适配、消息路由与时序数据归集的设备数据接入平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 设备注册 | `device_registry` | 设备档案 | 设备编号、设备类型、协议类型 |
| 协议配置 | `protocol_cfg` | 协议配置 | 配置编号、协议名称、传输方式 |
| 报文解码 | `msg_decode` | 解码规则 | 规则编号、协议类型、报文标识 |
| 消息路由 | `msg_router` | 路由规则 | 规则编号、匹配Topic、目标Topic |
| 时序测点 | `ts_point` | 测点定义 | 测点编号、设备编号、测点名称 |
| 时序存储 | `ts_store` | 存储策略 | 策略编号、测点编号、存储引擎 |
| 降采样 | `downsample` | 降采样任务 | 任务编号、源测点、目标粒度 |
| 告警触发 | `alarm_trigger` | 告警规则 | 规则编号、关联测点、告警条件 |
| 固件升级 | `firmware_ota` | 升级任务 | 任务编号、目标设备、固件版本 |
| 网关管理 | `gateway_mgmt` | 网关节点 | 网关编号、网关地址、挂载设备数 |
| 设备影子 | `shadow_dev` | 影子记录 | 记录编号、设备编号、期望状态 |
| 数据断点 | `data_gap` | 断点记录 | 记录编号、设备编号、测点编号 |
| 批量导出 | `batch_export` | 导出任务 | 任务编号、设备范围、测点列表 |
| 规则引擎 | `rule_engine` | 联动规则 | 规则编号、触发条件、执行动作 |
| 边缘缓存 | `edge_cache` | 缓存配置 | 配置编号、网关编号、缓存大小 |
| 标签绑定 | `tag_binding` | 标签关系 | 关系编号、设备编号、标签键 |
| 数据流管道 | `data_stream` | 流管道 | 管道编号、输入源、处理算子链 |
| 聚合分发 | `aggregate_sink` | 分发任务 | 任务编号、数据窗口、聚合函数 |

## 报文解码送测链路

报文解码页在规则表下方多了一块“送测确认”面板，消息路由页多了一块“路由详情”面板：

1. 选一条已启用的解码规则和 hex 报文，点“预览解析”。解析按规则里的
   字段偏移/长度/数据类型/字节序执行，**int32 按有符号解释**，同一段字节
   同时列出 `int32` / `uint32`（长度够时还有 `float64`）的合法解释。
   例如小端 `FEFFFFFF` 预览为 `int32(-2)`，`uint32` 解释为 `4294967294`。
2. 操作人选中一种解释后“确认”，生成送测凭据（`DEC-0001` 这样的编号）。
   只有确认过的解释能用于这次送测，`hex` 解释会被拒绝。
3. 若确认期间规则被另一个窗口改过（例如改成 `uint32`），确认时带上的
   规则版本对不上，界面保留当前申请与已确认值，同时显示冲突原因和
   “还能用哪份合法解释”，可以按新解释重新确认后再送测。
4. “送测”把凭据里的解析结果交给路由服务，服务端只消费数值载荷；
   同一份凭据重复送测只会有一笔路由流水。
5. 校验失败（例如 Topic 没有匹配路由）时报文留在原页，凭据状态回到
   “待送测”并给出原因，修正后可重试。
6. 路由详情页读取服务端回执，回执里的 `sent_type` / `sent_value` /
   `contains_hex_text` 用来证明实际发出的是 `int32(-2)` 或重新确认过的
   `uint32(4294967294)`，而不是十六进制文本，也不会出现两份流水。

相关接口都在 `/api/cross/flow2/*`：`preview`、`confirm`、`send`、
`submissions`、`receipts`、`rules/{rule_id}`。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 解码规则可用 `PATCH /api/msg_decode/{id}` 或 `PATCH /api/cross/flow2/rules/{规则编号}`
  修改解析参数，每次改动都会推进规则版本；送测凭据与路由回执存放在
  `store.records()` 的运行期流水里，不混进 18 个业务模块的清单。

领域：数据工程
子方向：数据采集、爬虫与RPA
