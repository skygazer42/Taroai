# Design Memory

## Brand Tone
- **Adjectives:** utilitarian(实用)、calm(克制)、trustworthy(可信)
- **Avoid:** 并列面板堆叠、同一信息多处重复(如旧版 Execution / Evidence / Timeline)、"--" 式空状态

## Layout & Spacing
- **Density:** 分层渐进 —— 默认只展示核心状态,细节按需展开(手风琴 / 内联展开)
- **偏好布局:** 叙事型时间线优于并列面板;阻塞项(审批)优先级最高,需要全局入口
- **Corner radius:** `--radius: 8px`(面板)、`--radius-small: 6px`(控件)、999px(chip / 进度条)
- **Shadows:** `--shadow-panel: 0 1px 2px rgb(17 16 15 / 5%)`,hover 提升可用 `0 3px 10px rgb(17 16 15 / 8%)`

## Typography
- **Font:** "Instrument Sans Variable", "Avenir Next", "Segoe UI", sans-serif
- **等宽:** 运行 ID、对象 ID、数值统一 `ui-monospace` + `tabular-nums`
- **小标签:** 10px 大写 + letter-spacing 0.8px(`.panel-kicker` 模式)

## Color
- **Primary:** `--signal: oklch(55% 0.2 260)`(进行中 / 主操作),`--signal-quiet` 做浅底
- **Semantic:** `--green #4f7a5e`(完成)、`--amber #9a6f2d`(待处理 / 审批)、`--red #a45248`(失败 / 危险操作)
- **状态色语义(本次确立):** 蓝 = 进行中,绿 = 完成,琥珀 = 需要用户处理,灰虚线 = 未开始 / 未使用
- **Neutral:** oklch 纸面色阶 `--paper` / `--paper-raised` / `--paper-inset`,边线 `--edge` / `--edge-strong`

## Interaction Patterns
- **进度:** 顶部粘性状态栏 + 4px 渐变进度条(green→signal)
- **阻塞项:** 状态栏琥珀 chip 全局入口,点击 `scrollIntoView` + 短暂高亮定位
- **审批:** 内联在事件流中就地 Approve / Reject,不弹窗
- **证据 / 详情:** chip + 内联展开(`aria-expanded`),不跳转
- **复杂管理界面:** 手风琴分组 + 二级工作台按需进入(Admin / Customer Success 模式)
- **危险操作:** 幽灵按钮 + 红色文字,置于末尾(如「取消运行」)
- **空状态:** 图标 + 一句解释 + (可选)行动按钮;禁止裸 "--" / "Waiting"

## Motion
- 微交互 150–200ms,`ease-out` 入场;live 状态可用 1.8s 缓脉冲
- 必须尊重 `prefers-reduced-motion`

## Accessibility Rules
- **Focus:** 全局 `:focus-visible` 2px `--clay` 外环 + 2px offset(已有,沿用)
- **Tabs:** `role="tablist"` + `aria-selected`;展开控件带 `aria-expanded`;进度条带 `role="progressbar"`
- **Live region:** 状态变化容器 `aria-live="polite"`

## Repo Conventions
- **技术栈:** 原生 JS(无框架、无打包),手写 CSS + oklch token,lucide SVG sprite(注意 ID 带 `lucide-` 前缀)
- **钩子:** JS 与测试依赖 `data-*` 属性和 `data-testid`,重构时必须保留
- **面板基类:** `.panel`(12px padding、1px `--edge` 边框、`--paper-raised` 底、10px gap)
- **已知级联陷阱:** `styles.css` 8300 行附近对 workbench 有 `display:none !important` 显隐级联(artifact-open / operations-open 两种 sidecar 模式),改动 operations 结构时需同步检查

---

*Updated by Design Lab (2026-07-26)*
