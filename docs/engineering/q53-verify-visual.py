#!/usr/bin/env python3
"""Q-53 浏览器视觉一致性检查（Chromium/Playwright，仓库内可复现版本）。

用法（仓库根目录，需先启动一个指向演示库的微版服务）：
    PMO_DB=runtime/demo.sqlite3 PMO_SCOPE=micro PMO_ENV=development DJANGO_DEBUG=1 \
        .venv/bin/python manage.py runserver 127.0.0.1:8765 --noreload &
    PH_BASE=http://127.0.0.1:8765 PH_PASSWORD=<演示库 owner 密码> \
        .venv/bin/python docs/engineering/q53-verify-visual.py

检查 22 条路由 × 5 个视口（1440/1024/768/390/320）的无横向滚动、console/page error、
5xx，以及 1024px 壳层几何（侧栏 240px、主区 x=240）。另加三个窄屏回归守卫：

1. `/due/` 390px 行卡末列必须保持单列，「查看来源」链接与单元格左缘对齐；
2. 500px 下把指标值替换为单笔上限 ¥10,000,000,000.00，文字不得越出指标卡；
3. 390px 下用模板同构的 `.expense-gap-table` 合成行验证末列「操作」不被挤出隐式窄列。

结果 JSON 写到临时目录（可用 PH_OUT 指定），不写入仓库工作树；退出码 0 表示全部通过。
"""
import json
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("PH_BASE", "http://127.0.0.1:8765")
USER = os.environ.get("PH_USER", "owner")
PASSWORD = os.environ.get("PH_PASSWORD", "")
OUT = os.environ.get("PH_OUT") or tempfile.mkdtemp(prefix="q53-visual-")
PAGES = [
    ("dashboard", "/"), ("dashboard-due", "/?tab=due"), ("dashboard-activity", "/?tab=activity"),
    ("projects", "/projects/"), ("project-detail", "/projects/1/"), ("project-edit", "/projects/1/edit/"),
    ("time", "/time/"), ("time-week", "/time/?tab=week"), ("expenses", "/expenses/"),
    ("cashflow", "/cashflow/"), ("cashflow-invoices", "/cashflow/?tab=invoices"),
    ("cashflow-plan", "/cashflow/?tab=plan"), ("cash", "/cash/"), ("billing", "/billing/"),
    ("settings", "/settings/"), ("settings-rates", "/settings/?tab=rates"),
    ("settings-audit", "/settings/?tab=audit"), ("status", "/status/"), ("audit", "/audit/"),
    ("due", "/due/"), ("exports", "/exports/"), ("setup", "/setup/"),
]
VIEWPORTS = [("1440", 1440, 900), ("1024", 1024, 800), ("768", 768, 900),
             ("390", 390, 844), ("320", 320, 640)]


def login(page):
    page.goto(BASE + "/login/", wait_until="networkidle")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


