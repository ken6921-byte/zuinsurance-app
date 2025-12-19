import os
import io
import re
import json
import time
import math
import sqlite3
import hashlib
import datetime
from typing import Dict, Any, List, Optional

import pandas as pd
import streamlit as st
from PIL import Image

# ==========
# 基本設定
# ==========
APP_TITLE = "專業保單管理系統（商用版）"
DB_PATH = os.getenv("DB_PATH", "insurance_app.db")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL_VISION = os.getenv("OPENAI_MODEL_VISION", "gpt-4.1-mini")  # 讀圖/結構化
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4.1-mini")      # 健檢摘要/說明

# 用量管控（可在 Secrets 覆蓋）
DAILY_IMAGE_LIMIT_PER_USER = int(os.getenv("DAILY_IMAGE_LIMIT_PER_USER", "30"))  # 每人每日讀圖上限
DAILY_TEXT_LIMIT_PER_USER = int(os.getenv("DAILY_TEXT_LIMIT_PER_USER", "80"))    # 每人每日文字請求上限

# 權限（Secrets 建議設定）
# ADMIN_PASSWORD = "xxxx"
# USER_PASSWORDS_JSON = '["pw1","pw2"]'  # 多位同仁共用密碼也可
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
USER_PASSWORDS_JSON = os.getenv("USER_PASSWORDS_JSON", "[]")

