import streamlit as st
import pandas as pd
import os
import json
import hashlib
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# --- 設定 ---
DATA_FILE = "insurance_data.csv"
USER_FILE = "users.json"

# ⚠️ 設定 Google Gemini API Key
# 注意：在正式專案中，建議將 Key 放在 Streamlit Secrets 以策安全，但在這裡我們先直接使用方便測試。
GOOGLE_API_KEY = "AIzaSyAaMQ1VHpt88C5PfB_EsF_WUa6pxZiyIXI"
genai.configure(api_key=GOOGLE_API_KEY)

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

# --- AI 辨識函式 ---
def analyze_image_with_gemini(image):
    """傳送圖片給 Gemini 進行分析"""
    model = genai.GenerativeModel('gemini-1.5-flash') # 使用輕量快速的模型
    
    prompt = """
    請扮演專業的保險助理。請分析這張保單圖片，並擷取以下資訊，輸出成 JSON 格式：
    1. "client_name": 保戶姓名 (若找不到請回傳空字串)
    2. "policy_details": 保險公司名稱與險種名稱 (例如：國泰人壽 - 真安順終身保險)
    3. "pay_amount": 繳費金額 (請只回傳純數字，去除逗號或幣別符號，若找不到回傳 0)
    
    請確保回傳的格式是可以直接被 Python json.loads 解析的純 JSON 字串，不要加 markdown 標記。
    """
    
    with st.spinner('🤖 AI 正在努力辨識保單內容中...請稍候'):
        try:
            response = model.generate_content([prompt, image])
            text = response.text
            # 清理可能的回傳格式 (有時候 AI 會加 ```json ...)
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            st.error(f"AI 辨識失敗: {e}")
            return None

# --- 程式核心邏輯 ---

# 初始化 Session State
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = ""

# 初始化表單自動填寫的變數
if 'form_client_name' not in st.session_state:
    st.session_state.form_client_name = ""
if 'form_policy_details' not in st.session_state:
    st.session_state.form_policy_details = ""
if 'form_pay_amount' not in st.session_state:
    st.session_state.form_pay_amount = 0

# --- 畫面 1: 登入/註冊頁面 ---
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
                st.rerun()
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

# --- 畫面 2: 主系統 ---
else:
    current_user = st.session_state.username
    st.sidebar.write(f"👋 你好，**{current_user}**")
    if st.sidebar.button("登出"):
        st.session_state.logged_in = False
        st.rerun()
    
    st.title(f"📋 保戶資料管理 - {current_user} 專區")

    if 'df' not in st.session_state:
        st.session_state.df = load_data()
    else:
        st.session_state.df = load_data()

    # --- 側邊欄：AI 辨識區 (移到表單外面) ---
    st.sidebar.header("📸 步驟 1: 上傳與辨識 (選填)")
    uploaded_file = st.sidebar.file_uploader("上傳保單照片", type=['png', 'jpg', 'jpeg'])
    
    if uploaded_file is not None:
        # 顯示圖片預覽
        image = Image.open(uploaded_file)
        st.sidebar.image(image, caption='已上傳的圖片', use_container_width=True)
        
        if st.sidebar.button("✨ AI 自動辨識內容"):
            ai_result = analyze_image_with_gemini(image)
            if ai_result:
                # 將 AI 辨識結果存入 Session State，讓下方的表單讀取
                st.session_state.form_client_name = ai_result.get("client_name", "")
                st.session_state.form_policy_details = ai_result.get("policy_details", "")
                st.session_state.form_pay_amount = int(ai_result.get("pay_amount", 0))
                st.sidebar.success("辨識完成！資料已自動帶入下方表單。")
                # 這裡不使用 rerun，直接讓使用者往下看表單

    # --- 側邊欄：資料填寫區 ---
    st.sidebar.markdown("---")
    st.sidebar.header("📝 步驟 2: 確認與新增資料")
    
    with st.sidebar.form("add_client_form"):
        st.text_input("業務員", value=current_user, disabled=True)
        
        # 這裡的 value 會讀取 AI 辨識後的結果 (如果有的話)
        client_name = st.text_input("保戶姓名", value=st.session_state.form_client_name)
        policy_details = st.text_area("投保明細", height=100, value=st.session_state.form_policy_details)
        claims_history = st.text_area("理賠紀錄/檢表", height=100)
        
        col1, col2 = st.columns(2)
        pay_date = st.date_input("繳費時間", datetime.today())
        pay_amount = st.number_input("繳費金額", min_value=0, step=1000, value=st.session_state.form_pay_amount)
        
        # 這裡只負責記錄檔名
        file_name_record = uploaded_file.name if uploaded_file else "無檔案"
        
        submit_button = st.form_submit_button("新增資料")

        if submit_button:
            if client_name:
                new_data = {
                    "業務員": current_user,
                    "保戶姓名": client_name,
                    "投保明細 (險種/保額)": policy_details,
                    "理賠紀錄/檢表": claims_history,
                    "繳費日期": pay_date,
                    "繳費金額": pay_amount,
                    "檔案名稱": file_name_record
                }
                
                updated_df = pd.concat([st.session_state.df, pd.DataFrame([new_data])], ignore_index=True)
                save_data(updated_df)
                
                # 新增成功後，清空暫存資料
                st.session_state.form_client_name = ""
                st.session_state.form_policy_details = ""
                st.session_state.form_pay_amount = 0
                
                st.success(f"已新增 {client_name} 的資料！")
                st.rerun()
            else:
                st.error("請至少輸入保戶姓名")

    # --- 資料顯示區 ---
    st.header("🔍 我的客戶列表")

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
