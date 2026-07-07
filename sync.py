#!/usr/bin/env python3
"""
金山文档 → JSON 同步脚本
由 GitHub Actions 每小时自动运行
"""
import json
import os
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests

# ========== 配置 ==========
CLIENT_ID = os.environ.get("KDOCS_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("KDOCS_CLIENT_SECRET", "")
FILE_TOKEN = os.environ.get("KDOCS_FILE_TOKEN", "cahOpefqdPr3")
OUTPUT = "data.json"

if not CLIENT_ID or not CLIENT_SECRET:
    print("⚠️ 未设置 KDOCS_CLIENT_ID / KDOCS_CLIENT_SECRET，跳过同步")
    print("   请在 GitHub Secrets 中配置")
    sys.exit(0)

# ========== KDocs 认证 ==========
def get_token():
    resp = requests.post(
        "https://account.kdocs.cn/api/v3/oauth/access_token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"✅ Token 获取成功 (expires_in: {data.get('expires_in')}s)")
    return data["access_token"]


# ========== 获取文档信息 ==========
def get_file_info(token):
    # 通过分享链接获取文件信息
    resp = requests.get(
        f"https://drive.kdocs.cn/api/v6/links/{FILE_TOKEN}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    file_id = data.get("id") or data.get("file_id")
    file_type = data.get("type", "")
    print(f"📄 文件: {data.get('name', '未知')} (type: {file_type}, id: {file_id})")
    return file_id, file_type


# ========== 获取表格数据 ==========
def get_sheet_data(token, file_id, file_type):
    """获取所有 Sheet 数据"""
    sheets_data = {}

    # 获取 sheet 列表
    if file_type in ("sheet", "et"):
        base_url = f"https://drive.kdocs.cn/api/v3/et/files/{file_id}"
    else:
        base_url = f"https://drive.kdocs.cn/api/v3/ksheet/files/{file_id}"

    resp = requests.get(
        f"{base_url}/sheets",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    sheets = resp.json().get("sheets", [])
    print(f"📊 共 {len(sheets)} 个 Sheet")

    for sheet in sheets:
        sid = sheet.get("sheetId") or sheet.get("id")
        name = sheet.get("name", f"Sheet{sid}")
        print(f"   读取: {name} (id: {sid})")

        resp = requests.get(
            f"{base_url}/sheets/{sid}/values/A1:Z100",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        sheets_data[name] = resp.json()

    return sheets_data


# ========== 数据解析 ==========
PRIORITY_MAP = {"高": "high", "中": "mid", "低": "low"}

def parse_bool(v):
    if v is None: return False
    if isinstance(v, (int, float)): return v == 1
    return str(v).strip() in ("1", "☑", "true", "是")

def parse_date(v):
    if v is None: return ""
    if isinstance(v, str) and len(v) >= 10: return v[:10].replace("-", "/")
    return str(v).strip()

def extract_brand(name):
    for b in ["小米", "美的", "容声", "云鲸", "易来", "杜亚", "红米"]:
        if b in name: return b
    return ""

def safe_float(v, default=0):
    if v is None: return default
    try: return float(v)
    except: return default

def safe_int(v, default=1):
    if v is None: return default
    try: return int(v)
    except: return default


def parse_products(raw):
    if not raw or "values" not in raw: return []
    rows = raw["values"]
    items = []
    for i in range(2, len(rows)):
        row = rows[i]
        if not row or not row[0]: continue
        try: idx = int(float(row[0]))
        except: continue
        if idx < 1 or idx > 999: continue
        name = str(row[2] or "").strip()
        items.append({
            "id": idx, "category": str(row[1] or "").strip(), "name": name,
            "brand": extract_brand(name), "qty": safe_int(row[3], 1),
            "originalPrice": safe_float(row[4]), "targetPrice": safe_float(row[5]),
            "actualPrice": safe_float(row[6]) if row[6] else None,
            "priority": PRIORITY_MAP.get(str(row[10] or "中").strip(), "mid"),
            "platform": str(row[11] or "").strip(), "purchased": parse_bool(row[8]),
            "received": parse_bool(row[9]), "link": str(row[12] or "").strip(),
            "note": str(row[13] or "").strip(), "source": str(row[14] or "").strip(),
            "lastUpdate": parse_date(row[15]),
        })
    return items


def parse_pending(raw):
    if not raw or "values" not in raw: return []
    rows = raw["values"]
    icons = {"智能灯": "💡", "筒灯": "🔆", "智能面板": "🎛️"}
    items = []
    for i in range(2, len(rows)):
        row = rows[i]
        if not row or not row[0]: continue
        try: idx = int(float(row[0]))
        except: continue
        cat = str(row[1] or "").strip()
        if not cat: continue
        items.append({
            "id": idx, "icon": icons.get(cat, "📦"), "category": cat,
            "description": str(row[2] or "").strip(),
            "budget": str(row[3] or "待定").strip(),
            "candidates": str(row[5] or "").strip(),
        })
    return items


def parse_timeline(raw):
    if not raw or "values" not in raw: return {}
    rows = raw["values"]
    tl, cur = {}, ""
    for i in range(2, len(rows)):
        row = rows[i]
        if not row or (not row[0] and not row[1]): continue
        g = str(row[0] or "").strip()
        if g and g != cur: cur = g; tl[cur] = []
        if row[1] and isinstance(row[1], str) and row[1].strip():
            tl.setdefault(cur, []).append({
                "name": row[1].strip(), "note": str(row[4] or "").strip(),
                "done": parse_bool(row[3]),
            })
    return tl


def parse_prices(raw):
    if not raw or "values" not in raw: return []
    rows = raw["values"]
    items = []
    for i in range(2, len(rows)):
        row = rows[i]
        if not row or not row[0]: continue
        if not row[3] or not isinstance(row[3], (int, float)): continue
        items.append({
            "date": parse_date(row[0]), "category": str(row[1] or "").strip(),
            "model": str(row[2] or "").strip(), "price": safe_float(row[3]),
            "source": str(row[4] or "").strip(), "note": str(row[5] or "").strip(),
        })
    return items


def parse_suggestions(raw):
    if not raw or "values" not in raw: return []
    tags = {"价格优化":"price","套装优惠":"bundle","物流规划":"logistics",
            "品牌生态":"eco","售后保障":"warranty","功能拓展":"feature"}
    rows = raw["values"]
    items = []
    for i in range(2, len(rows)):
        row = rows[i]
        if not row or not row[0]: continue
        label = str(row[0] or "").strip()
        if label not in tags: continue
        text = str(row[1] or "").strip()
        if not text: continue
        items.append({"tag": tags[label], "tagLabel": label, "text": text})
    return items


# ========== 主流程 ==========
def main():
    print("🚀 开始同步...")
    print(f"   文档: https://www.kdocs.cn/l/{FILE_TOKEN}")
    print()

    # 1. 认证
    token = get_token()

    # 2. 获取文件信息
    file_id, file_type = get_file_info(token)

    # 3. 获取所有 Sheet 数据
    sheets = get_sheet_data(token, file_id, file_type)

    # 4. 解析数据
    sheet_names = list(sheets.keys())
    products = parse_products(sheets.get(sheet_names[0], {})) if len(sheet_names) > 0 else []
    pending = parse_pending(sheets.get(sheet_names[1], {})) if len(sheet_names) > 1 else []
    prices = parse_prices(sheets.get(sheet_names[2], {})) if len(sheet_names) > 2 else []
    timeline = parse_timeline(sheets.get(sheet_names[3], {})) if len(sheet_names) > 3 else {}
    suggestions = parse_suggestions(sheets.get(sheet_names[4], {})) if len(sheet_names) > 4 else []

    # 5. 计算统计
    orig = sum(p["originalPrice"] for p in products)
    tgt = sum(p["targetPrice"] for p in products)

    data = {
        "meta": {
            "title": "家具采购清单", "subtitle": "智能管理 · 实时追踪 · 精明采购",
            "lastUpdate": datetime.utcnow().strftime("%Y-%m-%d %H:%M") + " UTC",
            "version": int(time.time()),
            "source": "kdocs",
            "docUrl": f"https://www.kdocs.cn/l/{FILE_TOKEN}",
        },
        "stats": {
            "totalItems": len(products), "originalTotal": orig, "targetTotal": tgt,
            "savedTotal": orig - tgt, "actualTotal": 0,
            "orderedCount": sum(1 for p in products if p["purchased"]),
            "receivedCount": sum(1 for p in products if p["received"]),
            "pendingCount": len(pending),
        },
        "products": products, "pendingItems": pending,
        "timeline": timeline, "priceHistory": prices, "suggestions": suggestions,
    }

    # 6. 写入 JSON
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 同步完成!")
    print(f"   商品: {len(products)} 件")
    print(f"   到手价合计: ¥{tgt:,.0f}")
    print(f"   输出: {OUTPUT}")


if __name__ == "__main__":
    main()
