# PH-022 真实派遣与边界

批准BASE：de82006（PH023/024/025均已合入）。按PH023视觉系统先行；无新增依赖、迁移、记账服务或API。

- Kimi 66b2ca88：project_detail、cost partial、app.css/app.js；02c6c2f7定点修无JS自然键盘、图表局部滚动和项目字号。均已真实completed。
- DSH f061fcac写21项新反例；初草稿把rows.actual_cents误当累计成本且算错日期间距，由主控明确预算余额语义，f97979b5修正后真实RED（缺core.charts）。
- DSH 3f8f742c只写core.reporting/core.charts，真实completed；主控仅接core.views授权后调用和linked/history查询，未并发写共享文件。
- 主控无凭据禁网隔离实际21项PASS（0.914s）。独立GLM最终快照审阅与完整验收另记最终报告，不把本文件当PASS证明。

接口：project_cost_burndown返回available/reason/as_of/budget_cents/actual_cost_cents/remaining_cents/note/rows/chart；rows.actual_cents=当前批准预算-该日累计确认成本，未来None，plan_cents参考余额。top.actual_cost_cents是累计确认成本，两者不混淆。core.charts.line_chart只产Decimal坐标，全部SVG/样式由Kimi模板渲染。无财务权不调用且context为None。
