# 前端组件契约 v2.0｜04 布局 × 01 蓝色配色

> 本文件是**设计期组件接口建议**，并非对现有仓库代码的假定。先检查已使用的 React/Vue/Ant Design/Element Plus/自研组件，能复用则复用。所有数据均由真实业务模型映射到 ViewModel，不改写原有服务端字段。

## 0. 组件设计质量门槛

1. **功能优先**：视觉改造不得移除旧系统具有的筛选、排序、分页、附件、审批、导出、权限提示、业务校验或导航能力。
2. **受控状态**：调用方传入数据和加载/错误状态；UI 组件不直接访问 API、不自行合成假值、不暗改账务状态。
3. **可读语义**：用标签+数值+单位+说明表示业务含义；不能仅以颜色表达。
4. **无障碍**：真实 `<button>` / `<a>` / `<label>`；可见焦点；弹窗管理焦点；图表至少有替代表格或文字摘要。
5. **行为透明**：失败有恢复路径、加载禁重复、无权限解释清楚；未经支持不显示“已保存/已导出”成功提示。
6. **复杂度控制**：不为一个页面再建一套布局框架；共享 token，不为不同业务页复制近似相同样式。

## 1. 系统组件

| 组件 | 最小属性 / 行为 | 必须覆盖的状态 | 设计尺寸与规则 |
|---|---|---|---|
| `AppShell` | `navigation`, `currentRoute`, `user`, `children`, `permissions` | 小桌面折叠、手机抽屉、无权限导航 | 宽侧栏 232px、窄侧栏 72px、顶栏 68px（移动 59px） |
| `SideNav` | 菜单组、路由、active、可见性 | 默认、悬停、激活、折叠、键盘导航 | 激活淡蓝底蓝字；一次只高亮实际当前分组位置 |
| `PageHeader` | `title`, `subtitle`, `breadcrumb`, `actions` | 标题过长、无 actions、窄屏换行 | H1 24/32；区域内最多一个主要 CTA |
| `SurfaceCard` | `title`, `subtitle`, `toolbar`, `children` | skeleton、empty、error | 14px 圆角、1px 边框、16px 卡距 |
| `FeedbackRegion` | `kind`, `message`, `retry`, `description` | loading、empty、error、unauthorized、success | error 给重试/解决方案，不制造假成功 |
| `ResponsiveDrawer` | `open`, `onClose`, `title`, `children`, `returnFocus` | 加载、滚动、Esc、背景关闭、焦点恢复 | overlay 遮挡背景，Trap 焦点；移动全宽 |

## 2. 数据展示组件

| 组件 | 展示契约 | 重要约束 |
|---|---|---|
| `KpiCard` | `label`, `formattedValue`, `unit`, `trend`, `period`, `status`, `loading`, `onOpenDetail` | `null`/缺测显示 `—`，不可替换成 0；趋势有好坏语义而非只看正负 |
| `StatusTag` | `text`, `tone`, `accessibleLabel` | 仅映射真实状态枚举；项目、付款、审批三套状态不能混同 |
| `DataTable` | `columns`, `rows`, `rowKey`, `pagination`, `sort`, `filters`, `loading`, `empty`, `selection`, `actions` | 金额右对齐；响应式列裁剪须不丢关键操作；数据域和总数来自 API |
| `ChartContainer` | `title`, `metricUnit`, `data`, `series`, `range`, `description`, `loading`, `empty`, `error` | 时间/单位/口径必填；缺值不得插值造数；tooltip 需要明确比较基准 |
| `FilterBar` | 受控 `filters`, `onChange`, `onReset`, `onSubmit?` | 不擅自触发大量请求，尊重原系统筛选逻辑；重置应清除全部有关字段 |
| `MoneyText` | 数值、币种、精度、隐私标记 | 货币格式由系统口径决定；金额零/空/负不可混淆，机密信息按权限处理 |
| `TimeEntryGrid` | 周期、项目/任务行、日列、值、权限、验证、草稿与提交操作 | 单元格输入可键盘操作；审批/成本映射交给业务层；移动可横向滚动 |
| `ApprovalPanel` | 申请详情、关联单据、权限、进度、操作日志、审批回调 | 同意/驳回均确认服务端结果；驳回意见要求按真实规则；冲突提示 |

## 3. ViewModel（示意，不等于 API/DB 契约）

以下 TypeScript 仅供 Agent 进行“视图层适配”，务必在实际仓库里与业务类型进行**字段映射**：

```ts
interface MetricVM {
  id: string;
  label: string;
  formattedValue: string | null;  // null 表示不可计算/暂无权限，显示 —
  unit?: string;
  periodLabel: string;             // 示例：“2026 年 10 月”，不可缺省而误导
  deltaLabel?: string;
  favorable?: boolean | null;      // 上升不一定有利：成本增加通常不是绿色
  description?: string;            // 数据来源/口径（需要时可展开）
}

interface MoneyDisplayVM {
  amountMinor?: string | null;    // 原系统如以分存储，原样保留为 string / bigint 适配
  formatted: string;              // 由既有财务格式化函数生成
  currency: string;
  unitLabel?: string;
  asOf?: string;
  sourceLabel?: string;
}

type AsyncVM<T> =
  | {kind: 'loading'}
  | {kind: 'empty'; reason: string; action?: {label: string; href?: string}}
  | {kind: 'error'; message: string; retryable: boolean}
  | {kind: 'unauthorized'; message: string}
  | {kind: 'ready'; data: T; updatedAt?: string};
```

**禁忌**：UI 侧使用浮点数直接求和生成真实财务报表；将 `undefined/null` 用 `0` 补齐；将资金收付用合同额推算；UI 自行解码权限并绕过后端授权。

## 4. 重要交互状态契约

| 情况 | 用户可见行为 | 必须验证 |
|---|---|---|
| 表单必填/跨字段非法 | 字段边框 + 错误文字 + 聚焦首个错误 | 不提交 API，原内容不丢失 |
| 保存请求中 | 按钮 loading，防双击，取消/离开提示遵循既有规则 | 无重复提交；网络超时可恢复 |
| 并发编辑冲突 | 保留输入，提示服务端版本冲突及下一步 | 不静默覆盖他人修改 |
| 表格为空 | 解释“当前筛选没有记录”与“租户尚无记录”的区别 | 根据权限显示创建/清除筛选 |
| 账户余额无权 | 不展示数字，也不显示隐含敏感趋势 | API 级校验通过 |
| 审批已处理 | 显示服务端最终状态和操作记录 | 重放点击不会重复处理 |
| 收付已登记 | 仅当 API 确认时显示成功 | 不在前端擅自更新余额汇总 |
| 财务数据不完整 | 显示无法计算 + 缺失理由 | 不使用截图 mock 假数 |
| 移动端密集表格 | 容器横向滚动，保持关键信息和操作可达 | 390px 页面本身无横向溢出 |

## 5. 核心设计覆盖范围

`default / hover / focus-visible / disabled / loading / error / success / empty / read-only / unauthorized`。

同一控件没有必要强制同时支持所有状态，例如只读 KPI 无 hover 操作。但凡可点、可改或异步请求的组件必须有对应状态。动效尽量在 120–240ms，并遵循 `prefers-reduced-motion`。

## 6. 参考实现

- 全局模块：`demo/index.html`、`screens/01-*` 至 `screens/10-*`
- 高风险操作：`demo/workflows.html`、`screens/11-flow-*` 至 `screens/15-flow-*`
- 颜色/间距：`design-tokens.json` 和 `tokens.css`

上述 HTML 可本地查看并验证 UI，**不是生产代码，不具备安全、事务、会计或真实审批能力**。