# ==========
# Streamlit 頁面
# ==========
st.set_page_config(page_title=APP_TITLE, page_icon="🛡️", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    .title-row { display:flex; align-items:center; gap:12px; }
    .badge { display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; border:1px solid #e6e6e6; }
    .ok { background:#e9f8ee; border-color:#bfe8c8; }
    .warn { background:#fff6e6; border-color:#ffe0a3; }
    .err { background:#ffecec; border-color:#ffb7b7; }
    .card { border:1px solid #ececec; border-radius:14px; padding:14px 16px; background:white; }
    .muted { color:#6b7280; font-size: 13px; }
    .small { font-size: 13px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
</style>
""", unsafe_allow_html=True)

# ==========
# DB
# ==========
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = db_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        id_no TEXT,
        birthday TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        policy_group_name TEXT,
        insurer TEXT,
        policy_no TEXT,
        pay_mode TEXT,
        effective_date TEXT,
        print_date TEXT,
        total_premium_year INTEGER DEFAULT 0,
        raw_json TEXT,                  -- AI 結構化原始 JSON
        health_report TEXT,             -- 健檢報告（Markdown）
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS policy_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        policy_id INTEGER NOT NULL,
        contract_type TEXT,         -- 主/附
        product_code TEXT,
        product_name TEXT,
        term TEXT,
        coverage_term TEXT,
        sum_insured TEXT,
        premium INTEGER DEFAULT 0,
        category TEXT,              -- 壽險/醫療/意外/癌症/重傷/長照/豁免/其他
        FOREIGN KEY(policy_id) REFERENCES policies(id) ON DELETE CASCADE
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS usage_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ymd TEXT NOT NULL,
        username TEXT NOT NULL,
        image_calls INTEGER NOT NULL DEFAULT 0,
        text_calls INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        UNIQUE(ymd, username)
    );
    """)

    conn.commit()
    conn.close()

init_db()

# ==========
# 安全/權限（極簡但可上線）
# ==========
def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def load_user_passwords() -> List[str]:
    try:
        arr = json.loads(USER_PASSWORDS_JSON)
        if isinstance(arr, list):
            return [str(x) for x in arr]
    except:
        pass
    return []

def login_ui():
    st.markdown(f"### 🛡️ {APP_TITLE}")
    st.markdown("<div class='muted'>請先登入後再使用（建議：同仁共用一組使用者密碼即可，管理者另有管理密碼）</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1])
    with c1:
        username = st.text_input("使用者名稱（可填你的名字/暱稱）", value=st.session_state.get("username", ""))
    with c2:
        password = st.text_input("密碼", type="password")

    user_pw_list = load_user_passwords()

    is_admin = False
    ok = False
    if st.button("登入", type="primary", use_container_width=True):
        if not username.strip():
            st.error("請輸入使用者名稱")
            st.stop()

        # 管理者密碼優先
        if ADMIN_PASSWORD and password == ADMIN_PASSWORD:
            ok = True
            is_admin = True
        elif user_pw_list and (password in user_pw_list):
            ok = True
            is_admin = False
        else:
            st.error("密碼不正確，請確認後再試")
            st.stop()

        st.session_state["authed"] = True
        st.session_state["username"] = username.strip()
        st.session_state["role"] = "admin" if is_admin else "user"

        # upsert user
        conn = db_conn()
        cur = conn.cursor()
        now = datetime.datetime.now().isoformat()
        cur.execute("INSERT OR IGNORE INTO users(username, role, created_at) VALUES(?,?,?)",
                    (st.session_state["username"], st.session_state["role"], now))
        cur.execute("UPDATE users SET role=? WHERE username=?",
                    (st.session_state["role"], st.session_state["username"]))
        conn.commit()
        conn.close()

        st.success("登入成功")
        time.sleep(0.6)
        st.rerun()

def require_auth():
    if not st.session_state.get("authed"):
        login_ui()
        st.stop()

require_auth()

USERNAME = st.session_state["username"]
ROLE = st.session_state["role"]

# ==========
# 用量限制
# ==========
def get_ymd():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def usage_get_or_create(username: str) -> Dict[str, int]:
    ymd = get_ymd()
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT image_calls, text_calls FROM usage_daily WHERE ymd=? AND username=?", (ymd, username))
    row = cur.fetchone()
    now = datetime.datetime.now().isoformat()
    if not row:
        cur.execute("INSERT OR IGNORE INTO usage_daily(ymd, username, image_calls, text_calls, updated_at) VALUES(?,?,?,?,?)",
                    (ymd, username, 0, 0, now))
        conn.commit()
        row = (0, 0)
    conn.close()
    return {"image_calls": int(row[0]), "text_calls": int(row[1])}

def usage_inc(username: str, image_inc=0, text_inc=0):
    ymd = get_ymd()
    conn = db_conn()
    cur = conn.cursor()
    now = datetime.datetime.now().isoformat()
    cur.execute("""
        INSERT INTO usage_daily(ymd, username, image_calls, text_calls, updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(ymd, username) DO UPDATE SET
            image_calls = image_calls + ?,
            text_calls = text_calls + ?,
            updated_at = ?
    """, (ymd, username, 0, 0, now, image_inc, text_inc, now))
    conn.commit()
    conn.close()

def enforce_limits(kind: str):
    u = usage_get_or_create(USERNAME)
    if kind == "image":
        if u["image_calls"] >= DAILY_IMAGE_LIMIT_PER_USER:
            st.error(f"今日 AI 讀圖已達上限（{DAILY_IMAGE_LIMIT_PER_USER} 次/人/日）。請明日再試或請管理者調整上限。")
            st.stop()
    if kind == "text":
        if u["text_calls"] >= DAILY_TEXT_LIMIT_PER_USER:
            st.error(f"今日 AI 文字處理已達上限（{DAILY_TEXT_LIMIT_PER_USER} 次/人/日）。請明日再試或請管理者調整上限。")
            st.stop()

# ==========
# OpenAI（新版 SDK：openai>=1.x）
# ==========
def openai_client():
    if not OPENAI_API_KEY:
        st.error("❌ 系統尚未設定 OpenAI API Key。請在 Streamlit Cloud → App → Settings → Secrets 加上 OPENAI_API_KEY。")
        st.stop()
    try:
        from openai import OpenAI
        return OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        st.error(f"❌ OpenAI 套件載入失敗：{e}")
        st.stop()

def image_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def normalize_int(s: Any) -> int:
    try:
        if s is None:
            return 0
        x = str(s).strip()
        x = x.replace(",", "").replace("，", "").replace("$", "").replace("元", "").replace(" ", "")
        if x == "" or x.lower() == "nan":
            return 0
        return int(float(x))
    except:
        return 0

# ==========
# AI：讀圖→結構化 JSON（保單明細表）
# ==========
STRUCT_SCHEMA_HINT = {
  "document": {
    "insured_name": "",
    "print_date": "",
    "policy_groups": [
      {
        "policy_group_name": "",
        "insurer": "",
        "effective_date": "",
        "pay_mode": "",
        "items": [
          {
            "contract_type": "",
            "product_code": "",
            "product_name": "",
            "term": "",
            "coverage_term": "",
            "sum_insured": "",
            "premium": ""
          }
        ],
        "total_premium": ""
      }
    ]
  }
}

def classify_item_category(name: str) -> str:
    t = (name or "").strip()
    if not t:
        return "其他"
    # 台灣保單常見粗分類（簡單可用，後續你要更精準我再升級規則）
    if any(k in t for k in ["壽險", "定期壽險", "終身壽險", "重大傷病定期保險", "壽"]):
        return "壽險"
    if any(k in t for k in ["住院", "實支", "醫療", "手術", "療程", "健康保險", "醫卡", "日額"]):
        return "醫療"
    if any(k in t for k in ["傷害", "意外", "骨折", "失能", "災害"]):
        return "意外"
    if any(k in t for k in ["癌", "防癌", "惡性腫瘤"]):
        return "癌症"
    if any(k in t for k in ["重大傷病", "重傷", "重大疾病"]):
        return "重傷"
    if any(k in t for k in ["長照", "照護", "失能扶助", "失能照護"]):
        return "長照"
    if any(k in t for k in ["豁免", "免繳"]):
        return "豁免"
    return "其他"

def ai_parse_policy_image(img: Image.Image) -> Dict[str, Any]:
    enforce_limits("image")

    client = openai_client()
    img_bytes = image_to_bytes(img)

    prompt = f"""
你是一個台灣保險保單「商品明細表」解析器。請從圖片中擷取欄位並輸出「嚴格 JSON」（不要 markdown、不要註解、不要多餘文字）。

輸出 JSON 結構如下（可參考但請以圖片為準）：
{json.dumps(STRUCT_SCHEMA_HINT, ensure_ascii=False)}

規則：
1) 必填鍵：document/insured_name/print_date/policy_groups
2) policy_groups 為陣列：一個保險公司/組合一個 group
3) items 為陣列，逐列擷取：約別、商品代碼、商品名稱、年期、保障年期、保額、保費
4) premium/total_premium 若能看出請填數字字串（例如 "10129"），看不出填空字串
5) 日期可用原樣（例如 114/11/04 或 2025/11/4 都可）
6) 若欄位在圖中不存在，就填空字串，不要亂猜

現在開始輸出 JSON：
""".strip()

    try:
        # responses API：同時輸入 text + image
        resp = client.responses.create(
            model=OPENAI_MODEL_VISION,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_data": img_bytes},
                ]
            }],
            temperature=0
        )
        text = (resp.output_text or "").strip()
        # 嘗試直接 parse；若模型意外包了雜訊，做一次保守清理
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        usage_inc(USERNAME, image_inc=1)
        return data
    except Exception as e:
        st.error(f"❌ AI 讀圖失敗：{e}")
        st.stop()

# ==========
# AI：保單健檢（四段式）
# ==========
def ai_health_check(struct_json: Dict[str, Any]) -> str:
    enforce_limits("text")
    client = openai_client()

    # 抽取簡要資料給模型，避免整包太大
    doc = struct_json.get("document", {})
    insured = doc.get("insured_name", "")
    groups = doc.get("policy_groups", []) or []

    compact = {
        "insured_name": insured,
        "print_date": doc.get("print_date", ""),
        "policy_groups": []
    }

    for g in groups:
        g2 = {
            "policy_group_name": g.get("policy_group_name", ""),
            "insurer": g.get("insurer", ""),
            "effective_date": g.get("effective_date", ""),
            "pay_mode": g.get("pay_mode", ""),
            "total_premium": g.get("total_premium", ""),
            "items": []
        }
        for it in (g.get("items", []) or []):
            g2["items"].append({
                "contract_type": it.get("contract_type", ""),
                "product_code": it.get("product_code", ""),
                "product_name": it.get("product_name", ""),
                "sum_insured": it.get("sum_insured", ""),
                "premium": it.get("premium", ""),
            })
        compact["policy_groups"].append(g2)

    prompt = f"""
你是台灣保險業務的「保單健檢分析助手」。根據以下 JSON（商品明細表擷取），請輸出「給客戶看的健檢摘要」：
- 用繁體中文
- 口吻專業、可行、務實
- 不要提到你是 AI，也不要提到模型/系統字眼
- 不要做法律/稅務保證，只能建議需再確認條款
- 格式請用 Markdown，固定四大段落標題：

## 1) 重複保障
## 2) 保障不足（缺口）
## 3) 條款風險（容易誤解/理賠限制）
## 4) 可優化保費（不影響核心保障前提）

資料：
{json.dumps(compact, ensure_ascii=False)}
""".strip()

    try:
        resp = client.responses.create(
            model=OPENAI_MODEL_TEXT,
            input=[{"role": "user", "content": [{"type":"input_text","text": prompt}]}],
            temperature=0.2
        )
        usage_inc(USERNAME, text_inc=1)
        return (resp.output_text or "").strip()
    except Exception as e:
        st.error(f"❌ 健檢生成失敗：{e}")
        st.stop()

# ==========
# DB 寫入：客戶 / 保單 / 明細
# ==========
def upsert_customer(name: str, id_no: str = "", birthday: str = "", phone: str = "", email: str = "", address: str = "", notes: str = "") -> int:
    conn = db_conn()
    cur = conn.cursor()
    now = datetime.datetime.now().isoformat()

    # 若同名同證號視為同一人；沒有證號則用同名比對（可再強化）
    if id_no:
        cur.execute("SELECT id FROM customers WHERE name=? AND id_no=?", (name, id_no))
    else:
        cur.execute("SELECT id FROM customers WHERE name=?", (name,))
    row = cur.fetchone()

    if row:
        cid = int(row[0])
        cur.execute("""
            UPDATE customers SET birthday=?, phone=?, email=?, address=?, notes=?, updated_at=?
            WHERE id=?
        """, (birthday, phone, email, address, notes, now, cid))
    else:
        cur.execute("""
            INSERT INTO customers(name, id_no, birthday, phone, email, address, notes, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (name, id_no, birthday, phone, email, address, notes, now, now))
        cid = cur.lastrowid

    conn.commit()
    conn.close()
    return int(cid)

def insert_policy(customer_id: int, policy_group_name: str, insurer: str, policy_no: str, pay_mode: str,
                  effective_date: str, print_date: str, total_premium_year: int,
                  raw_json: Dict[str, Any], health_report: str, created_by: str) -> int:
    conn = db_conn()
    cur = conn.cursor()
    now = datetime.datetime.now().isoformat()

    cur.execute("""
        INSERT INTO policies(customer_id, policy_group_name, insurer, policy_no, pay_mode, effective_date, print_date,
                             total_premium_year, raw_json, health_report, created_by, created_at, updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (customer_id, policy_group_name, insurer, policy_no, pay_mode, effective_date, print_date,
          total_premium_year, json.dumps(raw_json, ensure_ascii=False), health_report, created_by, now, now))
    pid = int(cur.lastrowid)

    conn.commit()
    conn.close()
    return pid

def insert_policy_items(policy_id: int, items: List[Dict[str, Any]]):
    conn = db_conn()
    cur = conn.cursor()
    for it in items:
        product_name = (it.get("product_name") or "").strip()
        category = classify_item_category(product_name)
        cur.execute("""
            INSERT INTO policy_items(policy_id, contract_type, product_code, product_name, term, coverage_term, sum_insured, premium, category)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            policy_id,
            (it.get("contract_type") or "").strip(),
            (it.get("product_code") or "").strip(),
            product_name,
            (it.get("term") or "").strip(),
            (it.get("coverage_term") or "").strip(),
            (it.get("sum_insured") or "").strip(),
            normalize_int(it.get("premium")),
            category
        ))
    conn.commit()
    conn.close()

# ==========
# CRM 匯入（CSV/Excel）
# ==========
def parse_uploaded_table(file) -> pd.DataFrame:
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(file)
    raise ValueError("只支援 CSV 或 Excel（.xlsx/.xls）")

def import_customers_df(df: pd.DataFrame, mapping: Dict[str, str]) -> int:
    """
    mapping: 你的欄位 -> 系統欄位
    系統欄位：name,id_no,birthday,phone,email,address,notes
    """
    count = 0
    for _, r in df.iterrows():
        name = str(r.get(mapping.get("name",""), "")).strip()
        if not name or name.lower() == "nan":
            continue
        cid = upsert_customer(
            name=name,
            id_no=str(r.get(mapping.get("id_no",""), "")).strip(),
            birthday=str(r.get(mapping.get("birthday",""), "")).strip(),
            phone=str(r.get(mapping.get("phone",""), "")).strip(),
            email=str(r.get(mapping.get("email",""), "")).strip(),
            address=str(r.get(mapping.get("address",""), "")).strip(),
            notes=str(r.get(mapping.get("notes",""), "")).strip(),
        )
        count += 1
    return count

# ==========
# UI
# ==========
with st.sidebar:
    st.markdown(f"### 👤 {USERNAME}")
    st.markdown(f"<span class='badge {'ok' if ROLE=='admin' else 'warn'}'>{'管理者' if ROLE=='admin' else '使用者'}</span>", unsafe_allow_html=True)

    u = usage_get_or_create(USERNAME)
    st.markdown("#### 📊 今日用量")
    st.write(f"AI 讀圖：{u['image_calls']} / {DAILY_IMAGE_LIMIT_PER_USER}")
    st.write(f"文字健檢：{u['text_calls']} / {DAILY_TEXT_LIMIT_PER_USER}")

    if st.button("登出", use_container_width=True):
        for k in ["authed","username","role"]:
            st.session_state.pop(k, None)
        st.rerun()

st.markdown(f"<div class='title-row'><h2 style='margin:0'>🛡️ {APP_TITLE}</h2></div>", unsafe_allow_html=True)
st.markdown("<div class='muted'>讀圖 → 結構化 → 入庫 → 健檢報告 → 匯入/匯出 → 權限控管（可商用上線）</div>", unsafe_allow_html=True)
st.divider()

tabs = st.tabs([
    "➕ 新增保單（AI 讀圖）",
    "🔎 客戶/保單管理",
    "📄 報表（客戶總覽）",
    "📥 匯入（既有CRM）",
    "📤 匯出（備份/交接）",
    "⚙️ 管理（上限/維運）"
])

# -------------------------
# Tab 1：新增保單（AI）
# -------------------------
with tabs[0]:
    st.markdown("### ➕ 新增保單（AI 讀圖）")
    left, right = st.columns([1, 1])

    with left:
        uploaded = st.file_uploader("上傳保單圖片（JPG/PNG）", type=["jpg","jpeg","png"])
        st.markdown("<div class='muted'>建議：正面、不要歪斜、避免反光、字要清楚。</div>", unsafe_allow_html=True)

        customer_name = st.text_input("客戶姓名（若圖片有被保險人姓名，也可留空讓系統帶入）", value="")
        customer_idno = st.text_input("身分證字號（選填）", value="")
        customer_phone = st.text_input("電話（選填）", value="")
        customer_address = st.text_input("地址（選填）", value="")
        customer_notes = st.text_area("客戶備註（選填）", value="", height=80)

        do_health = st.checkbox("同時產生「保單健檢摘要」", value=True)

        run_btn = st.button("🤖 AI 讀圖並入庫", type="primary", use_container_width=True, disabled=(uploaded is None))

    with right:
        st.markdown("### 📌 處理結果")
        if uploaded is None:
            st.info("請先上傳圖片。")
        else:
            img = Image.open(uploaded).convert("RGB")
            st.image(img, caption="上傳圖片預覽", use_container_width=True)

        if run_btn and uploaded is not None:
            with st.spinner("AI 正在讀取圖片並結構化…"):
                struct = ai_parse_policy_image(img)

            # 抽出被保險人
            doc = struct.get("document", {})
            insured_name = (doc.get("insured_name") or "").strip()
            print_date = (doc.get("print_date") or "").strip()
            policy_groups = doc.get("policy_groups", []) or []

            # 客戶姓名：以手填優先，否則用 AI
            final_name = (customer_name or "").strip() or insured_name
            if not final_name:
                st.error("❌ 無法取得客戶姓名。請在左側輸入「客戶姓名」再試一次。")
                st.stop()

            # 建立/更新客戶
            cid = upsert_customer(
                name=final_name,
                id_no=customer_idno.strip(),
                birthday="",
                phone=customer_phone.strip(),
                email="",
                address=customer_address.strip(),
                notes=customer_notes.strip()
            )

            # 產生健檢（可選）
            report_md = ""
            if do_health:
                with st.spinner("生成保單健檢摘要…"):
                    report_md = ai_health_check(struct)

            # 入庫：每個 group 一張保單
            inserted_policy_ids = []
            for g in policy_groups:
                group_name = (g.get("policy_group_name") or "").strip()
                insurer = (g.get("insurer") or "").strip()
                effective_date = (g.get("effective_date") or "").strip()
                pay_mode = (g.get("pay_mode") or "").strip()
                policy_no = ""  # 商品明細表通常不一定有保單號碼，保留空字串
                total_premium = normalize_int(g.get("total_premium"))
                items = g.get("items", []) or []

                pid = insert_policy(
                    customer_id=cid,
                    policy_group_name=group_name,
                    insurer=insurer,
                    policy_no=policy_no,
                    pay_mode=pay_mode,
                    effective_date=effective_date,
                    print_date=print_date,
                    total_premium_year=total_premium,
                    raw_json=struct,
                    health_report=report_md,
                    created_by=USERNAME
                )
                insert_policy_items(pid, items)
                inserted_policy_ids.append(pid)

            st.success(f"✅ 入庫完成：客戶「{final_name}」新增/更新成功，建立保單 {len(inserted_policy_ids)} 筆。")

            st.markdown("#### 🔎 AI 結構化結果（可檢查）")
            st.json(struct)

            if report_md:
                st.markdown("#### 🧾 保單健檢摘要（給客戶看）")
                st.markdown(report_md)

# -------------------------
# Tab 2：管理
# -------------------------
with tabs[1]:
    st.markdown("### 🔎 客戶/保單管理")

    conn = db_conn()
    customers = pd.read_sql_query("SELECT * FROM customers ORDER BY updated_at DESC", conn)
    conn.close()

    if customers.empty:
        st.info("尚無客戶資料。請先到「新增保單（AI 讀圖）」或「匯入（既有CRM）」建立資料。")
    else:
        colA, colB = st.columns([2, 1])
        with colA:
            q = st.text_input("搜尋（姓名/身分證/電話）", value="")
        with colB:
            st.write("")
            st.write("")
            if st.button("重整", use_container_width=True):
                st.rerun()

        df = customers.copy()
        if q.strip():
            qq = q.strip()
            mask = (
                df["name"].astype(str).str.contains(qq, na=False) |
                df["id_no"].astype(str).str.contains(qq, na=False) |
                df["phone"].astype(str).str.contains(qq, na=False)
            )
            df = df[mask].copy()

        sel = st.selectbox("選擇客戶", df["name"].tolist(), index=0 if len(df) else None)
        row = df[df["name"] == sel].head(1).to_dict("records")[0]
        cid = int(row["id"])

        st.markdown("#### 👤 客戶資料")
        c1, c2, c3 = st.columns(3)
        with c1:
            new_name = st.text_input("姓名", value=row.get("name",""))
            new_idno = st.text_input("身分證字號", value=row.get("id_no","") or "")
        with c2:
            new_phone = st.text_input("電話", value=row.get("phone","") or "")
            new_email = st.text_input("Email", value=row.get("email","") or "")
        with c3:
            new_addr = st.text_input("地址", value=row.get("address","") or "")
        new_notes = st.text_area("備註", value=row.get("notes","") or "", height=80)

        if st.button("💾 更新客戶資料", type="primary"):
            conn = db_conn()
            cur = conn.cursor()
            now = datetime.datetime.now().isoformat()
            cur.execute("""
                UPDATE customers SET name=?, id_no=?, phone=?, email=?, address=?, notes=?, updated_at=?
                WHERE id=?
            """, (new_name.strip(), new_idno.strip(), new_phone.strip(), new_email.strip(), new_addr.strip(), new_notes.strip(), now, cid))
            conn.commit()
            conn.close()
            st.success("✅ 已更新")
            time.sleep(0.6)
            st.rerun()

        st.divider()
        st.markdown("#### 📑 保單列表")

        conn = db_conn()
        policies = pd.read_sql_query("""
            SELECT p.*, c.name as customer_name
            FROM policies p
            JOIN customers c ON c.id = p.customer_id
            WHERE p.customer_id = ?
            ORDER BY p.updated_at DESC
        """, conn, params=(cid,))
        conn.close()

        if policies.empty:
            st.info("此客戶尚無保單。")
        else:
            show_cols = ["id","insurer","policy_group_name","effective_date","pay_mode","total_premium_year","created_by","updated_at"]
            st.dataframe(policies[show_cols], use_container_width=True)

            pid = st.selectbox("選擇要檢視的保單（依 id）", policies["id"].tolist(), index=0)

            conn = db_conn()
            items = pd.read_sql_query("""
                SELECT contract_type, product_code, product_name, term, coverage_term, sum_insured, premium, category
                FROM policy_items WHERE policy_id=? ORDER BY id ASC
            """, conn, params=(pid,))
            p = pd.read_sql_query("SELECT * FROM policies WHERE id=?", conn, params=(pid,))
            conn.close()

            st.markdown("##### 📌 明細")
            st.dataframe(items, use_container_width=True)

            st.markdown("##### 🧾 健檢摘要")
            rep = (p["health_report"].iloc[0] or "").strip()
            if rep:
                st.markdown(rep)
            else:
                st.info("此保單尚未產生健檢摘要。你可以在下方按鈕補產生。")

            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("✨ 補產生健檢摘要", type="primary"):
                    raw_json = json.loads(p["raw_json"].iloc[0] or "{}")
                    with st.spinner("生成中…"):
                        rep2 = ai_health_check(raw_json)
                    conn = db_conn()
                    cur = conn.cursor()
                    now = datetime.datetime.now().isoformat()
                    cur.execute("UPDATE policies SET health_report=?, updated_at=? WHERE id=?", (rep2, now, pid))
                    conn.commit()
                    conn.close()
                    st.success("✅ 已更新健檢摘要")
                    time.sleep(0.6)
                    st.rerun()

            with c2:
                if st.button("🗑️ 刪除這張保單（含明細）", type="secondary"):
                    conn = db_conn()
                    cur = conn.cursor()
                    cur.execute("DELETE FROM policies WHERE id=?", (pid,))
                    conn.commit()
                    conn.close()
                    st.success("✅ 已刪除")
                    time.sleep(0.6)
                    st.rerun()

        if ROLE == "admin":
            st.divider()
            st.markdown("#### ⚠️ 管理者：刪除客戶（含全部保單）")
            if st.button("🗑️ 刪除此客戶（不可復原）", type="secondary"):
                conn = db_conn()
                cur = conn.cursor()
                cur.execute("DELETE FROM customers WHERE id=?", (cid,))
                conn.commit()
                conn.close()
                st.success("✅ 已刪除客戶與所有資料")
                time.sleep(0.6)
                st.rerun()

# -------------------------
# Tab 3：報表
# -------------------------
with tabs[2]:
    st.markdown("### 📄 報表（客戶總覽）")
    conn = db_conn()
    df = pd.read_sql_query("""
        SELECT
            c.id as customer_id,
            c.name as 客戶姓名,
            c.phone as 電話,
            c.id_no as 身分證字號,
            COUNT(DISTINCT p.id) as 保單數,
            COALESCE(SUM(p.total_premium_year), 0) as 年繳保費合計,
            MAX(p.updated_at) as 最近更新
        FROM customers c
        LEFT JOIN policies p ON p.customer_id = c.id
        GROUP BY c.id
        ORDER BY 最近更新 DESC
    """, conn)
    conn.close()

    if df.empty:
        st.info("尚無資料。")
    else:
        st.dataframe(df, use_container_width=True)

        st.markdown("#### 📌 粗分類統計（壽險/醫療/意外/癌症/重傷/長照/豁免）")
        conn = db_conn()
        cat = pd.read_sql_query("""
            SELECT
              c.name as 客戶姓名,
              pi.category as 類別,
              COUNT(*) as 件數,
              COALESCE(SUM(pi.premium), 0) as 保費合計
            FROM policy_items pi
            JOIN policies p ON p.id = pi.policy_id
            JOIN customers c ON c.id = p.customer_id
            GROUP BY c.name, pi.category
            ORDER BY c.name ASC
        """, conn)
        conn.close()

        if cat.empty:
            st.info("尚無明細資料。")
        else:
            st.dataframe(cat, use_container_width=True)

# -------------------------
# Tab 4：匯入（既有CRM）
# -------------------------
with tabs[3]:
    st.markdown("### 📥 匯入（既有CRM 客戶名單）")
    st.markdown("<div class='muted'>你可以把既有系統匯出成 CSV/Excel，再丟到這裡「一次灌進來」。</div>", unsafe_allow_html=True)

    file = st.file_uploader("上傳 CSV / Excel", type=["csv","xlsx","xls"])
    if file is None:
        st.info("請先上傳檔案。")
    else:
        try:
            df = parse_uploaded_table(file)
        except Exception as e:
            st.error(f"讀檔失敗：{e}")
            st.stop()

        st.markdown("#### 1) 檢查欄位")
        st.dataframe(df.head(20), use_container_width=True)

        cols = list(df.columns)
        st.markdown("#### 2) 做欄位對應（你原本的欄位 → 系統欄位）")
        m1, m2, m3 = st.columns(3)
        with m1:
            col_name = st.selectbox("姓名（必填）", options=[""]+cols, index=0)
            col_id = st.selectbox("身分證字號", options=[""]+cols, index=0)
            col_phone = st.selectbox("電話", options=[""]+cols, index=0)
        with m2:
            col_bday = st.selectbox("生日", options=[""]+cols, index=0)
            col_email = st.selectbox("Email", options=[""]+cols, index=0)
            col_addr = st.selectbox("地址", options=[""]+cols, index=0)
        with m3:
            col_notes = st.selectbox("備註", options=[""]+cols, index=0)

        if st.button("🚀 開始匯入", type="primary"):
            if not col_name:
                st.error("姓名必須對應一個欄位")
                st.stop()

            mapping = {
                "name": col_name,
                "id_no": col_id,
                "birthday": col_bday,
                "phone": col_phone,
                "email": col_email,
                "address": col_addr,
                "notes": col_notes
            }
            with st.spinner("匯入中…"):
                n = import_customers_df(df, mapping)
            st.success(f"✅ 匯入完成：新增/更新 {n} 筆客戶")

# -------------------------
# Tab 5：匯出
# -------------------------
with tabs[4]:
    st.markdown("### 📤 匯出（備份/交接）")
    st.markdown("<div class='muted'>建議每週匯出一次，保留本機備份。</div>", unsafe_allow_html=True)

    conn = db_conn()
    customers = pd.read_sql_query("SELECT * FROM customers", conn)
    policies = pd.read_sql_query("SELECT * FROM policies", conn)
    items = pd.read_sql_query("SELECT * FROM policy_items", conn)
    conn.close()

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button(
            "⬇️ 下載 customers.csv",
            customers.to_csv(index=False).encode("utf-8-sig"),
            file_name="customers.csv",
            mime="text/csv",
            use_container_width=True
        )
    with c2:
        st.download_button(
            "⬇️ 下載 policies.csv",
            policies.to_csv(index=False).encode("utf-8-sig"),
            file_name="policies.csv",
            mime="text/csv",
            use_container_width=True
        )
    with c3:
        st.download_button(
            "⬇️ 下載 policy_items.csv",
            items.to_csv(index=False).encode("utf-8-sig"),
            file_name="policy_items.csv",
            mime="text/csv",
            use_container_width=True
        )

    st.divider()
    st.markdown("#### ✅ 一鍵打包（Excel）")
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        customers.to_excel(writer, sheet_name="customers", index=False)
        policies.to_excel(writer, sheet_name="policies", index=False)
        items.to_excel(writer, sheet_name="policy_items", index=False)
    st.download_button(
        "⬇️ 下載 insurance_backup.xlsx",
        out.getvalue(),
        file_name="insurance_backup.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

# -------------------------
# Tab 6：管理
# -------------------------
with tabs[5]:
    st.markdown("### ⚙️ 管理（上限/維運）")

    st.markdown("#### ✅ 現在系統已具備：")
    st.write("1) 讀圖 → 結構化 JSON → 入庫（SQLite）")
    st.write("2) 自動分類（壽險/醫療/意外/癌症/重傷/長照/豁免/其他）")
    st.write("3) 保單健檢摘要（四段式，可對客戶直接講）")
    st.write("4) 匯入既有 CRM 客戶名單（CSV/Excel）")
    st.write("5) 匯出備份（CSV / Excel）")
    st.write("6) 權限控管（使用者/管理者）＋每日用量上限")

    st.divider()
    st.markdown("#### 🔐 Secrets 建議（Streamlit Cloud）")
    st.code(
        """OPENAI_API_KEY = "sk-...你的key..."
ADMIN_PASSWORD = "管理者密碼（建議強密碼）"
USER_PASSWORDS_JSON = "[\\"同仁密碼1\\", \\"同仁密碼2\\"]"
DAILY_IMAGE_LIMIT_PER_USER = "30"
DAILY_TEXT_LIMIT_PER_USER = "80"
OPENAI_MODEL_VISION = "gpt-4.1-mini"
OPENAI_MODEL_TEXT = "gpt-4.1-mini"
""",
        language="toml"
    )

    st.divider()
    if ROLE != "admin":
        st.info("此頁面管理功能需要管理者權限。")
    else:
        st.markdown("#### 🧹 管理者：資料庫維護")
        if st.button("清空今日用量（所有使用者）", type="secondary"):
            conn = db_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM usage_daily WHERE ymd=?", (get_ymd(),))
            conn.commit()
            conn.close()
            st.success("✅ 已清空今日用量")
            time.sleep(0.6)
            st.rerun()

        st.warning("⚠️ 下方為高風險操作（不可復原）")
        if st.button("⚠️ 清空全部資料（客戶/保單/明細）", type="secondary"):
            conn = db_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM policy_items")
            cur.execute("DELETE FROM policies")
            cur.execute("DELETE FROM customers")
            conn.commit()
            conn.close()
            st.success("✅ 已清空全部資料")
            time.sleep(0.6)
            st.rerun()
