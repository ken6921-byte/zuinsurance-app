import streamlit as st
import pandas as pd
import os
import json
import hashlib
from datetime import datetime

# --- 設定 ---
DATA_FILE = "insurance_data.csv"
USER_FILE = "users.json"

st.set_page_config(page_title="保戶資料管理系統", layout="wide")

# --- 工具函式：密碼加密與檔案處理 ---

def make_hashes(password):
    """將密碼轉成亂碼 (Hash)，增加安全性"""
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    """檢查輸入的密碼是否正確"""
    if make_hashes(password) == hashed_text:
        return True
    return False

def load_users():
    """讀取使用者帳號檔案"""
    if not os.path.exists(USER_FILE):
        return {}
    with open(USER_FILE, 'r') as f:
        return json.load(f)

def save_user(username, password):
    """儲存新使用者"""
    users = load_users()
    users[username] = make_hashes(password)
    with open(USER_FILE, 'w') as f:
        json.dump(users, f)

def load_data():
    """讀取保單資料"""
    if os.path.exists(DATA_FILE):
        return pd.read_csv(DATA_FILE)
    else:
        columns = [
            "業務員", "保戶姓名", "投保明細 (險種/保額)", 
            "理賠紀錄/檢表", "繳費日期", "繳費金額", "檔案名稱"
        ]
        return pd.DataFrame(columns=columns)

def save_data(df):
    """儲存保單資料"""
    df.to_csv(DATA_FILE, index=False)

# --- 程式核心邏輯 ---

# 初始化 Session State (紀錄登入狀態)
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = ""

# --- 畫面 1: 登入/註冊頁面 (如果還沒登入) ---
if not st.session_state.logged_in:
    st.title("🔐 保險業務系統 - 登入")
    
    tab1, tab2 = st.tabs(["登入", "註冊新帳號"])

    with tab1:
        username = st.text_input("帳號 (使用者名稱)")
        password = st.text_input("密碼", type='password')
        if st.button("登入"):
            users = load_users()
            if username in users and check_hashes(password, users[username]):
                st.session_state.logged_in = True
                st.session_state.username = username
                st.success("登入成功！")
                st.rerun() # 重新整理畫面進入系統
            else:
                st.error("帳號或密碼錯誤")

    with tab2:
        new_user = st.text_input("設定新帳號")
        new_password = st.text_input("設定新密碼", type='password')
        if st.button("建立帳號"):
            users = load_users()
            if new_user in users:
                st.warning("這個帳號已經有人使用了")
            elif new_user and new_password:
                save_user(new_user, new_password)
                st.success("帳號建立成功！請切換到「登入」分頁進行登入。")
            else:
                st.error("請輸入帳號和密碼")

# --- 畫面 2: 主系統 (登入後才看得到) ---
else:
    current_user = st.session_state.username
    st.sidebar.write(f"👋 你好，**{current_user}**")
    if st.sidebar.button("登出"):
        st.session_state.logged_in = False
        st.rerun()
    
    st.title(f"📋 保戶資料管理 - {current_user} 專區")

    # 讀取資料
    if 'df' not in st.session_state:
        st.session_state.df = load_data()
    else:
        # 每次操作前重新讀取確保資料最新
        st.session_state.df = load_data()

    # --- 新增資料區 (自動帶入業務員名字) ---
    st.sidebar.header("📝 新增保戶資料")
    with st.sidebar.form("add_client_form"):
        # 這裡鎖定業務員欄位，不讓使用者修改，確保資料正確
        st.text_input("業務員", value=current_user, disabled=True)
        
        client_name = st.text_input("保戶姓名")
        policy_details = st.text_area("投保明細", height=100)
        claims_history = st.text_area("理賠紀錄/檢表", height=100)
        
        col1, col2 = st.columns(2)
        pay_date = st.date_input("繳費時間", datetime.today())
        pay_amount = st.number_input("繳費金額", min_value=0, step=1000)
        
        uploaded_file = st.file_uploader("上傳保單資料", type=['png', 'jpg', 'pdf'])
        
        submit_button = st.form_submit_button("新增資料")

        if submit_button:
            if client_name:
                file_name_record = uploaded_file.name if uploaded_file else "無檔案"
                
                new_data = {
                    "業務員": current_user, # 強制使用登入者的名字
                    "保戶姓名": client_name,
                    "投保明細 (險種/保額)": policy_details,
                    "理賠紀錄/檢表": claims_history,
                    "繳費日期": pay_date,
                    "繳費金額": pay_amount,
                    "檔案名稱": file_name_record
                }
                
                # 儲存
                updated_df = pd.concat([st.session_state.df, pd.DataFrame([new_data])], ignore_index=True)
                save_data(updated_df)
                st.success(f"已新增 {client_name} 的資料！")
                st.rerun() # 重新整理以顯示新資料
            else:
                st.error("請輸入保戶姓名")

    # --- 資料顯示區 (只顯示該業務員的資料) ---
    st.header("🔍 我的客戶列表")

    # 特殊權限：如果是 'admin' 帳號，可以看到全部，否則只能看自己的
    if current_user == 'admin':
        st.info("管理員模式：顯示所有業務員資料")
        my_data = st.session_state.df
    else:
        my_data = st.session_state.df[st.session_state.df["業務員"] == current_user]

    if not my_data.empty:
        st.dataframe(
            my_data, 
            use_container_width=True,
            column_config={
                "繳費金額": st.column_config.NumberColumn(format="$%d"),
                "繳費日期": st.column_config.DateColumn(format="YYYY-MM-DD"),
            }
        )
        total = my_data["繳費金額"].sum()
        st.metric("我的業績總額", f"${total:,.0f}")
    else:
        st.info("目前還沒有資料，請從左側新增。")
