# GitHub Pages 静态版股息率计算器

纯前端实现的 A 股真实股息率 + 市赚率计算器，部署于 GitHub Pages，无后端。

## 架构

- **数据源**（全部支持浏览器 CORS）：
  - 腾讯行情 `qt.gtimg.cn` — 价格 / 总股本 / PE-TTM / PB（GBK 编码）
  - 腾讯 K 线 `web.ifzq.gtimg.cn` — 月度前复权走势
  - 东方财富 `datacenter-web.eastmoney.com` — 分红明细（RPT_SHAREBONUS_DET）、财务（RPT_F10_FINANCE_MAINFINADATA）、行业（RPT_F10_BASIC_ORGINFO）
  - 腾讯 `smartbox.gtimg.cn` — 股票名称→代码（script 标签 JSONP）
- **计算逻辑**：`js/calculator.js` 纯函数，与 `src/` 下 Python 实现逐字段对齐（见下方验证）
- **部署**：`.github/workflows/pages.yml` 推送 main 时自动部署到 Pages

## 目录

- `index.html` — 页面（单一页面来源，本地 `src/web.py` 与 GitHub Pages 共用本目录）
- `js/calculator.js` — 纯计算逻辑（Node/browser 双端可用）
- `js/datasources.js` — 浏览器数据获取层
- `js/app.js` — 综合分析编排
- `js/verify.js` / `js/verify_raw.js` — Node 验证脚本
- `js/calculator.test.js` — 纯函数单元测试

## 本地运行

```bash
python3 -m http.server 8000 --directory site
# 打开 http://localhost:8000
```

## 单元测试

```bash
node --test site/js/calculator.test.js
```

## 与 Python 实现的一致性验证

```bash
python scripts/verify_js_vs_python.py 600900 600987 600919 600887 600019
```

脚本让 JS 与 Python 消费**相同原始数据**（同一批腾讯/东财接口），逐字段对比计算逻辑。