def main():
    results = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        errs, pageerrs, srv = [], [], []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: pageerrs.append(str(e)))
        pg.on("response", lambda r: srv.append(f"{r.status} {r.url}") if r.status >= 500 else None)
        login(pg)
        for vname, vw, vh in VIEWPORTS:
            pg.set_viewport_size({"width": vw, "height": vh})
            for key, path in PAGES:
                errs.clear(); pageerrs.clear(); srv.clear()
                r = pg.goto(BASE + path, wait_until="networkidle")
                pg.wait_for_timeout(80)
                m = pg.evaluate("""() => {
                  const d = document.documentElement;
                  const sb = document.querySelector('.desktop-sidebar');
                  const app = document.querySelector('.app-shell');
                  const sbsb = sb ? getComputedStyle(sb) : null;
                  return {
                    sw: d.scrollWidth, cw: d.clientWidth, bodySW: document.body.scrollWidth,
                    sidebarVisible: !!sb && sbsb.display !== 'none' && sb.getBoundingClientRect().width > 0,
                    sidebarW: sb ? Math.round(sb.getBoundingClientRect().width) : 0,
                    appX: app ? Math.round(app.getBoundingClientRect().x) : null,
                  };
                }""")
                checks = {
                    "status": r.status if r else 0,
                    "no_hscroll": m["sw"] <= m["cw"] + 1 and m["bodySW"] <= m["cw"] + 1,
                    "console_errors": len(errs), "page_errors": len(pageerrs), "server_5xx": len(srv),
                }
                if vname == "1024":
                    checks["shell_1024_ok"] = (
                        m["sidebarVisible"] and m["sidebarW"] == 240 and m["appX"] == 240)
                results.append({"viewport": vname, "page": key, "path": path, **checks})
                ok = (checks["status"] == 200 and checks["no_hscroll"]
                      and checks["console_errors"] == 0 and checks["page_errors"] == 0
                      and checks["server_5xx"] == 0)
                if vname == "1024":
                    ok = ok and checks.get("shell_1024_ok", False)
                if not ok:
                    print("FAIL", vname, key, checks)

        # 守卫 1：/due/ 390px 末列保持单列，链接与 td 左缘对齐（防隐式窄列）。
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.goto(BASE + "/due/", wait_until="networkidle")
        due = pg.evaluate("""() => {
          const t = document.querySelector('.q41-due-table');
          const tr = t && t.querySelector('tbody tr');
          if (!tr) return {skip: true};
          const last = tr.lastElementChild;
          const td = last.getBoundingClientRect();
          const link = last.querySelector('a,button');
          const lr = link ? link.getBoundingClientRect() : null;
          return {cols: getComputedStyle(last).gridTemplateColumns,
                  tdX: Math.round(td.x), linkX: lr ? Math.round(lr.x) : null};
        }""")
        if due.get("skip"):
            print("due last-cell check skipped (no due rows)")
        else:
            single = len(due["cols"].split()) == 1
            aligned = due["linkX"] is not None and abs(due["linkX"] - due["tdX"]) <= 5
            if not (single and aligned):
                print("FAIL due-last-cell", due)
            results.append({"viewport": "390", "page": "due-last-cell", "path": "/due/", "status": 200,
                            "no_hscroll": True, "console_errors": 0, "page_errors": 0, "server_5xx": 0,
                            "last_cell_single_column": single, "last_cell_aligned": aligned})

        # 守卫 2：500px 指标卡中的单笔上限金额不得越出卡片（按文本实际宽度判断）。
        pg.set_viewport_size({"width": 500, "height": 900})
        pg.goto(BASE + "/", wait_until="networkidle")
        metric = pg.evaluate("""() => {
          const strong = document.querySelector('.metric > strong');
          if (!strong) return {skip: true};
          strong.textContent = '¥ 10,000,000,000.00';
          const card = strong.closest('.metric');
          const cs = getComputedStyle(card);
          const cr = card.getBoundingClientRect();
          const innerRight = cr.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
          const range = document.createRange();
          range.selectNodeContents(strong);
          const tr = range.getBoundingClientRect();
          return {overflow: tr.right > innerRight + 1,
                  textRight: Math.round(tr.right), innerRight: Math.round(innerRight),
                  cols: getComputedStyle(document.querySelector('.metrics-grid'))
                          .gridTemplateColumns.split(' ').length};
        }""")
        if metric.get("skip"):
            print("metric overflow check skipped")
        else:
            if metric["overflow"]:
                print("FAIL metric-overflow-500", metric)
            results.append({"viewport": "500", "page": "metric-overflow", "path": "/", "status": 200,
                            "no_hscroll": True, "console_errors": 0, "page_errors": 0, "server_5xx": 0,
                            "metric_no_overflow": not metric["overflow"]})

        # 守卫 3：390px 合成 .expense-gap-table 行，末列「操作」不得被挤出隐式窄列。
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.goto(BASE + "/expenses/", wait_until="networkidle")
        expgap = pg.evaluate("""() => {
          const wrap = document.createElement('div');
          wrap.className = 'table-scroll';
          wrap.innerHTML = '<table class="expense-gap-table"><thead><tr><th>项目</th><th>类别</th>'
            + '<th>发生日期</th><th class="numeric">金额</th><th>操作</th></tr></thead><tbody><tr>'
            + '<td data-label="项目">PH-0001 · 合成项目</td><td data-label="类别">外包与采购</td>'
            + '<td data-label="发生日期">2026.11.09</td><td data-label="金额" class="numeric">¥ 1,000.00</td>'
            + '<td data-label="操作"><form class="inline-form"><button class="button primary" type="submit">'
            + '确认计入成本</button></form></td></tr></tbody></table>';
          document.querySelector('main').appendChild(wrap);
          const tr = wrap.querySelector('tbody tr');
          const last = tr.lastElementChild;
          const td = last.getBoundingClientRect();
          const btn = last.querySelector('button').getBoundingClientRect();
          const cols = getComputedStyle(last).gridTemplateColumns;
          wrap.remove();
          return {cols, single: cols.split(' ').length === 1,
                  tdX: Math.round(td.x), btnX: Math.round(btn.x)};
        }""")
        if not (expgap["single"] and abs(expgap["btnX"] - expgap["tdX"]) <= 5):
            print("FAIL expense-gap-last-cell", expgap)
        results.append({"viewport": "390", "page": "expense-gap-last-cell", "path": "/expenses/",
                        "status": 200, "no_hscroll": True, "console_errors": 0, "page_errors": 0,
                        "server_5xx": 0, "last_cell_single_column": expgap["single"],
                        "last_cell_aligned": abs(expgap["btnX"] - expgap["tdX"]) <= 5})
        br.close()

    passed = sum(1 for r in results if r["status"] == 200 and r["no_hscroll"]
                 and r["console_errors"] == 0 and r["page_errors"] == 0 and r["server_5xx"] == 0
                 and r.get("shell_1024_ok", True) and r.get("last_cell_single_column", True)
                 and r.get("last_cell_aligned", True) and r.get("metric_no_overflow", True))
    print(f"checks: {passed}/{len(results)} passed")
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "verify.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=1)
    print("results:", out_path)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
